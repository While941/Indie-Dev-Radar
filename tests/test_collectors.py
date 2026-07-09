"""Tests for the Godot / Hacker News / GitHub collectors (field mapping + boundaries)."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import respx

from collectors.github_collector import GitHubCollector
from collectors.godot_collector import ASSET_PAGE_URL, GodotCollector
from collectors.hackernews_collector import HackerNewsCollector
from config import GitHubSourceConfig, GodotSourceConfig, HackerNewsSourceConfig


NOW = datetime(2026, 7, 7, 0, 0, 0, tzinfo=timezone.utc)
FRESH_EPOCH = int(datetime(2026, 7, 6, 0, 0, 0, tzinfo=timezone.utc).timestamp())
FRESH_ISO = "2026-07-06T10:00:00Z"


def _godot_cfg(**kw) -> GodotSourceConfig:
    base = dict(
        enabled=True, godot_version="4.x", max_results=30,
        max_age_days=0, freshness_horizon_days=7, sorts=("updated",),
    )
    base.update(kw)
    return GodotSourceConfig(**base)


def _hn_cfg(**kw) -> HackerNewsSourceConfig:
    base = dict(
        enabled=True, lists=("topstories",), top_n=20,
        max_age_days=0, freshness_horizon_days=7,
        require_topic_match=False, topic_keywords=(),
    )
    base.update(kw)
    return HackerNewsSourceConfig(**base)


def _gh_cfg(**kw) -> GitHubSourceConfig:
    base = dict(
        enabled=True, query="godot", pushed_within_days=14, min_stars=30,
        per_page=30, max_age_days=0, freshness_horizon_days=7,
        path_sorts=("stars",), created_within_days=0,
    )
    base.update(kw)
    return GitHubSourceConfig(**base)


# --- Godot ----------------------------------------------------------------

@respx.mock
def test_godot_parses_fields_and_skips_rows_without_id() -> None:
    respx.get("https://godotengine.org/asset-library/api/asset").respond(200, json={
        "result": [
            {
                "asset_id": 123, "title": "TileMap Pro", "description": "A 2D tool",
                "author": "bob", "browse_url": "https://example.test/foo",
                "modify_date": FRESH_EPOCH,
                "rating": {"score": 4.5, "positive_ratings": 10, "negative_ratings": 1},
                "category": "2D Tools", "godot_version": "4.2", "cost": "Free",
            },
            {"title": "NoIdAsset"},
        ],
        "total_items": 2,
    })
    with httpx.Client() as client:
        items = GodotCollector(_godot_cfg(), client, now=NOW).collect()

    assert len(items) == 1
    it = items[0]
    assert it.source == "Godot"
    assert it.source_url == "https://example.test/foo"
    assert it.title == "TileMap Pro"
    assert it.author == "bob"
    assert it.summary_raw == "A 2D tool"
    assert it.published_at is not None
    assert it.score_raw["rating_score"] == 4.5
    assert it.score_raw["category"] == "2D Tools"
    assert it.signals is not None
    assert "godot_updated" in it.signals.paths


@respx.mock
def test_godot_falls_back_to_asset_page_url() -> None:
    respx.get("https://godotengine.org/asset-library/api/asset").respond(200, json={
        "result": [{"asset_id": 42, "title": "Fallback", "modify_date": FRESH_EPOCH}],
    })
    with httpx.Client() as client:
        items = GodotCollector(_godot_cfg(), client, now=NOW).collect()

    assert items[0].source_url == ASSET_PAGE_URL.format(asset_id=42)


@respx.mock
def test_godot_empty_result_returns_empty() -> None:
    respx.get("https://godotengine.org/asset-library/api/asset").respond(200, json={"result": []})
    with httpx.Client() as client:
        assert GodotCollector(_godot_cfg(), client, now=NOW).collect() == []


@respx.mock
def test_godot_multi_path_merge_and_freshness() -> None:
    stale = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())

    def handler(request: httpx.Request) -> httpx.Response:
        sort = request.url.params.get("sort")
        if sort == "updated":
            return httpx.Response(200, json={"result": [
                {
                    "asset_id": 1, "title": "Hot Plugin",
                    "browse_url": "https://a.test/1", "modify_date": FRESH_EPOCH,
                    "rating": {"score": 4.0},
                },
                {
                    "asset_id": 2, "title": "Old Plugin",
                    "browse_url": "https://a.test/2", "modify_date": stale,
                },
            ]})
        return httpx.Response(200, json={"result": [
            {
                "asset_id": 1, "title": "Hot Plugin",
                "browse_url": "https://a.test/1", "modify_date": FRESH_EPOCH,
                "rating": {"score": 4.8},
            },
        ]})

    respx.get("https://godotengine.org/asset-library/api/asset").mock(side_effect=handler)
    cfg = _godot_cfg(sorts=("updated", "rating"), max_age_days=3)
    with httpx.Client() as client:
        items = GodotCollector(cfg, client, now=NOW).collect()

    assert len(items) == 1
    it = items[0]
    assert it.source_url == "https://a.test/1"
    assert it.signals is not None
    assert "godot_updated" in it.signals.paths and "godot_rating" in it.signals.paths
    assert it.signals.path_count == 2


# --- Hacker News ----------------------------------------------------------

@respx.mock
def test_hn_filters_non_stories_and_handles_missing_url() -> None:
    base = "https://hacker-news.firebaseio.com/v0"
    respx.get(f"{base}/topstories.json").respond(200, json=[1, 2, 3])
    respx.get(f"{base}/item/1.json").respond(200, json={
        "id": 1, "type": "story", "by": "alice", "time": FRESH_EPOCH,
        "title": "Story One", "url": "https://external.test/1",
        "score": 120, "descendants": 42,
    })
    respx.get(f"{base}/item/2.json").respond(200, json={"id": 2, "type": "comment"})
    respx.get(f"{base}/item/3.json").respond(200, json={
        "id": 3, "type": "story", "by": "carol", "time": FRESH_EPOCH,
        "title": "Ask HN: best engine?", "text": "discuss", "score": 30,
    })

    with httpx.Client() as client:
        items = HackerNewsCollector(_hn_cfg(), client, now=NOW).collect()

    assert len(items) == 2
    by_id = {round(it.score_raw.get("score") or 0): it for it in items}
    story = by_id[120]
    ask = by_id[30]

    assert story.source_url == "https://external.test/1"
    assert story.author == "alice"
    assert story.score_raw["comments"] == 42
    assert ask.source_url == "https://news.ycombinator.com/item?id=3"
    assert ask.summary_raw == "discuss"


@respx.mock
def test_hn_top_n_limits_items() -> None:
    base = "https://hacker-news.firebaseio.com/v0"
    respx.get(f"{base}/topstories.json").respond(200, json=[10, 11, 12, 13])
    for i in (10, 11):
        respx.get(f"{base}/item/{i}.json").respond(200, json={
            "id": i, "type": "story", "by": "x", "time": FRESH_EPOCH,
            "title": f"T{i}", "url": f"https://x.test/{i}", "score": i,
        })

    with httpx.Client() as client:
        items = HackerNewsCollector(_hn_cfg(top_n=2), client, now=NOW).collect()

    assert len(items) == 2


@respx.mock
def test_hn_skips_failing_item_without_aborting_batch() -> None:
    base = "https://hacker-news.firebaseio.com/v0"
    respx.get(f"{base}/topstories.json").respond(200, json=[1, 2, 3])
    respx.get(f"{base}/item/1.json").respond(200, json={
        "id": 1, "type": "story", "by": "a", "time": FRESH_EPOCH,
        "title": "OK", "url": "https://x.test/1", "score": 5,
    })
    respx.get(f"{base}/item/2.json").respond(404)
    respx.get(f"{base}/item/3.json").respond(200, json={
        "id": 3, "type": "story", "by": "c", "time": FRESH_EPOCH,
        "title": "Also OK", "url": "https://x.test/3", "score": 7,
    })

    with httpx.Client() as client:
        items = HackerNewsCollector(_hn_cfg(), client, now=NOW).collect()

    assert [i.title for i in items] == ["OK", "Also OK"]


@respx.mock
def test_hn_topic_and_age_filters() -> None:
    base = "https://hacker-news.firebaseio.com/v0"
    respx.get(f"{base}/topstories.json").respond(200, json=[1, 2, 3])
    respx.get(f"{base}/item/1.json").respond(200, json={
        "id": 1, "type": "story", "by": "a", "time": FRESH_EPOCH,
        "title": "New Godot 4 plugin for tilemaps", "url": "https://x.test/1", "score": 50,
    })
    respx.get(f"{base}/item/2.json").respond(200, json={
        "id": 2, "type": "story", "by": "b", "time": FRESH_EPOCH,
        "title": "Rust async runtime internals", "url": "https://x.test/2", "score": 200,
    })
    old = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
    respx.get(f"{base}/item/3.json").respond(200, json={
        "id": 3, "type": "story", "by": "c", "time": old,
        "title": "Old gamedev article", "url": "https://x.test/3", "score": 80,
    })

    cfg = _hn_cfg(max_age_days=2, require_topic_match=True)
    with httpx.Client() as client:
        items = HackerNewsCollector(cfg, client, now=NOW).collect()

    assert len(items) == 1
    assert "Godot" in items[0].title


# --- GitHub ---------------------------------------------------------------

@respx.mock
def test_github_builds_rolling_query_and_sends_auth() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return httpx.Response(200, json={"items": [
            {
                "full_name": "dev/godot-thing", "html_url": "https://github.com/dev/godot-thing",
                "description": "A useful addon", "owner": {"login": "dev"},
                "stargazers_count": 240, "forks_count": 18, "language": "GDScript",
                "topics": ["godot", "2d"], "pushed_at": FRESH_ISO,
                "homepage": "https://dev.test",
            },
        ]})

    respx.get("https://api.github.com/search/repositories").mock(side_effect=handler)

    cfg = _gh_cfg(pushed_within_days=14)
    with httpx.Client() as client:
        items = GitHubCollector(cfg, client, token="ghp_token", now=NOW).collect()

    assert captured["params"]["q"] == "godot pushed:>2026-06-23 stars:>30"
    assert captured["params"]["sort"] == "stars"
    assert captured["headers"]["authorization"] == "Bearer ghp_token"
    assert "indie-dev-radar" in captured["headers"]["user-agent"].lower()

    assert len(items) == 1
    it = items[0]
    assert it.source == "GitHub"
    assert it.source_url == "https://github.com/dev/godot-thing"
    assert it.title == "dev/godot-thing"
    assert it.author == "dev"
    assert it.summary_raw == "A useful addon"
    assert it.score_raw["stars"] == 240
    assert it.score_raw["language"] == "GDScript"
    assert it.published_at is not None
    assert it.signals is not None
    assert "gh_stars" in it.signals.paths


@respx.mock
def test_github_omits_auth_when_no_token() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return httpx.Response(200, json={"items": []})

    respx.get("https://api.github.com/search/repositories").mock(side_effect=handler)

    with httpx.Client() as client:
        GitHubCollector(_gh_cfg(), client, token="", now=NOW).collect()

    assert "authorization" not in captured["headers"]


@respx.mock
def test_github_source_label_githubai() -> None:
    respx.get("https://api.github.com/search/repositories").respond(200, json={
        "items": [{
            "full_name": "dev/llm-npc",
            "html_url": "https://github.com/dev/llm-npc",
            "description": "NPC dialogue",
            "owner": {"login": "dev"},
            "stargazers_count": 50,
            "forks_count": 2,
            "language": "Python",
            "topics": ["ai", "game"],
            "pushed_at": FRESH_ISO,
        }],
    })
    cfg = _gh_cfg(query="llm game", min_stars=20)
    with httpx.Client() as client:
        items = GitHubCollector(
            cfg, client, token="", now=NOW, source_label="GitHubAI",
        ).collect()

    assert len(items) == 1
    assert items[0].source == "GitHubAI"
    assert items[0].title == "dev/llm-npc"


@respx.mock
def test_github_multi_path_merge_and_drops_stale() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        sort = request.url.params.get("sort")
        q = request.url.params.get("q", "")
        if "created:>" in q:
            return httpx.Response(200, json={"items": [{
                "full_name": "dev/new-game",
                "html_url": "https://github.com/dev/new-game",
                "description": "brand new",
                "owner": {"login": "dev"},
                "stargazers_count": 40,
                "pushed_at": FRESH_ISO,
            }]})
        if sort == "stars":
            return httpx.Response(200, json={"items": [
                {
                    "full_name": "dev/hot",
                    "html_url": "https://github.com/dev/hot",
                    "description": "hot",
                    "owner": {"login": "dev"},
                    "stargazers_count": 500,
                    "pushed_at": FRESH_ISO,
                },
                {
                    "full_name": "dev/stale",
                    "html_url": "https://github.com/dev/stale",
                    "description": "old push",
                    "owner": {"login": "dev"},
                    "stargazers_count": 900,
                    "pushed_at": "2026-01-01T00:00:00Z",
                },
            ]})
        return httpx.Response(200, json={"items": [{
            "full_name": "dev/hot",
            "html_url": "https://github.com/dev/hot",
            "description": "hot updated",
            "owner": {"login": "dev"},
            "stargazers_count": 500,
            "pushed_at": FRESH_ISO,
        }]})

    respx.get("https://api.github.com/search/repositories").mock(side_effect=handler)
    cfg = _gh_cfg(
        path_sorts=("stars", "updated"),
        created_within_days=7,
        max_age_days=3,
        pushed_within_days=3,
    )
    with httpx.Client() as client:
        items = GitHubCollector(cfg, client, token="", now=NOW).collect()

    urls = {i.source_url for i in items}
    assert "https://github.com/dev/stale" not in urls
    assert "https://github.com/dev/hot" in urls
    assert "https://github.com/dev/new-game" in urls
    hot = next(i for i in items if i.source_url.endswith("/hot"))
    assert hot.signals is not None
    assert hot.signals.path_count == 2
    assert set(hot.signals.paths) >= {"gh_stars", "gh_updated"}
