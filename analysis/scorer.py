"""Scoring: cheap model rates dimensions; code computes a deterministic score.

The model only emits per-dimension ratings (0-10) plus qualitative fields.
``compute_score`` applies the weighted formula in code. Collector
``DiscoverySignals`` then anchor ``freshness`` and ``path_corroboration``;
the final score is always recomputed from the merged dimensions (auditable).
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from models.item import IntelligenceItem

from .ai_client import AIAuthError, ChatClient
from .prompts import SCORE_SYSTEM, build_score_user
from .signals import PATH_DIM, apply_discovery_signals

log = logging.getLogger(__name__)

POSITIVE_DIMS = (
    "relevance", "utility", "freshness", "popularity",
    "differentiation", "biz_value", PATH_DIM,
)
RISK_DIM = "risk"
ALL_DIMS = POSITIVE_DIMS + (RISK_DIM,)

VALID_ACTIONS = frozenset({"待审核", "发布", "暂存", "加入周报", "删除"})
VALID_PLATFORMS = frozenset({"小红书", "知乎", "B站"})
RISK_LEVELS = frozenset({"低", "中", "高"})


def compute_score(dimensions: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """Apply the weighted formula and map to a 0-100 score.

    Positive dimensions add (max 100 when all = 10); ``risk`` is a deduction.
    Only dimensions present in ``POSITIVE_DIMS`` / risk with non-zero weight count.
    """
    total = 0.0
    max_total = 0.0
    for d in POSITIVE_DIMS:
        w = float(weights.get(d, 0.0))
        if w == 0.0:
            continue
        total += float(dimensions.get(d, 0.0)) * w
        max_total += 10.0 * w
    risk_w = float(weights.get(RISK_DIM, 0.0))
    risk_deduction = float(dimensions.get(RISK_DIM, 0.0)) * risk_w
    if max_total <= 0:
        return 0.0
    raw = (total - risk_deduction) / max_total * 100.0
    return round(max(0.0, min(100.0, raw)), 1)


def _clamp_dim(value: Any) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return 0.0
    if x != x:  # NaN guard
        return 0.0
    return max(0.0, min(10.0, x))


def coerce_dimensions(raw: Mapping[str, Any]) -> dict[str, float]:
    """Return a complete {dim: float in [0,10]} dict, defaulting unknowns to 0."""
    return {d: _clamp_dim(raw.get(d)) for d in ALL_DIMS}


def _str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _str_list(value: Any, valid: frozenset[str] | None = None) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for v in value:
        s = str(v).strip()
        if not s:
            continue
        if valid is None or s in valid:
            out.append(s)
    return out


def parse_score_response(
    raw: Mapping[str, Any], weights: Mapping[str, float]
) -> dict[str, Any]:
    """Normalise a model response into fields ready for ``dataclasses.replace``."""
    dims = coerce_dimensions(raw)
    action = str(raw.get("recommended_action", "待审核")).strip() or "待审核"
    if action not in VALID_ACTIONS:
        action = "待审核"
    risk_level = str(raw.get("risk_level", "低")).strip() or "低"
    if risk_level not in RISK_LEVELS:
        risk_level = "低"
    return {
        "dimensions": dims,
        "score": compute_score(dims, weights),
        "category": _str(raw.get("category")),
        "tags": tuple(_str_list(raw.get("tags"))),
        "risk_level": risk_level,
        "one_line_summary": _str(raw.get("one_line_summary")),
        "recommended_action": action,
        "recommended_platforms": tuple(_str_list(raw.get("recommended_platforms"), VALID_PLATFORMS)),
        "target_audience": _str(raw.get("target_audience")),
    }


class Scorer:
    def __init__(
        self,
        model: str,
        weights: Mapping[str, float],
        client: ChatClient,
        *,
        temperature: float = 0.2,
        timeout: float = 60.0,
    ) -> None:
        self._model = model
        self._weights = weights
        self._client = client
        self._temperature = temperature
        self._timeout = timeout
        self.aborted: bool = False  # True if last score_all hit AIAuthError

    def score(self, item: IntelligenceItem) -> IntelligenceItem:
        raw = self._client.chat_json(
            model=self._model, system=SCORE_SYSTEM, user=build_score_user(item),
            temperature=self._temperature, timeout=self._timeout,
        )
        analysis = parse_score_response(raw, self._weights)
        # Anchor calendar freshness + path corroboration, then recompute once.
        dims = apply_discovery_signals(analysis["dimensions"], item)
        analysis["dimensions"] = dims
        analysis["score"] = compute_score(dims, self._weights)
        return replace(item, **analysis)

    def score_all(self, items: list[IntelligenceItem]) -> list[IntelligenceItem]:
        """Score every item; on per-item failure, keep the original (unscored).

        Auth failures (``AIAuthError``) abort the rest of the batch so we do not
        hammer a dead key/endpoint and leave a trail of empty retries.
        Sets ``self.aborted`` when an auth failure occurs.
        """
        self.aborted = False
        scored: list[IntelligenceItem] = []
        for item in items:
            if self.aborted:
                scored.append(item)
                continue
            try:
                scored.append(self.score(item))
            except AIAuthError as exc:
                log.error("AI auth failed during scoring — aborting batch: %s", exc)
                scored.append(item)
                self.aborted = True
            except Exception as exc:  # noqa: BLE001 - degrade, don't crash the batch
                log.warning("Scoring failed for %s: %s", item.source_url, exc)
                scored.append(item)
        log.info("Scored %d/%d items%s",
                 sum(1 for i in scored if i.is_scored), len(items),
                 " (aborted)" if self.aborted else "")
        return scored
