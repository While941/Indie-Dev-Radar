"""Tests for multi-path merge, freshness gate, and discovery signals."""
from __future__ import annotations

from datetime import datetime, timezone

from analysis.scorer import compute_score
from analysis.signals import apply_discovery_signals
from collectors.multi_path import collect_paths, filter_fresh, merge_by_url
from models.item import IntelligenceItem
from models.signals import (
    DiscoverySignals,
    build_discovery_signals,
    multi_path_unit,
    path_families,
)

NOW = datetime(2026, 7, 7, tzinfo=timezone.utc)


def _item(
    url: str,
    *,
    published_at: datetime | None = None,
    stars: int | None = None,
    summary: str = "",
) -> IntelligenceItem:
    raw: dict = {}
    if stars is not None:
        raw["stars"] = stars
    return IntelligenceItem(
        source="GitHub",
        source_url=url,
        title=url,
        summary_raw=summary,
        author="x",
        published_at=published_at,
        fetched_at=NOW,
        score_raw=raw,
    )


def test_merge_by_url_unions_paths() -> None:
    a = _item("https://x/1", stars=10, summary="short")
    b = _item("https://x/1", stars=10, summary="a much longer summary here")
    c = _item("https://x/2", stars=5)
    merged = merge_by_url({"p1": [a, c], "p2": [b]})
    by_url = {i.source_url: i for i in merged}
    assert set(by_url) == {"https://x/1", "https://x/2"}
    assert set(by_url["https://x/1"].signals.paths) == {"p1", "p2"}  # type: ignore[union-attr]
    assert "longer" in by_url["https://x/1"].summary_raw


def test_filter_fresh_drops_old() -> None:
    fresh = _item("https://x/f", published_at=datetime(2026, 7, 6, tzinfo=timezone.utc))
    stale = _item("https://x/s", published_at=datetime(2026, 6, 1, tzinfo=timezone.utc))
    kept = filter_fresh([fresh, stale], now=NOW, max_age_days=3)
    assert [i.source_url for i in kept] == ["https://x/f"]


def test_filter_fresh_disabled() -> None:
    stale = _item("https://x/s", published_at=datetime(2020, 1, 1, tzinfo=timezone.utc))
    assert filter_fresh([stale], now=NOW, max_age_days=0) == [stale]


def test_github_sort_paths_collapse_to_one_family() -> None:
    assert path_families(("gh_stars", "gh_updated")) == frozenset({"gh_activity"})
    assert path_families(("gh_stars", "gh_created")) == frozenset({"gh_activity", "gh_created"})
    assert multi_path_unit(("gh_stars", "gh_updated")) == multi_path_unit(("gh_stars",))


def test_build_discovery_signals() -> None:
    sig = build_discovery_signals(
        published_at=NOW,
        score_raw={"stars": 50},
        paths=("gh_stars", "gh_created"),
        now=NOW,
        freshness_horizon_days=3,
    )
    assert sig.path_count == 2
    assert sig.freshness == 1.0
    assert sig.multi_path > multi_path_unit(("gh_stars",))
    assert 0 <= sig.popularity <= 1


def test_collect_paths_merges_and_annotates() -> None:
    a = _item("https://x/1", published_at=NOW, stars=100, summary="hi")
    b = _item("https://x/1", published_at=NOW, stars=100, summary="hello world")
    out = collect_paths(
        {"p1": lambda: [a], "p2": lambda: [b]},
        now=NOW,
        max_age_days=3,
        freshness_horizon_days=3,
        log_label="test",
    )
    assert len(out) == 1
    assert out[0].signals is not None
    assert set(out[0].signals.paths) == {"p1", "p2"}
    assert out[0].signals.age_days == 0.0


def test_apply_discovery_recomputes_score_from_dimensions() -> None:
    """Score always matches compute_score after anchoring signals."""
    weights = {
        "relevance": 0.3, "utility": 0.2, "freshness": 0.2,
        "popularity": 0.1, "differentiation": 0.1, "biz_value": 0.0,
        "path_corroboration": 0.1, "risk": 0.1,
    }
    item = _item("https://x/1", published_at=NOW, stars=10)
    item = IntelligenceItem(
        source=item.source, source_url=item.source_url, title=item.title,
        summary_raw=item.summary_raw, author=item.author,
        published_at=item.published_at, fetched_at=item.fetched_at,
        score_raw=item.score_raw,
        signals=DiscoverySignals(
            paths=("godot_updated", "godot_rating"),
            age_days=0.0,
            freshness=1.0,
            multi_path=0.75,
            popularity=0.5,
        ),
    )
    ai_dims = {
        "relevance": 8, "utility": 8, "freshness": 2, "popularity": 5,
        "differentiation": 5, "biz_value": 0, "path_corroboration": 0, "risk": 1,
    }
    merged = apply_discovery_signals(ai_dims, item)
    assert merged["freshness"] == 10.0
    assert merged["path_corroboration"] == 7.5
    score = compute_score(merged, weights)
    # Re-running compute_score on same dims is stable
    assert score == compute_score(merged, weights)
