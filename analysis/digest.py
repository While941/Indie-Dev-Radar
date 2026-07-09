"""Build daily / weekly digest packages from scored intelligence items.

Human publishes manually: digests are copy-ready (title + body per platform).
No auto-posting adapters.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from typing import Any

from models.digest import DigestPackage, period_label_daily, period_label_weekly
from models.item import PUBLISH_PLATFORMS, IntelligenceItem

from .ai_client import AIAuthError, ChatClient
from .prompts import DIGEST_SYSTEM, build_digest_user
from .rewriter import parse_rewrite_response

log = logging.getLogger(__name__)


def select_digest_candidates(
    items: Sequence[IntelligenceItem],
    *,
    score_threshold: float,
    max_items: int,
) -> list[IntelligenceItem]:
    """Pick scored, non-deleted items above threshold, highest score first."""
    pool = [
        i for i in items
        if i.is_scored
        and i.score is not None
        and i.score >= score_threshold
        and i.recommended_action != "删除"
        and i.source not in {"日贴", "周贴"}
    ]
    pool.sort(key=lambda x: x.score or 0.0, reverse=True)
    if max_items > 0:
        pool = pool[:max_items]
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
    """Strong-model digest generation for 日贴 / 周贴."""

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
    ) -> DigestPackage | None:
        """Generate one digest. Returns None if no candidates or empty AI result."""
        self.aborted = False
        if kind not in {"日贴", "周贴"}:
            raise ValueError(f"unknown digest kind: {kind}")

        if period_label is None:
            period_label = (
                period_label_daily(today) if kind == "日贴"
                else period_label_weekly(today)
            )

        candidates = select_digest_candidates(
            items,
            score_threshold=self._score_threshold,
            max_items=self._max_items if max_items is None else max_items,
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
