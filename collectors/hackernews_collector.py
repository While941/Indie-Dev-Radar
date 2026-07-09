"""Hacker News collector (official Firebase API).

    GET https://hacker-news.firebaseio.com/v0/{topstories|showstories|...}.json
        -> [id, id, ...]
    GET https://hacker-news.firebaseio.com/v0/item/{id}.json
        -> {id, type, by, time, title, url, score, descendants, ...}

No auth required. Includes Ask HN (no external url) using the HN item link.
Item fetches for a list are parallelised with a small thread pool.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import httpx

from config import HackerNewsSourceConfig
from models.item import IntelligenceItem

from .base import Collector, get_json, parse_epoch

log = logging.getLogger(__name__)

BASE_URL = "https://hacker-news.firebaseio.com/v0"
ITEM_URL = "https://news.ycombinator.com/item?id={item_id}"
DEFAULT_WORKERS = 8


class HackerNewsCollector(Collector):
    def __init__(
        self,
        cfg: HackerNewsSourceConfig,
        client: httpx.Client,
        *,
        base_url: str = BASE_URL,
        max_workers: int = DEFAULT_WORKERS,
    ) -> None:
        self._cfg = cfg
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._max_workers = max(1, max_workers)

    def collect(self) -> list[IntelligenceItem]:
        now = datetime.now(timezone.utc)
        items: list[IntelligenceItem] = []
        for list_name in self._cfg.lists:
            ids = get_json(self._client, f"{self._base_url}/{list_name}.json")
            if not isinstance(ids, list):
                log.warning("HN list %s returned non-list, skipped", list_name)
                continue
            batch_ids = ids[: self._cfg.top_n]
            items.extend(self._fetch_items_parallel(batch_ids, now))
        log.info("HackerNews: collected %d items", len(items))
        return items

    def _fetch_items_parallel(
        self, item_ids: list, fetched_at: datetime
    ) -> list[IntelligenceItem]:
        if not item_ids:
            return []
        # Preserve list order for stable output (as_completed is nondeterministic).
        by_id: dict[Any, IntelligenceItem] = {}
        workers = min(self._max_workers, len(item_ids))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._fetch_item, item_id, fetched_at): item_id
                for item_id in item_ids
            }
            for fut in as_completed(futures):
                item_id = futures[fut]
                try:
                    item = fut.result()
                except Exception as exc:  # noqa: BLE001 - skip one bad item
                    log.warning("HN item %s fetch failed, skipped: %s", item_id, exc)
                    continue
                if item is not None:
                    by_id[item_id] = item
        return [by_id[i] for i in item_ids if i in by_id]

    def _fetch_item(self, item_id: int, fetched_at: datetime) -> IntelligenceItem | None:
        data = get_json(self._client, f"{self._base_url}/item/{item_id}.json")
        if not isinstance(data, dict):
            return None
        if data.get("type") != "story":
            return None

        external_url = data.get("url")
        source_url = external_url or ITEM_URL.format(item_id=item_id)
        title = (data.get("title") or "").strip() or source_url
        # For Ask HN with text but no url, use the text as raw summary.
        summary = (data.get("text") or "").strip()

        return IntelligenceItem(
            source="HackerNews",
            source_url=source_url,
            title=title,
            summary_raw=summary,
            author=data.get("by"),
            published_at=parse_epoch(data.get("time")),
            fetched_at=fetched_at,
            score_raw={
                "score": data.get("score"),
                "comments": data.get("descendants"),
            },
        )
