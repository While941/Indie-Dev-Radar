"""Hacker News collector with multi-list paths, freshness, and topic gate."""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import httpx

from config import HackerNewsSourceConfig
from models.item import IntelligenceItem

from .base import Collector, get_json, parse_epoch
from .multi_path import collect_paths

log = logging.getLogger(__name__)

BASE_URL = "https://hacker-news.firebaseio.com/v0"
ITEM_URL = "https://news.ycombinator.com/item?id={item_id}"
DEFAULT_WORKERS = 8

DEFAULT_TOPIC_KEYWORDS = (
    "game", "gamedev", "godot", "unity", "unreal", "indie", "roguelike",
    "pixel", "sprite", "shader", "gameplay", "steam", "itch.io", "gdscript",
    "game engine", "level design", "procedural",
)


class HackerNewsCollector(Collector):
    def __init__(
        self,
        cfg: HackerNewsSourceConfig,
        client: httpx.Client,
        *,
        base_url: str = BASE_URL,
        max_workers: int = DEFAULT_WORKERS,
        now: datetime | None = None,
    ) -> None:
        self._cfg = cfg
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._max_workers = max(1, max_workers)
        self._now = now or datetime.now(timezone.utc)

    def collect(self) -> list[IntelligenceItem]:
        fetchers = {
            f"hn_{list_name}": (lambda n=list_name: self._collect_list(n))
            for list_name in self._cfg.lists
        }
        items = collect_paths(
            fetchers,
            now=self._now,
            max_age_days=self._cfg.max_age_days,
            freshness_horizon_days=self._cfg.freshness_horizon_days,
            log_label="HackerNews",
        )
        if not self._cfg.require_topic_match:
            return items
        before = len(items)
        kept = [i for i in items if self._topic_match(i)]
        dropped = before - len(kept)
        if dropped:
            log.info("HN topic gate: dropped %d/%d off-topic", dropped, before)
        return kept

    def _collect_list(self, list_name: str) -> list[IntelligenceItem]:
        ids = get_json(self._client, f"{self._base_url}/{list_name}.json")
        if not isinstance(ids, list):
            log.warning("HN list %s returned non-list, skipped", list_name)
            return []
        return self._fetch_items_parallel(ids[: self._cfg.top_n])

    def _topic_keywords(self) -> tuple[str, ...]:
        if self._cfg.topic_keywords:
            return self._cfg.topic_keywords
        return DEFAULT_TOPIC_KEYWORDS

    def _topic_match(self, item: IntelligenceItem) -> bool:
        text = f"{item.title} {item.summary_raw}".lower()
        for kw in self._topic_keywords():
            k = (kw or "").strip().lower()
            if not k:
                continue
            if " " in k or "." in k:
                if k in text:
                    return True
            elif re.search(rf"\b{re.escape(k)}\b", text):
                return True
        return False

    def _fetch_items_parallel(self, item_ids: list) -> list[IntelligenceItem]:
        if not item_ids:
            return []
        by_id: dict[Any, IntelligenceItem] = {}
        workers = min(self._max_workers, len(item_ids))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._fetch_item, item_id): item_id
                for item_id in item_ids
            }
            for fut in as_completed(futures):
                item_id = futures[fut]
                try:
                    item = fut.result()
                except Exception as exc:  # noqa: BLE001
                    log.warning("HN item %s fetch failed, skipped: %s", item_id, exc)
                    continue
                if item is not None:
                    by_id[item_id] = item
        return [by_id[i] for i in item_ids if i in by_id]

    def _fetch_item(self, item_id: int) -> IntelligenceItem | None:
        data = get_json(self._client, f"{self._base_url}/item/{item_id}.json")
        if not isinstance(data, dict):
            return None
        if data.get("type") != "story":
            return None

        external_url = data.get("url")
        source_url = external_url or ITEM_URL.format(item_id=item_id)
        title = (data.get("title") or "").strip() or source_url
        summary = (data.get("text") or "").strip()

        return IntelligenceItem(
            source="HackerNews",
            source_url=source_url,
            title=title,
            summary_raw=summary,
            author=data.get("by"),
            published_at=parse_epoch(data.get("time")),
            fetched_at=self._now,
            score_raw={
                "score": data.get("score"),
                "comments": data.get("descendants"),
            },
        )
