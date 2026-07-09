"""Tests for storage.feishu_schema — schema integrity + writer/schema consistency."""
from __future__ import annotations

from datetime import datetime, timezone

from models.item import IntelligenceItem
from storage.feishu import item_to_fields
from storage.feishu_schema import (
    FIELDS,
    FIELD_NAMES,
    PRIMARY_FIELD,
    URL_FIELD,
    FieldType,
    field_payload,
)


def test_no_duplicate_field_names() -> None:
    names = [f.name for f in FIELDS]
    assert len(names) == len(set(names))


def test_primary_and_url_fields_present() -> None:
    assert PRIMARY_FIELD in FIELD_NAMES
    assert URL_FIELD in FIELD_NAMES
    assert len(FIELDS) >= 18


def test_item_to_fields_keys_are_all_in_schema() -> None:
    """DRY guard: every key item_to_fields can emit must be a known schema field."""
    item = IntelligenceItem(
        source="GitHub", source_url="u", title="t", summary_raw="s", author="a",
        published_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 7, 7, tzinfo=timezone.utc), score_raw={},
        category="c", tags=("x",),
        dimensions={"relevance": 8, "utility": 7, "freshness": 6, "popularity": 5,
                    "differentiation": 4, "biz_value": 3, "risk": 1},
        score=80.0, risk_level="中",
        one_line_summary="o", recommended_action="发布", recommended_platforms=("小红书", "知乎"),
        target_audience="t", recommended_title="ti",
        platform_posts={
            "小红书": {"title": "xt", "body": "xb"},
            "知乎": {"title": "zt", "body": "zb"},
            "B站": {"title": "bt", "body": "bb"},
        },
    )
    extra = set(item_to_fields(item)) - FIELD_NAMES
    assert not extra, f"item_to_fields emits unknown fields: {extra}"


def test_field_payload_text_has_no_property() -> None:
    spec = next(f for f in FIELDS if f.name == "标题")
    assert field_payload(spec) == {"field_name": "标题", "type": int(FieldType.TEXT)}


def test_field_payload_single_select_has_options() -> None:
    spec = next(f for f in FIELDS if f.name == "来源")
    payload = field_payload(spec)
    assert payload["type"] == int(FieldType.SINGLE_SELECT)
    assert payload["property"]["options"] == [
        {"name": "GitHub"}, {"name": "HackerNews"}, {"name": "Godot"},
        {"name": "日贴"}, {"name": "周贴"},
    ]
