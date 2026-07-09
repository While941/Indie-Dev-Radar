"""Godot Asset Library collector with multi-path discovery.

Fetches several sort orders (updated / new / rating / …), merges by URL, applies
a hard max-age gate on modify/submit date.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from config import GodotSourceConfig
from models.item import IntelligenceItem

from .base import Collector, get_json, parse_epoch
from .multi_path import collect_paths, dedupe_strs

log = logging.getLogger(__name__)

BASE_URL = "https://godotengine.org/asset-library"
ASSET_PAGE_URL = f"{BASE_URL}/asset/{{asset_id}}"
DEFAULT_SORTS = ("updated", "new", "rating")


class GodotCollector(Collector):
    def __init__(
        self,
        cfg: GodotSourceConfig,
        client: httpx.Client,
        *,
        base_url: str = f"{BASE_URL}/api/asset",
        now: datetime | None = None,
    ) -> None:
        self._cfg = cfg
        self._client = client
        self._base_url = base_url
        self._now = now or datetime.now(timezone.utc)

    def _sorts(self) -> list[str]:
        sorts = list(self._cfg.sorts) if self._cfg.sorts else list(DEFAULT_SORTS)
        return dedupe_strs(sorts) or list(DEFAULT_SORTS)

    def _fetch_sort(self, sort: str) -> list[IntelligenceItem]:
        params = {
            "godot_version": self._cfg.godot_version,
            "sort": sort,
            "max_results": str(self._cfg.max_results),
            "page": "1",
        }
        data = get_json(self._client, self._base_url, params=params)
        assets = data.get("result", []) if isinstance(data, dict) else []
        if not isinstance(assets, list):
            return []

        items: list[IntelligenceItem] = []
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            item = self._to_item(asset, self._now)
            if item is not None:
                items.append(item)
        return items

    def collect(self) -> list[IntelligenceItem]:
        fetchers = {
            f"godot_{sort}": (lambda s=sort: self._fetch_sort(s))
            for sort in self._sorts()
        }
        return collect_paths(
            fetchers,
            now=self._now,
            max_age_days=self._cfg.max_age_days,
            freshness_horizon_days=self._cfg.freshness_horizon_days,
            log_label="Godot",
        )

    @staticmethod
    def _to_item(asset: dict, fetched_at: datetime) -> IntelligenceItem | None:
        asset_id = asset.get("asset_id")
        browse_url = asset.get("browse_url")
        if browse_url:
            source_url = browse_url
        elif asset_id:
            source_url = ASSET_PAGE_URL.format(asset_id=asset_id)
        else:
            log.warning("Godot asset without id/url, skipped: %r", asset.get("title"))
            return None

        title = (asset.get("title") or "").strip() or source_url
        description = (asset.get("description") or "").strip()
        rating = asset.get("rating") if isinstance(asset.get("rating"), dict) else {}
        score_raw = {
            "rating_score": rating.get("score"),
            "positive": rating.get("positive_ratings"),
            "negative": rating.get("negative_ratings"),
            "cost": asset.get("cost"),
            "category": asset.get("category"),
            "godot_version": asset.get("godot_version"),
        }

        return IntelligenceItem(
            source="Godot",
            source_url=source_url,
            title=title,
            summary_raw=description,
            author=asset.get("author"),
            published_at=parse_epoch(asset.get("modify_date") or asset.get("submit_date")),
            fetched_at=fetched_at,
            score_raw=score_raw,
        )
