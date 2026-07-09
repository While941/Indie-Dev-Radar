"""Pure de-duplication by ``source_url``.

Stateless: the caller supplies the set of already-seen URLs (typically pulled
from Feishu before a run). Also de-duplicates within the incoming batch so two
collectors returning the same URL do not produce duplicates.
"""
from __future__ import annotations

from collections.abc import Iterable

from models.item import IntelligenceItem


def filter_new(
    items: Iterable[IntelligenceItem],
    seen_urls: Iterable[str],
) -> list[IntelligenceItem]:
    """Return items whose ``source_url`` is not already seen.

    Preserves order and keeps the first occurrence of any URL within the batch.
    """
    seen: set[str] = set(seen_urls)
    result: list[IntelligenceItem] = []
    for item in items:
        if not item.source_url:
            continue
        if item.source_url in seen:
            continue
        seen.add(item.source_url)
        result.append(item)
    return result
