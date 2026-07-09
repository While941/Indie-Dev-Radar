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

def test_parse_platforms_title_body() -> None:
    raw = {
        "recommended_title": "好标题",
        "tags": ["Godot", "  ", "2D"],
        "platforms": {
            "小红书": {"title": "xhs题", "body": "xhs文"},
            "知乎": {"title": "zh题", "body": "zh文"},
            "B站": {"title": "b题", "body": "b文"},
            "公众号": {"title": "drop", "body": "drop"},
        },
    }
    parsed = parse_rewrite_response(raw)
    assert parsed["recommended_title"] == "好标题"
    assert parsed["tags"] == ("Godot", "2D")
    assert set(parsed["platform_posts"]) == {"小红书", "知乎", "B站"}
    assert parsed["platform_posts"]["小红书"] == {"title": "xhs题", "body": "xhs文"}
    assert parsed["drafts"]["小红书"] == "xhs文"


def test_parse_legacy_drafts_strings() -> None:
    raw = {
        "drafts": {"小红书": "xhs", "知乎": "zh", "公众号": "drop", "B站": ""},
    }
    parsed = parse_rewrite_response(raw)
    assert set(parsed["platform_posts"]) == {"小红书", "知乎"}
    assert parsed["platform_posts"]["小红书"]["body"] == "xhs"
    assert parsed["drafts"]["知乎"] == "zh"


def test_parse_omits_missing_fields() -> None:
    parsed = parse_rewrite_response({"platforms": {}})
    assert parsed["platform_posts"] == {}
    assert parsed["drafts"] == {}
    assert "recommended_title" not in parsed
    assert "tags" not in parsed


def test_parse_fallback_recommended_title_from_platform() -> None:
    parsed = parse_rewrite_response({
        "platforms": {"知乎": {"title": "仅知乎标题", "body": "正文"}},
    })
    assert parsed["recommended_title"] == "仅知乎标题"


# --- Rewriter threshold + integration -------------------------------------

def test_should_rewrite_respects_threshold() -> None:
    rw = Rewriter("strong", threshold=70, client=FakeChatClient([]))
    assert rw.should_rewrite(_item(85)) is True
    assert rw.should_rewrite(_item(70)) is True     # inclusive
    assert rw.should_rewrite(_item(69)) is False
    assert rw.should_rewrite(_item(None)) is False  # unscored


def test_rewrite_applies_platform_posts() -> None:
    raw = {
        "recommended_title": "T",
        "tags": ["a"],
        "platforms": {
            "小红书": {"title": "xt", "body": "xb"},
            "知乎": {"title": "zt", "body": "zb"},
            "B站": {"title": "bt", "body": "bb"},
        },
    }
    rw = Rewriter("strong", threshold=70, client=FakeChatClient([raw]),
                  temperature=0.4, timeout=10)
    result = rw.rewrite(_item(85))
    assert result.recommended_title == "T"
    assert result.platform_posts["小红书"]["title"] == "xt"
    assert result.drafts == {"小红书": "xb", "知乎": "zb", "B站": "bb"}
    assert result.has_publish_content is True
    assert rw._client.calls[0]["model"] == "strong"  # type: ignore[attr-defined]


def test_rewrite_all_skips_low_score_and_degrades() -> None:
    rw = Rewriter("strong", threshold=70,
                  client=FakeChatClient([
                      {"platforms": {"小红书": {"title": "t", "body": "ok"}}},
                      ValueError("boom"),
                  ]),
                  timeout=10)
    results = rw.rewrite_all([_item(50), _item(85), _item(90)])
    assert results[0].has_publish_content is False
    assert results[1].drafts == {"小红书": "ok"}
    assert results[2].has_publish_content is False
    assert rw.aborted is False


def test_rewrite_all_aborts_on_auth_error() -> None:
    from analysis.ai_client import AIAuthError
    client = FakeChatClient([
        AIAuthError("401"),
        {"platforms": {"小红书": {"title": "n", "body": "never"}}},
    ])
    rw = Rewriter("strong", threshold=70, client=client, timeout=10)
    results = rw.rewrite_all([_item(80), _item(90)])
    assert results[0].has_publish_content is False
    assert results[1].has_publish_content is False
    assert rw.aborted is True
    assert len(client.calls) == 1
