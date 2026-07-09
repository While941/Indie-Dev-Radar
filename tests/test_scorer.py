"""Tests for analysis.scorer — pure formula + parser + Scorer integration."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from analysis.scorer import (
    Scorer,
    coerce_dimensions,
    compute_score,
    parse_score_response,
)
from models.item import IntelligenceItem

from .conftest import FakeChatClient

DEFAULT_WEIGHTS = {
    "relevance": 0.30, "utility": 0.25, "freshness": 0.15,
    "popularity": 0.10, "differentiation": 0.10, "biz_value": 0.10, "risk": 0.10,
}


# --- compute_score (pure) -------------------------------------------------

def test_score_all_ten_no_risk_is_hundred() -> None:
    dims = {d: 10 for d in ("relevance", "utility", "freshness", "popularity",
                            "differentiation", "biz_value")}
    dims["risk"] = 0
    assert compute_score(dims, DEFAULT_WEIGHTS) == 100.0


def test_score_risk_is_a_deduction() -> None:
    dims = {d: 10 for d in ("relevance", "utility", "freshness", "popularity",
                            "differentiation", "biz_value")}
    dims["risk"] = 10
    assert compute_score(dims, DEFAULT_WEIGHTS) == 90.0


def test_score_all_zero_high_risk_clamps_to_zero() -> None:
    dims = {d: 0 for d in ("relevance", "utility", "freshness", "popularity",
                           "differentiation", "biz_value")}
    dims["risk"] = 10
    assert compute_score(dims, DEFAULT_WEIGHTS) == 0.0


def test_score_midpoint_is_fifty() -> None:
    dims = {d: 5 for d in ("relevance", "utility", "freshness", "popularity",
                           "differentiation", "biz_value")}
    dims["risk"] = 0
    assert compute_score(dims, DEFAULT_WEIGHTS) == 50.0


def test_score_zero_weights_returns_zero() -> None:
    dims = {"relevance": 10, "risk": 0}
    assert compute_score(dims, {}) == 0.0


def test_score_custom_weights_respected() -> None:
    dims = {"relevance": 10, "utility": 0, "freshness": 0, "popularity": 0,
            "differentiation": 0, "biz_value": 0, "risk": 0}
    # only relevance weighted -> 10 * 1.0 / 10 * 100 = 100
    assert compute_score(dims, {"relevance": 1.0}) == 100.0


# --- coerce_dimensions ----------------------------------------------------

def test_coerce_clamps_and_defaults() -> None:
    out = coerce_dimensions({"relevance": 12, "utility": -3, "freshness": "7",
                             "popularity": "NaN-ish", "risk": None})
    assert out["relevance"] == 10.0
    assert out["utility"] == 0.0
    assert out["freshness"] == 7.0
    assert out["popularity"] == 0.0      # non-numeric
    assert out["risk"] == 0.0            # None


def test_coerce_complete_keys() -> None:
    out = coerce_dimensions({})
    assert set(out) == {
        "relevance", "utility", "freshness", "popularity",
        "differentiation", "biz_value", "path_corroboration", "risk",
    }


# --- parse_score_response -------------------------------------------------

def test_parse_maps_and_validates() -> None:
    raw = {
        "relevance": 8, "utility": 7, "freshness": 9, "popularity": 6,
        "differentiation": 8, "biz_value": 5, "risk": 2,
        "category": "Godot 插件", "tags": ["2D", "TileMap"],
        "risk_level": "中", "one_line_summary": "好用",
        "recommended_action": "发布",
        "recommended_platforms": ["小红书", "TikTok", "知乎"],
        "target_audience": "Godot 开发者",
    }
    parsed = parse_score_response(raw, DEFAULT_WEIGHTS)
    assert parsed["category"] == "Godot 插件"
    assert parsed["tags"] == ("2D", "TileMap")
    assert parsed["risk_level"] == "中"
    assert parsed["recommended_action"] == "发布"
    assert parsed["recommended_platforms"] == ("小红书", "知乎")  # TikTok filtered
    assert parsed["score"] == pytest.approx(72.0, abs=0.1)


def test_parse_defaults_invalid_enums() -> None:
    parsed = parse_score_response(
        {"recommended_action": "马上发", "risk_level": "极高",
         "recommended_platforms": "小红书"},
        DEFAULT_WEIGHTS,
    )
    assert parsed["recommended_action"] == "待审核"
    assert parsed["risk_level"] == "低"
    assert parsed["recommended_platforms"] == ()   # string not list


# --- Scorer integration ---------------------------------------------------

def _item() -> IntelligenceItem:
    return IntelligenceItem(
        source="Godot", source_url="https://x.test/1", title="TileMap Pro",
        summary_raw="A 2D tool", author="bob", published_at=None,
        fetched_at=datetime(2026, 7, 7, tzinfo=timezone.utc), score_raw={},
    )


def test_scorer_score_applies_analysis() -> None:
    raw = {"relevance": 9, "utility": 9, "freshness": 8, "popularity": 5,
           "differentiation": 8, "biz_value": 4, "risk": 1,
           "category": "Godot 插件", "tags": ["2D"],
           "risk_level": "低", "one_line_summary": "summary",
           "recommended_action": "加入周报",
           "recommended_platforms": ["小红书"], "target_audience": "开发者"}
    fake = FakeChatClient([raw])
    scorer = Scorer("cheap-model", DEFAULT_WEIGHTS, fake, temperature=0.2, timeout=10)

    result = scorer.score(_item())

    assert result.is_scored
    assert result.category == "Godot 插件"
    assert result.tags == ("2D",)
    assert result.recommended_action == "加入周报"
    assert fake.calls[0]["model"] == "cheap-model"
    assert "TileMap Pro" in fake.calls[0]["user"]


def test_scorer_all_degrades_on_failure() -> None:
    fake = FakeChatClient([ValueError("boom"), _ok_response()])
    scorer = Scorer("cheap", DEFAULT_WEIGHTS, fake, timeout=10)

    results = scorer.score_all([_item(), _item()])
    assert results[0].score is None       # first failed -> unchanged
    assert results[1].is_scored           # second succeeded
    assert scorer.aborted is False


def test_scorer_all_aborts_on_auth_error() -> None:
    from analysis.ai_client import AIAuthError
    fake = FakeChatClient([_ok_response(), AIAuthError("401"), _ok_response()])
    scorer = Scorer("cheap", DEFAULT_WEIGHTS, fake, timeout=10)

    results = scorer.score_all([_item(), _item(), _item()])
    assert results[0].is_scored
    assert results[1].score is None      # auth fail item kept unscored
    assert results[2].score is None      # not attempted after abort
    assert scorer.aborted is True
    assert len(fake.calls) == 2           # third item never sent to AI


def _ok_response() -> dict:
    return {"relevance": 8, "utility": 7, "freshness": 7, "popularity": 5,
            "differentiation": 6, "biz_value": 4, "risk": 2}


def test_scorer_anchors_signals_then_recomputes() -> None:
    """Final score = compute_score after freshness + path_corroboration anchors."""
    from models.signals import DiscoverySignals

    weights = {
        **DEFAULT_WEIGHTS,
        "path_corroboration": 0.10,
    }
    # AI claims freshness=10; discovery says freshness=0.2 → dim 2.0
    raw = {d: 10 for d in ("relevance", "utility", "freshness", "popularity",
                           "differentiation", "biz_value")}
    raw["risk"] = 0
    raw.update({
        "category": "x", "tags": [], "risk_level": "低",
        "one_line_summary": "s", "recommended_action": "发布",
        "recommended_platforms": ["知乎"], "target_audience": "dev",
    })
    fake = FakeChatClient([raw])
    scorer = Scorer("cheap", weights, fake, timeout=10)
    item = IntelligenceItem(
        source="GitHub", source_url="https://x/1", title="t",
        summary_raw="s", author="a", published_at=None,
        fetched_at=datetime(2026, 7, 7, tzinfo=timezone.utc),
        score_raw={"stars": 10},
        signals=DiscoverySignals(
            paths=("godot_updated", "godot_rating"),
            age_days=2.0,
            freshness=0.2,
            multi_path=0.75,
            popularity=0.4,
        ),
    )
    result = scorer.score(item)
    assert result.dimensions["freshness"] == pytest.approx(2.0)
    assert result.dimensions["path_corroboration"] == pytest.approx(7.5)
    # Score must match recompute from stored dimensions
    assert result.score == compute_score(result.dimensions, weights)
