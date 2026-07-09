"""Tests for analysis.rewriter — threshold gating + parser + integration."""
from __future__ import annotations

from datetime import datetime, timezone

from analysis.rewriter import Rewriter, parse_rewrite_response
from models.item import IntelligenceItem

from .conftest import FakeChatClient


def _item(score: float | None = None) -> IntelligenceItem:
    return IntelligenceItem(
        source="GitHub", source_url="https://x.test/1", title="Thing",
        summary_raw="desc", author="a", published_at=None,
        fetched_at=datetime(2026, 7, 7, tzinfo=timezone.utc), score_raw={},
        score=score,
    )


# --- parse_rewrite_response -----------------------------------------------

def test_parse_filters_invalid_draft_keys() -> None:
    raw = {
        "recommended_title": "好标题",
        "tags": ["Godot", "  ", "2D"],
        "drafts": {"小红书": "xhs", "公众号": "gz", "知乎": "should-drop", "B站": ""},
    }
    parsed = parse_rewrite_response(raw)
    assert parsed["recommended_title"] == "好标题"
    assert parsed["tags"] == ("Godot", "2D")
    assert set(parsed["drafts"]) == {"小红书", "公众号"}   # 知乎 filtered, B站 empty dropped


def test_parse_omits_missing_fields() -> None:
    parsed = parse_rewrite_response({"drafts": {}})
    assert parsed["drafts"] == {}
    assert "recommended_title" not in parsed
    assert "tags" not in parsed


# --- Rewriter threshold + integration -------------------------------------

def test_should_rewrite_respects_threshold() -> None:
    rw = Rewriter("strong", threshold=70, client=FakeChatClient([]))
    assert rw.should_rewrite(_item(85)) is True
    assert rw.should_rewrite(_item(70)) is True     # inclusive
    assert rw.should_rewrite(_item(69)) is False
    assert rw.should_rewrite(_item(None)) is False  # unscored


def test_rewrite_applies_drafts() -> None:
    raw = {"recommended_title": "T", "tags": ["a"],
           "drafts": {"小红书": "x", "公众号": "y", "B站": "z"}}
    rw = Rewriter("strong", threshold=70, client=FakeChatClient([raw]),
                  temperature=0.4, timeout=10)
    result = rw.rewrite(_item(85))
    assert result.recommended_title == "T"
    assert result.drafts == {"小红书": "x", "公众号": "y", "B站": "z"}
    assert rw._client.calls[0]["model"] == "strong"  # type: ignore[attr-defined]


def test_rewrite_all_skips_low_score_and_degrades() -> None:
    rw = Rewriter("strong", threshold=70,
                  client=FakeChatClient([
                      {"drafts": {"小红书": "ok"}},          # for the 85 item
                      ValueError("boom"),                    # for the 90 item
                  ]),
                  timeout=10)
    results = rw.rewrite_all([_item(50), _item(85), _item(90)])
    assert results[0].drafts == {}        # below threshold, untouched
    assert results[1].drafts == {"小红书": "ok"}
    assert results[2].drafts == {}        # failed -> unchanged
    assert rw.aborted is False


def test_rewrite_all_aborts_on_auth_error() -> None:
    from analysis.ai_client import AIAuthError
    client = FakeChatClient([
        AIAuthError("401"),
        {"drafts": {"小红书": "never"}},
    ])
    rw = Rewriter("strong", threshold=70, client=client, timeout=10)
    results = rw.rewrite_all([_item(80), _item(90)])
    assert results[0].drafts == {}
    assert results[1].drafts == {}
    assert rw.aborted is True
    assert len(client.calls) == 1
