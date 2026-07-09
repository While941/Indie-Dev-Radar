"""Rewriter: strong model turns high-score items into multi-platform drafts.

Only triggered for items at or above the configured score threshold, keeping
strong-model (token-heavy) calls to a minimum. Low-score items pass through
unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from models.item import IntelligenceItem

from .ai_client import AIAuthError, ChatClient
from .prompts import REWRITE_SYSTEM, build_rewrite_user

log = logging.getLogger(__name__)

VALID_DRAFT_KEYS = frozenset({"小红书", "公众号", "B站"})


def _str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def parse_rewrite_response(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise a rewrite response into fields for ``dataclasses.replace``."""
    drafts_raw = raw.get("drafts")
    drafts: dict[str, str] = {}
    if isinstance(drafts_raw, dict):
        for key, val in drafts_raw.items():
            if key in VALID_DRAFT_KEYS and val:
                drafts[key] = str(val).strip()

    out: dict[str, Any] = {"drafts": drafts}
    title = _str(raw.get("recommended_title"))
    if title:
        out["recommended_title"] = title
    tags = raw.get("tags")
    if isinstance(tags, list) and tags:
        cleaned = tuple(str(t).strip() for t in tags if str(t).strip())
        if cleaned:
            out["tags"] = cleaned
    return out


class Rewriter:
    def __init__(self, model: str, threshold: float, client: ChatClient,
                 *, temperature: float = 0.4, timeout: float = 60.0) -> None:
        self._model = model
        self._threshold = threshold
        self._client = client
        self._temperature = temperature
        self._timeout = timeout
        self.aborted: bool = False  # True if last rewrite_all hit AIAuthError

    def should_rewrite(self, item: IntelligenceItem) -> bool:
        return item.score is not None and item.score >= self._threshold

    def rewrite(self, item: IntelligenceItem) -> IntelligenceItem:
        raw = self._client.chat_json(
            model=self._model, system=REWRITE_SYSTEM, user=build_rewrite_user(item),
            temperature=self._temperature, timeout=self._timeout,
        )
        return replace(item, **parse_rewrite_response(raw))

    def rewrite_all(self, items: list[IntelligenceItem]) -> list[IntelligenceItem]:
        self.aborted = False
        rewritten: list[IntelligenceItem] = []
        for item in items:
            if not self.should_rewrite(item) or self.aborted:
                rewritten.append(item)
                continue
            try:
                rewritten.append(self.rewrite(item))
            except AIAuthError as exc:
                log.error("AI auth failed during rewrite — aborting batch: %s", exc)
                rewritten.append(item)
                self.aborted = True
            except Exception as exc:  # noqa: BLE001 - degrade gracefully
                log.warning("Rewrite failed for %s: %s", item.source_url, exc)
                rewritten.append(item)
        log.info("Rewrote %d/%d items%s",
                 sum(1 for i in rewritten if i.drafts), len(items),
                 " (aborted)" if self.aborted else "")
        return rewritten
