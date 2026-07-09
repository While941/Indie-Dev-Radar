"""Tests for storage: dedup + Feishu client."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest
import respx

from models.item import IntelligenceItem
from storage.dedup import filter_new
from storage.feishu import (
    BATCH_SIZE,
    FeishuClient,
    _extract_url,
    _to_ms,
    format_dimensions,
    item_to_fields,
)


# --- helpers --------------------------------------------------------------

def make_item(url: str, **kw) -> IntelligenceItem:
    base = dict(
        source="GitHub", source_url=url, title=url, summary_raw="",
        author=None, published_at=None,
        fetched_at=datetime(2026, 7, 7, tzinfo=timezone.utc), score_raw={},
    )
    base.update(kw)
    return IntelligenceItem(**base)


# --- dedup ----------------------------------------------------------------

def test_filter_new_excludes_seen_urls() -> None:
    items = [make_item("u1"), make_item("u2"), make_item("u3")]
    result = filter_new(items, {"u2"})
    assert [i.source_url for i in result] == ["u1", "u3"]


def test_filter_new_dedups_within_batch() -> None:
    items = [make_item("u1"), make_item("u1"), make_item("u2")]
    result = filter_new(items, set())
    assert [i.source_url for i in result] == ["u1", "u2"]


def test_filter_new_skips_empty_url() -> None:
    items = [make_item(""), make_item("u1")]
    result = filter_new(items, set())
    assert [i.source_url for i in result] == ["u1"]


# --- _extract_url ---------------------------------------------------------

def test_extract_url_handles_various_feishu_shapes() -> None:
    assert _extract_url("https://x.test/a") == "https://x.test/a"
    assert _extract_url({"link": "https://x.test/b", "text": "label"}) == "https://x.test/b"
    assert _extract_url([{"text": "https://x.test/c"}]) == "https://x.test/c"
    assert _extract_url(None) == ""
    assert _extract_url("  https://x.test/d  ") == "https://x.test/d"


# --- item_to_fields -------------------------------------------------------

def test_item_to_fields_full_mapping() -> None:
    published = datetime(2026, 6, 1, tzinfo=timezone.utc)
    fetched = datetime(2026, 7, 7, tzinfo=timezone.utc)
    item = IntelligenceItem(
        source="GitHub", source_url="https://github.com/a/b", title="a/b",
        summary_raw="desc", author="a", published_at=published, fetched_at=fetched,
        score_raw={}, category="开源项目", tags=("godot", "2d"), score=82.5,
        dimensions={"relevance": 8, "utility": 7, "freshness": 6, "popularity": 5,
                    "differentiation": 4, "biz_value": 3, "risk": 1},
        risk_level="中", one_line_summary="一句话", recommended_action="发布",
        recommended_platforms=("小红书", "知乎"), target_audience="Godot 开发者",
        recommended_title="推荐标题",
        platform_posts={
            "小红书": {"title": "xhs题", "body": "xhs文"},
            "知乎": {"title": "zh题", "body": "zh文"},
            "B站": {"title": "b题", "body": "b文"},
        },
    )
    f = item_to_fields(item)
    assert f["来源"] == "GitHub"
    assert f["原始链接"] == "https://github.com/a/b"
    assert f["AI 评分"] == 82.5
    assert "相关度:8" in f["维度评分"]
    assert "风险:1" in f["维度评分"]
    assert f["分类"] == "开源项目"
    assert f["标签"] == ["godot", "2d"]
    assert f["推荐动作"] == "发布"
    assert f["推荐发布平台"] == ["小红书", "知乎"]
    assert f["小红书标题"] == "xhs题"
    assert f["小红书正文"] == "xhs文"
    assert f["知乎标题"] == "zh题"
    assert f["知乎正文"] == "zh文"
    assert f["B站标题"] == "b题"
    assert f["B站正文"] == "b文"
    assert f["发布时间"] == _to_ms(published)
    assert f["抓取时间"] == _to_ms(fetched)


def test_format_dimensions_empty() -> None:
    assert format_dimensions({}) == ""


def test_item_to_fields_minimal_defaults_action_and_risk() -> None:
    item = make_item("u1")
    f = item_to_fields(item)
    assert f["推荐动作"] == "待审核"      # default when None
    assert f["风险等级"] == "低"          # model default
    assert "AI 评分" not in f             # score None -> omitted
    assert "分类" not in f
    assert "发布时间" not in f            # published None -> omitted
    assert "抓取时间" in f


# --- FeishuClient (HTTP via respx) ---------------------------------------

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
RECORDS_URL = "https://open.feishu.cn/open-apis/bitable/v1/apps/appT/tables/tblT/records"


def _client() -> FeishuClient:
    return FeishuClient("id", "secret", "appT", "tblT", client=httpx.Client())


@respx.mock
def test_token_cached_across_operations() -> None:
    token_route = respx.post(TOKEN_URL).respond(200, json={
        "code": 0, "tenant_access_token": "t-abc", "expire": 7200,
    })
    respx.get(RECORDS_URL).respond(200, json={"code": 0, "data": {"items": [], "has_more": False}})
    respx.post(f"{RECORDS_URL}/batch_create").respond(200, json={
        "code": 0, "data": {"records": [{"record_id": "r1"}]},
    })

    fc = _client()
    fc.list_source_urls()
    fc.batch_create([make_item("u1")])
    assert token_route.call_count == 1   # token fetched once, then cached


@respx.mock
def test_list_source_urls_respects_lookback() -> None:
    """Records older than lookback_days are excluded from the seen set."""
    respx.post(TOKEN_URL).respond(200, json={
        "code": 0, "tenant_access_token": "t", "expire": 7200,
    })
    # "now" = 2026-07-07; lookback 14 days => cutoff ~ 2026-06-23
    recent_ms = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
    old_ms = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp() * 1000)
    respx.get(RECORDS_URL).respond(200, json={
        "code": 0,
        "data": {
            "items": [
                {"fields": {"原始链接": "https://new.test", "抓取时间": recent_ms}},
                {"fields": {"原始链接": "https://old.test", "抓取时间": old_ms}},
                {"fields": {"原始链接": "https://no-date.test"}},  # missing date -> still seen
            ],
            "has_more": False,
        },
    })
    now = datetime(2026, 7, 7, tzinfo=timezone.utc)
    fc = FeishuClient("id", "secret", "appT", "tblT", client=httpx.Client(),
                      dedup_lookback_days=14, now=now)
    urls = fc.list_source_urls()
    assert urls == {"https://new.test", "https://no-date.test"}
    assert "https://old.test" not in urls


@respx.mock
def test_list_source_urls_paginates_and_extracts() -> None:
    respx.post(TOKEN_URL).respond(200, json={"code": 0, "tenant_access_token": "t", "expire": 7200})
    respx.get(RECORDS_URL).mock(side_effect=[
        httpx.Response(200, json={"code": 0, "data": {
            "items": [
                {"fields": {"原始链接": "https://x.test/1"}},
                {"fields": {"原始链接": [{"link": "https://x.test/2"}]}},
            ],
            "has_more": True, "page_token": "p2",
        }}),
        httpx.Response(200, json={"code": 0, "data": {
            "items": [{"fields": {"原始链接": "https://x.test/3"}}],
            "has_more": False,
        }}),
    ])
    fc = _client()
    urls = fc.list_source_urls()
    assert urls == {"https://x.test/1", "https://x.test/2", "https://x.test/3"}


@respx.mock
def test_batch_create_sends_fields_and_counts() -> None:
    respx.post(TOKEN_URL).respond(200, json={"code": 0, "tenant_access_token": "t", "expire": 7200})
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "data": {"records": [{"record_id": "r1"}, {"record_id": "r2"}]}})

    respx.post(f"{RECORDS_URL}/batch_create").mock(side_effect=handler)
    fc = _client()
    count = fc.batch_create([make_item("u1"), make_item("u2")])

    assert count == 2
    payload = captured["body"]
    assert len(payload["records"]) == 2
    assert payload["records"][0]["fields"]["原始链接"] == "u1"
    assert payload["records"][0]["fields"]["推荐动作"] == "待审核"


@respx.mock
def test_batch_create_chunks_at_batch_size() -> None:
    respx.post(TOKEN_URL).respond(200, json={"code": 0, "tenant_access_token": "t", "expire": 7200})
    create_route = respx.post(f"{RECORDS_URL}/batch_create").respond(200, json={
        "code": 0, "data": {"records": [{"record_id": "r"}]},
    })
    fc = _client()
    items = [make_item(f"u{i}") for i in range(BATCH_SIZE + 1)]
    count = fc.batch_create(items)
    assert create_route.call_count == 2      # split into 500 + 1
    assert count == 2                         # each mock response returns 1 record


@respx.mock
def test_list_records_error_code_raises() -> None:
    respx.post(TOKEN_URL).respond(200, json={"code": 0, "tenant_access_token": "t", "expire": 7200})
    respx.get(RECORDS_URL).respond(200, json={"code": 99991661, "msg": "token invalid"})
    with pytest.raises(RuntimeError):
        _client().list_source_urls()


@respx.mock
def test_list_source_urls_stops_when_has_more_without_page_token() -> None:
    # Malformed Feishu response: has_more=True but no page_token. Must NOT loop forever.
    respx.post(TOKEN_URL).respond(200, json={"code": 0, "tenant_access_token": "t", "expire": 7200})
    route = respx.get(RECORDS_URL).respond(200, json={"code": 0, "data": {
        "items": [{"fields": {"原始链接": "https://x.test/1"}}],
        "has_more": True,
        # page_token intentionally absent
    }})
    fc = _client()
    urls = fc.list_source_urls()
    assert urls == {"https://x.test/1"}
    assert route.call_count == 1   # terminated after the first (broken) page


@respx.mock
def test_clear_all_deletes_all_records() -> None:
    respx.post(TOKEN_URL).respond(200, json={"code": 0, "tenant_access_token": "t", "expire": 7200})
    respx.get(RECORDS_URL).respond(200, json={"code": 0, "data": {
        "items": [{"record_id": "r1"}, {"record_id": "r2"}], "has_more": False,
    }})
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "data": {"records": []}})

    respx.post(f"{RECORDS_URL}/batch_delete").mock(side_effect=handler)
    fc = _client()
    assert fc.clear_all() == 2
    assert captured["body"]["records"] == ["r1", "r2"]


@respx.mock
def test_token_error_raises() -> None:
    respx.post(TOKEN_URL).respond(200, json={"code": 99991663, "msg": "bad secret"})
    fc = _client()
    with pytest.raises(RuntimeError):
        fc.list_source_urls()
