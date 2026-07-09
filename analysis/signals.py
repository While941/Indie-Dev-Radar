"""Apply collector DiscoverySignals into scoring dimensions (single formula)."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from models.item import IntelligenceItem

# Dimension set by calendar age (0–10). Path corroboration is orthogonal evidence.
PATH_DIM = "path_corroboration"


def apply_discovery_signals(
    dimensions: Mapping[str, float],
    item: IntelligenceItem,
) -> dict[str, float]:
    """Return dimensions with deterministic freshness + path_corroboration applied.

    Does not re-score popularity (AI already sees score_raw). Caller must run
    ``compute_score`` on the result so score always matches dimensions.
    """
    dims = {k: float(v) for k, v in dimensions.items()}
    sig = item.signals
    if sig is None:
        dims.setdefault(PATH_DIM, 0.0)
        return dims
    dims["freshness"] = round(float(sig.freshness) * 10.0, 1)
    dims[PATH_DIM] = round(float(sig.multi_path) * 10.0, 1)
    return dims


def signals_prompt_block(item: IntelligenceItem) -> dict[str, Any]:
    """Compact dict for the scoring prompt (typed signals + raw heat)."""
    sig = item.signals
    out: dict[str, Any] = dict(item.score_raw or {})
    if sig is None:
        return out
    out["discovery_paths"] = list(sig.paths)
    out["path_count"] = sig.path_count
    out["age_days"] = sig.age_days
    out["signal_freshness"] = sig.freshness
    out["signal_multi_path"] = sig.multi_path
    out["signal_popularity"] = sig.popularity
    return out
