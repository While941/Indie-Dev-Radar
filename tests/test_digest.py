"""Tests for digest selection, parsing, and export."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from analysis.digest import parse_digest_response, select_digest_candidates
from models.digest import DigestPackage, period_label_daily, period_label_weekly
from models.item import IntelligenceItem
from storage.export_copy import export_package


def _item(url: str, score: float | None, **kw) -> IntelligenceItem:
    base = dict(
        source="GitHub", source_url=url, title=url, summary_raw="s",
        author=None, published_at=None,
        fetched_at=datetime(2026, 7, 7, tzinfo=timezone.utc), score_raw={},
        score=score, one_line_summary="一句话",
    )
    base.update(kw)
    return IntelligenceItem(**base)


def test_select_candidates_threshold_and_order() -> None:
    items = [
        _item("u1", 60),
        _item("u2", 90),
        _item("u3", 80),
        _item("u4", 95, recommended_action="删除"),
        _item("u5", None),
    ]
    got = select_digest_candidates(items, score_threshold=70, max_items=2)
    assert [i.source_url for i in got] == ["u2", "u3"]


def test_parse_digest_response() -> None:
    raw = {
        "recommended_title": "本周日推",
        "tags": ["Godot", "AI"],
        "platforms": {
            "小红书": {"title": "xt", "body": "xb" * 20},
            "知乎": {"title": "zt", "body": "zb" * 20},
            "B站": {"title": "bt", "body": "bb" * 10},
        },
    }
    items = [_item("u1", 80), _item("u2", 75)]
    pkg = parse_digest_response(raw, kind="日贴", period_label="2026-07-09", source_items=items)
    assert pkg.kind == "日贴"
    assert pkg.item_count == 2
    assert pkg.platform_posts["小红书"]["title"] == "xt"
    assert "Godot" in pkg.tags
    item = pkg.to_item()
    assert item.source == "日贴"
    assert item.source_url.startswith("digest://")
    assert item.has_publish_content
    assert item.is_scored


def test_period_labels() -> None:
    assert period_label_daily()  # non-empty
    assert "W" in period_label_weekly()


def test_export_html_has_copy_buttons(tmp_path: Path) -> None:
    pkg = DigestPackage(
        kind="日贴",
        period_label="2026-07-09",
        platform_posts={
            "小红书": {"title": "题", "body": "正文内容"},
            "知乎": {"title": "知题", "body": "知正文"},
            "B站": {"title": "B题", "body": "B正文"},
        },
        source_urls=("https://a.test",),
        item_count=1,
        recommended_title="今日情报",
    )
    paths = export_package(pkg, tmp_path)
    html = paths["html"].read_text(encoding="utf-8")
    assert "一键复制标题" in html
    assert "一键复制正文" in html
    assert "正文内容" in html
    assert (tmp_path / "latest-daily.html").exists()
    md = paths["markdown"].read_text(encoding="utf-8")
    assert "## 小红书" in md
