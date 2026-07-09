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


# --- Godot ----------------------------------------------------------------

@respx.mock
def test_godot_parses_fields_and_skips_rows_without_id() -> None:
    respx.get("https://godotengine.org/asset-library/api/asset").respond(200, json={
        "result": [
            {
                "asset_id": 123, "title": "TileMap Pro", "description": "A 2D tool",
                "author": "bob", "browse_url": "https://example.test/foo",
                "modify_date": 1700000000,
                "rating": {"score": 4.5, "positive_ratings": 10, "negative_ratings": 1},
                "category": "2D Tools", "godot_version": "4.2", "cost": "Free",
            },
            {"title": "NoIdAsset"},  # no asset_id / browse_url -> skipped
        ],
        "total_items": 2,
    })
    cfg = GodotSourceConfig(enabled=True, godot_version="4.x", sort="updated", max_results=30)
    with httpx.Client() as client:
        items = GodotCollector(cfg, client).collect()

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


@respx.mock
def test_godot_falls_back_to_asset_page_url() -> None:
    respx.get("https://godotengine.org/asset-library/api/asset").respond(200, json={
        "result": [{"asset_id": 42, "title": "Fallback"}],
    })
    cfg = GodotSourceConfig(enabled=True, godot_version="4.x", sort="updated", max_results=30)
    with httpx.Client() as client:
        items = GodotCollector(cfg, client).collect()

    assert items[0].source_url == ASSET_PAGE_URL.format(asset_id=42)


@respx.mock
def test_godot_empty_result_returns_empty() -> None:
    respx.get("https://godotengine.org/asset-library/api/asset").respond(200, json={"result": []})
    cfg = GodotSourceConfig(enabled=True, godot_version="4.x", sort="updated", max_results=30)
    with httpx.Client() as client:
        assert GodotCollector(cfg, client).collect() == []


# --- Hacker News ----------------------------------------------------------

@respx.mock
def test_hn_filters_non_stories_and_handles_missing_url() -> None:
    base = "https://hacker-news.firebaseio.com/v0"
    respx.get(f"{base}/topstories.json").respond(200, json=[1, 2, 3])
    respx.get(f"{base}/item/1.json").respond(200, json={
        "id": 1, "type": "story", "by": "alice", "time": 1700000000,
        "title": "Story One", "url": "https://external.test/1",
        "score": 120, "descendants": 42,
    })
    respx.get(f"{base}/item/2.json").respond(200, json={"id": 2, "type": "comment"})  # filtered
    respx.get(f"{base}/item/3.json").respond(200, json={
        "id": 3, "type": "story", "by": "carol", "time": 1700000000,
        "title": "Ask HN: best engine?", "text": "discuss", "score": 30,
    })  # no external url -> HN link used

    cfg = HackerNewsSourceConfig(enabled=True, lists=("topstories",), top_n=20)
    with httpx.Client() as client:
        items = HackerNewsCollector(cfg, client).collect()

    assert len(items) == 2
    by_id = {round(it.score_raw.get("score") or 0): it for it in items}
    story = by_id[120]
    ask = by_id[30]

    assert story.source_url == "https://external.test/1"
    assert story.author == "alice"
    assert story.score_raw["comments"] == 42

    # Ask HN: external url absent -> canonical HN item link is the dedup key
    assert ask.source_url == "https://news.ycombinator.com/item?id=3"
    assert ask.summary_raw == "discuss"


@respx.mock
def test_hn_top_n_limits_items() -> None:
    base = "https://hacker-news.firebaseio.com/v0"
    respx.get(f"{base}/topstories.json").respond(200, json=[10, 11, 12, 13])
    for i in (10, 11):
        respx.get(f"{base}/item/{i}.json").respond(200, json={
            "id": i, "type": "story", "by": "x", "time": 1700000000,
            "title": f"T{i}", "url": f"https://x.test/{i}", "score": i,
        })

    cfg = HackerNewsSourceConfig(enabled=True, lists=("topstories",), top_n=2)
    with httpx.Client() as client:
        items = HackerNewsCollector(cfg, client).collect()

    assert len(items) == 2  # top_n sliced the id list before fetching


@respx.mock
def test_hn_skips_failing_item_without_aborting_batch() -> None:
    base = "https://hacker-news.firebaseio.com/v0"
    respx.get(f"{base}/topstories.json").respond(200, json=[1, 2, 3])
    respx.get(f"{base}/item/1.json").respond(200, json={
        "id": 1, "type": "story", "by": "a", "time": 1700000000,
        "title": "OK", "url": "https://x.test/1", "score": 5,
    })
    respx.get(f"{base}/item/2.json").respond(404)   # deleted story -> non-retryable, skipped
    respx.get(f"{base}/item/3.json").respond(200, json={
        "id": 3, "type": "story", "by": "c", "time": 1700000000,
        "title": "Also OK", "url": "https://x.test/3", "score": 7,
    })

    cfg = HackerNewsSourceConfig(enabled=True, lists=("topstories",), top_n=20)
    with httpx.Client() as client:
        items = HackerNewsCollector(cfg, client).collect()

    assert [i.title for i in items] == ["OK", "Also OK"]   # item 2 skipped, batch continued


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
                "topics": ["godot", "2d"], "pushed_at": "2026-06-30T10:00:00Z",
                "homepage": "https://dev.test",
            },
        ]})

    respx.get("https://api.github.com/search/repositories").mock(side_effect=handler)

    cfg = GitHubSourceConfig(enabled=True, query="godot", pushed_within_days=14,
                             min_stars=30, per_page=30)
    with httpx.Client() as client:
        items = GitHubCollector(cfg, client, token="ghp_token", now=NOW).collect()

    # cutoff = NOW - 14 days = 2026-06-23
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


@respx.mock
def test_github_omits_auth_when_no_token() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return httpx.Response(200, json={"items": []})

    respx.get("https://api.github.com/search/repositories").mock(side_effect=handler)

    cfg = GitHubSourceConfig(enabled=True, query="godot", pushed_within_days=14,
                             min_stars=30, per_page=30)
    with httpx.Client() as client:
        GitHubCollector(cfg, client, token="", now=NOW).collect()

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
            "pushed_at": "2026-06-30T10:00:00Z",
        }],
    })
    cfg = GitHubSourceConfig(
        enabled=True, query="llm game", pushed_within_days=14,
        min_stars=20, per_page=30,
    )
    with httpx.Client() as client:
        items = GitHubCollector(
            cfg, client, token="", now=NOW, source_label="GitHubAI",
        ).collect()

    assert len(items) == 1
    assert items[0].source == "GitHubAI"
    assert items[0].title == "dev/llm-npc"
