"""Multi-path collection: merge by URL, freshness gate, attach discovery paths.

Collectors declare named fetch callables; this module owns the shared
orchestration so GitHub / Godot / HN do not copy-paste the same loop.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timezone

from models.item import IntelligenceItem
from models.signals import DiscoverySignals, age_days, build_discovery_signals

log = logging.getLogger(__name__)

PathFetcher = Callable[[], Sequence[IntelligenceItem]]


def dedupe_strs(values: Sequence[str]) -> list[str]:
    """Preserve order, drop empties and duplicates (case-folded)."""
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        key = (v or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def filter_fresh(
    items: Sequence[IntelligenceItem],
    *,
    now: datetime,
    max_age_days: int,
    drop_unknown: bool = False,
) -> list[IntelligenceItem]:
    """Drop items older than ``max_age_days``. ``max_age_days <= 0`` disables."""
    if max_age_days <= 0:
        return list(items)
    kept: list[IntelligenceItem] = []
    dropped = 0
    for it in items:
        days = age_days(it.published_at, now)
        if days is None:
            if drop_unknown:
                dropped += 1
                continue
            kept.append(it)
            continue
        if days <= float(max_age_days):
            kept.append(it)
        else:
            dropped += 1
    if dropped:
        log.info(
            "Freshness: dropped %d/%d items older than %d day(s)",
            dropped, len(items), max_age_days,
        )
    return kept


def _paths_of(item: IntelligenceItem) -> list[str]:
    if item.signals is not None:
        return list(item.signals.paths)
    return []


def merge_by_url(
    path_items: Mapping[str, Sequence[IntelligenceItem]],
) -> list[IntelligenceItem]:
    """Merge multi-path results; same URL unions discovery path labels."""
    best: dict[str, IntelligenceItem] = {}
    for path_name, items in path_items.items():
        path = (path_name or "default").strip() or "default"
        for it in items:
            url = (it.source_url or "").strip()
            if not url:
                continue
            existing = best.get(url)
            if existing is None:
                best[url] = replace(
                    it,
                    signals=DiscoverySignals(
                        paths=(path,),
                        age_days=None,
                        freshness=0.0,
                        multi_path=0.0,
                        popularity=0.0,
                    ),
                )
                continue
            paths = list(_paths_of(existing))
            if path not in paths:
                paths.append(path)
            base = it if len(it.summary_raw or "") > len(existing.summary_raw or "") else existing
            other = existing if base is it else it
            merged_raw = dict(other.score_raw or {})
            merged_raw.update(base.score_raw or {})
            best[url] = replace(
                base,
                score_raw=merged_raw,
                signals=DiscoverySignals(
                    paths=tuple(paths),
                    age_days=None,
                    freshness=0.0,
                    multi_path=0.0,
                    popularity=0.0,
                ),
            )
    return list(best.values())


def collect_paths(
    fetchers: Mapping[str, PathFetcher],
    *,
    now: datetime,
    max_age_days: int,
    freshness_horizon_days: int,
    log_label: str = "",
) -> list[IntelligenceItem]:
    """Run named path fetchers, merge, freshness-filter, attach full signals."""
    path_map: dict[str, list[IntelligenceItem]] = {}
    for name, fetch in fetchers.items():
        try:
            rows = list(fetch() or [])
            path_map[name] = rows
            log.info("%s path %s: %d item(s)", log_label or "collect", name, len(rows))
        except Exception as exc:  # noqa: BLE001
            log.warning("%s path %s failed: %s", log_label or "collect", name, exc)
            path_map[name] = []

    merged = merge_by_url(path_map)
    fresh = filter_fresh(merged, now=now, max_age_days=max_age_days)
    horizon = freshness_horizon_days if freshness_horizon_days > 0 else 7
    out: list[IntelligenceItem] = []
    for it in fresh:
        paths = _paths_of(it)
        sig = build_discovery_signals(
            published_at=it.published_at,
            score_raw=it.score_raw or {},
            paths=paths,
            now=now,
            freshness_horizon_days=horizon,
        )
        out.append(replace(it, signals=sig))

    multi = sum(1 for i in out if i.signals and len(i.signals.paths) >= 2)
    log.info(
        "%s: collected %d item(s) (%d multi-path)",
        log_label or "collect", len(out), multi,
    )
    return out
