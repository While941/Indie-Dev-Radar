"""Typed discovery / freshness signals produced at collect time."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class DiscoverySignals:
    """Deterministic signals attached by multi-path collectors.

    Units are 0–1 unless noted. The scorer anchors ``freshness`` and
    ``path_corroboration`` from these, then runs ``compute_score`` once.
    """

    paths: tuple[str, ...]
    age_days: float | None
    freshness: float  # 0–1 calendar freshness
    multi_path: float  # 0–1 corroboration across orthogonal path families
    popularity: float  # 0–1 raw heat (prompt/log only; not re-scored into formula)

    @property
    def path_count(self) -> int:
        return len(self.paths)


def age_days(published_at: datetime | None, now: datetime) -> float | None:
    if published_at is None:
        return None
    pub = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
    ref = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return max(0.0, (ref - pub).total_seconds() / 86400.0)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def freshness_unit(age: float | None, horizon_days: int) -> float:
    """1.0 = today; 0.0 = at/over horizon. Unknown → mid-low."""
    h = max(1, int(horizon_days))
    if age is None:
        return 0.35
    if age <= 0:
        return 1.0
    return _clamp01(1.0 - age / float(h))


def path_families(paths: Sequence[str]) -> frozenset[str]:
    """Collapse non-orthogonal GitHub sort variants into one family.

    ``gh_stars`` / ``gh_updated`` share the same Search query → one family.
    ``gh_created`` is independent. Godot/HN path names stay distinct.
    """
    families: set[str] = set()
    for p in paths:
        name = (p or "").strip()
        if not name:
            continue
        if name == "gh_created":
            families.add("gh_created")
        elif name.startswith("gh_"):
            families.add("gh_activity")
        else:
            families.add(name)
    return frozenset(families)


def multi_path_unit(paths: Sequence[str]) -> float:
    n = len(path_families(paths))
    if n <= 0:
        return 0.3
    if n == 1:
        return 0.45
    if n == 2:
        return 0.75
    return 1.0


def popularity_unit(score_raw: Mapping[str, Any]) -> float:
    """Map source-specific heat into 0–1 (logging / AI context only)."""
    stars = score_raw.get("stars")
    if stars is not None:
        try:
            s = float(stars)
            return _clamp01(math.log1p(max(0.0, s)) / math.log1p(10_000.0))
        except (TypeError, ValueError):
            pass
    hn = score_raw.get("score")
    if hn is not None:
        try:
            return _clamp01(math.log1p(max(0.0, float(hn))) / math.log1p(500.0))
        except (TypeError, ValueError):
            pass
    rating = score_raw.get("rating_score")
    if rating is not None:
        try:
            return _clamp01(float(rating) / 5.0)
        except (TypeError, ValueError):
            pass
    pos = score_raw.get("positive")
    if pos is not None:
        try:
            return _clamp01(math.log1p(max(0.0, float(pos))) / math.log1p(200.0))
        except (TypeError, ValueError):
            pass
    return 0.3


def build_discovery_signals(
    *,
    published_at: datetime | None,
    score_raw: Mapping[str, Any],
    paths: Sequence[str],
    now: datetime,
    freshness_horizon_days: int,
) -> DiscoverySignals:
    age = age_days(published_at, now)
    path_t = tuple(p for p in paths if p)
    return DiscoverySignals(
        paths=path_t,
        age_days=None if age is None else round(age, 2),
        freshness=round(freshness_unit(age, freshness_horizon_days), 3),
        multi_path=round(multi_path_unit(path_t), 3),
        popularity=round(popularity_unit(score_raw), 3),
    )
