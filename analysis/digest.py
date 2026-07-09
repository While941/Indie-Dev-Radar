"""Build daily / weekly digest packages from scored intelligence items.

Human publishes manually: digests are copy-ready (title + body per platform).
No auto-posting adapters.

Supports general 日贴/周贴 and independent AI日贴/AI周贴 (GitHubAI track).
"""
from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from datetime import date
from typing import Any

from models.digest import DIGEST_KINDS, DIGEST_SOURCES, DigestPackage, period_label_daily, period_label_weekly
from models.item import PUBLISH_PLATFORMS, IntelligenceItem

from .ai_client import AIAuthError, ChatClient
from .prompts import DIGEST_SYSTEM, build_digest_user
from .rewriter import parse_rewrite_response

log = logging.getLogger(__name__)

# Kinds that use ISO-week period labels.
_WEEKLY_KINDS = frozenset({"周贴", "AI周贴"})


def select_digest_candidates(
    items: Sequence[IntelligenceItem],
    *,
    score_threshold: float,
    max_items: int,
    min_fallback: int = 3,
    sources: Collection[str] | None = None,
) -> list[IntelligenceItem]:
    """Pick scored, non-deleted items for a digest.

    Prefer items at/above ``score_threshold``. If none qualify (common when the
    batch is off-topic), fall back to the top-scoring scored items so a 日贴
    is still produced instead of silent empty ``output/``.

    When ``sources`` is set, only items whose ``source`` is in that set are
    considered (used to keep GitHubAI out of general digests and vice versa).
    """
    base = [
        i for i in items
        if i.is_scored
        and i.score is not None
        and i.recommended_action != "删除"
        and i.source not in DIGEST_SOURCES
        and (sources is None or i.source in sources)
    ]
    base.sort(key=lambda x: x.score or 0.0, reverse=True)
    above = [i for i in base if (i.score or 0) >= score_threshold]
    if above:
        pool = above
    else:
        # Fallback: best available so export still happens
        pool = base
        if pool:
            log.info(
                "No items >= %.0f; falling back to top %d by score (best=%.0f)",
                score_threshold,
                min(max_items if max_items > 0 else len(pool), len(pool)),
                pool[0].score or 0,
            )
    if max_items > 0:
        pool = pool[:max_items]
    # Avoid single-item noise when using pure fallback of very low scores
    if not above and len(pool) < min_fallback and len(base) >= min_fallback:
        pool = base[:max(max_items, min_fallback) if max_items > 0 else min_fallback]
    return pool


def parse_digest_response(
    raw: dict[str, Any],
    *,
    kind: str,
    period_label: str,
    source_items: Sequence[IntelligenceItem],
) -> DigestPackage:
    """Reuse platform parser from rewriter; wrap as DigestPackage."""
    parsed = parse_rewrite_response(raw)
    posts = parsed.get("platform_posts") or {}
    # Ensure all three keys exist even if empty (export still shows slots)
    platform_posts = {
        p: dict(posts.get(p) or {"title": "", "body": ""})
        for p in PUBLISH_PLATFORMS
    }
    tags = parsed.get("tags") or ()
    if not isinstance(tags, tuple):
        tags = tuple(tags) if tags else ()
    return DigestPackage(
        kind=kind,
        period_label=period_label,
        platform_posts=platform_posts,
        source_urls=tuple(i.source_url for i in source_items),
        item_count=len(source_items),
        recommended_title=parsed.get("recommended_title"),
        tags=tags,
    )


class DigestBuilder:
    """Strong-model digest generation for 日贴 / 周贴 / AI日贴 / AI周贴."""

    def __init__(
        self,
        model: str,
        client: ChatClient,
        *,
        score_threshold: float = 70.0,
        max_items: int = 8,
        temperature: float = 0.4,
        timeout: float = 90.0,
    ) -> None:
        self._model = model
        self._client = client
        self._score_threshold = score_threshold
        self._max_items = max_items
        self._temperature = temperature
        self._timeout = timeout
        self.aborted: bool = False

    def build(
        self,
        items: Sequence[IntelligenceItem],
        *,
        kind: str,
        period_label: str | None = None,
        today: date | None = None,
        max_items: int | None = None,
        sources: Collection[str] | None = None,
    ) -> DigestPackage | None:
        """Generate one digest. Returns None if no candidates or empty AI result."""
        self.aborted = False
        if kind not in DIGEST_KINDS:
            raise ValueError(f"unknown digest kind: {kind}")

        if period_label is None:
            period_label = (
                period_label_weekly(today) if kind in _WEEKLY_KINDS
                else period_label_daily(today)
            )

        candidates = select_digest_candidates(
            items,
            score_threshold=self._score_threshold,
            max_items=self._max_items if max_items is None else max_items,
            sources=sources,
        )
        if not candidates:
            log.info("%s: no candidates above threshold %.0f", kind, self._score_threshold)
            return None

        try:
            raw = self._client.chat_json(
                model=self._model,
                system=DIGEST_SYSTEM,
                user=build_digest_user(
                    kind=kind, period=period_label, items=list(candidates),
                ),
                temperature=self._temperature,
                timeout=self._timeout,
            )
        except AIAuthError:
            self.aborted = True
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("%s generation failed: %s", kind, exc)
            return None

        package = parse_digest_response(
            raw, kind=kind, period_label=period_label, source_items=candidates,
        )
        if not any((p.get("body") or "").strip() for p in package.platform_posts.values()):
            log.warning("%s: model returned empty bodies", kind)
            return None
        log.info("%s ready: %d items → %s", kind, package.item_count, package.period_label)
        return package
