"""Tests for pipeline orchestration (uses fake components, no network)."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import httpx

from config import load_config
from collectors.github_collector import GitHubCollector
from models.item import IntelligenceItem
from pipeline import Pipeline, PipelineResult, build_pipeline, main


def _item(url: str, **kw) -> IntelligenceItem:
    base = dict(
        source="GitHub", source_url=url, title=url, summary_raw="",
        author=None, published_at=None,
        fetched_at=datetime(2026, 7, 7, tzinfo=timezone.utc), score_raw={},
    )
    base.update(kw)
    return IntelligenceItem(**base)


# --- fakes ----------------------------------------------------------------

class FakeCollector:
    def __init__(self, items: list[IntelligenceItem], exc: Exception | None = None) -> None:
        self.items = items
        self.exc = exc

    def collect(self) -> list[IntelligenceItem]:
        if self.exc:
            raise self.exc
        return self.items


class FakeScorer:
    def __init__(self) -> None:
        self.calls = 0

    def score_all(self, items: list[IntelligenceItem]) -> list[IntelligenceItem]:
        self.calls += 1
        return [replace(i, score=80.0) for i in items]


class FakeRewriter:
    def __init__(self) -> None:
        self.calls = 0

    def rewrite_all(self, items: list[IntelligenceItem]) -> list[IntelligenceItem]:
        self.calls += 1
        out = []
        for i in items:
            if i.score is not None and i.score >= 70:
                out.append(replace(i, drafts={"小红书": "x"}))
            else:
                out.append(i)
        return out


class FakeFeishu:
    def __init__(self, seen: set[str]) -> None:
        self.seen = seen
        self.created: list[IntelligenceItem] = []
        self.list_called = 0
        self.batch_called = 0

    def list_source_urls(self) -> set[str]:
        self.list_called += 1
        return set(self.seen)

    def batch_create(self, items) -> int:
        self.batch_called += 1
        self.created = list(items)
        return len(self.created)


# --- tests ----------------------------------------------------------------

def test_dry_run_prints_items_without_pushing() -> None:
    lines: list[str] = []
    pipe = Pipeline(
        collectors=[FakeCollector([
            _item("u1"),
            _item("u2", one_line_summary="一句话", recommended_action="发布",
                  drafts={"小红书": "x"}),
        ])],
        out=lines.append,
    )
    result = pipe.run(dry_run=True)

    assert result.collected == 2
    assert result.new_after_dedup == 2
    assert result.pushed == 0
    assert any("u1" in line for line in lines)
    assert any("dry-run" in line for line in lines)
    assert any("一句话" in line for line in lines)
    assert any("发布" in line for line in lines)
    assert any("drafts" in line for line in lines)


def test_dedup_uses_feishu_seen_urls_on_push_path() -> None:
    feishu = FakeFeishu(seen={"u1"})
    pipe = Pipeline(
        collectors=[FakeCollector([
            _item("u1", score=80.0),
            _item("u2", score=80.0),
        ])],
        feishu=feishu,
    )
    result = pipe.run(dry_run=False)

    assert feishu.list_called == 1
    assert feishu.batch_called == 1
    assert [i.source_url for i in feishu.created] == ["u2"]   # u1 deduped
    assert result.pushed == 1
    assert result.collected == 2
    assert result.new_after_dedup == 1


def test_collector_failure_does_not_abort_pipeline() -> None:
    feishu = FakeFeishu(seen=set())
    pipe = Pipeline(
        collectors=[
            FakeCollector([], exc=RuntimeError("godot down")),
            FakeCollector([_item("u1", score=75.0)]),
        ],
        feishu=feishu,
    )
    result = pipe.run(dry_run=False)
    assert result.collected == 1            # only the successful collector counted
    assert result.pushed == 1


def test_scorer_and_rewriter_applied() -> None:
    scorer, rewriter = FakeScorer(), FakeRewriter()
    feishu = FakeFeishu(seen=set())
    pipe = Pipeline(
        collectors=[FakeCollector([_item("u1"), _item("u2")])],
        scorer=scorer, rewriter=rewriter, feishu=feishu,
    )
    result = pipe.run(dry_run=False)

    assert scorer.calls == 1
    assert rewriter.calls == 1
    assert result.scored == 2
    assert result.rewritten == 2
    assert result.ai_aborted is False
    assert all(i.drafts == {"小红书": "x"} for i in feishu.created)
    assert result.pushed == 2


def test_limit_caps_items() -> None:
    feishu = FakeFeishu(seen=set())
    pipe = Pipeline(
        collectors=[FakeCollector([_item(f"u{i}") for i in range(5)])],
        feishu=feishu, out=lambda _: None,
    )
    result = pipe.run(dry_run=False, limit=2)
    assert result.new_after_dedup == 5   # count before limit
    assert result.processed == 2
    assert len(feishu.created) == 0     # unscored items are not pushed


def test_dry_run_lists_feishu_but_does_not_push() -> None:
    feishu = FakeFeishu(seen={"u1"})
    pipe = Pipeline(
        collectors=[FakeCollector([_item("u1"), _item("u2")])],
        feishu=feishu, out=lambda _: None,
    )
    result = pipe.run(dry_run=True)
    # dry-run still de-dupes via read-only list; never writes
    assert feishu.list_called == 1
    assert feishu.batch_called == 0
    assert result.new_after_dedup == 1
    assert [i.source_url for i in result.items] == ["u2"]


def test_push_skips_unscored_and_deleted() -> None:
    feishu = FakeFeishu(seen=set())
    pipe = Pipeline(
        collectors=[FakeCollector([
            _item("u1", score=80.0),
            _item("u2"),  # unscored
            _item("u3", score=90.0, recommended_action="删除"),
        ])],
        feishu=feishu, out=lambda _: None,
    )
    result = pipe.run(dry_run=False)
    assert [i.source_url for i in feishu.created] == ["u1"]
    assert result.pushed == 1


def test_ai_abort_flags_result() -> None:
    class AbortingScorer:
        aborted = False

        def score_all(self, items):
            self.aborted = True
            return items

    feishu = FakeFeishu(seen=set())
    pipe = Pipeline(
        collectors=[FakeCollector([_item("u1")])],
        scorer=AbortingScorer(),  # type: ignore[arg-type]
        feishu=feishu, out=lambda _: None,
    )
    result = pipe.run(dry_run=False)
    assert result.ai_aborted is True
    assert result.pushed == 0


# --- build_pipeline (construction from config) ---------------------------

def _write_cfg(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text("""
sources:
  github: {enabled: true, query: godot, pushed_within_days: 14, min_stars: 30, per_page: 20}
  hackernews: {enabled: false, lists: [topstories], top_n: 5}
  godot: {enabled: false, godot_version: "4.x", sort: updated, max_results: 5}
scoring: {weights: {relevance: 0.3, utility: 0.25, freshness: 0.15, popularity: 0.1, differentiation: 0.1, biz_value: 0.1, risk: 0.1}, score_threshold: 70}
ai: {base_url: "https://api.test/v4/", cheap_model: c, strong_model: s, temperature: 0.2, timeout_seconds: 30}
feishu: {app_token: "${FEISHU_APP_TOKEN}", table_id: "${FEISHU_TABLE_ID}", dedup_lookback_days: 7}
""", encoding="utf-8")
    return path


def test_build_pipeline_without_secrets_disables_ai_and_feishu(tmp_path: Path,
                                                                monkeypatch) -> None:
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    cfg = load_config(_write_cfg(tmp_path), load_env=False)
    pipe = build_pipeline(cfg, client=httpx.Client())

    assert len(pipe.collectors) == 1                      # only github enabled
    assert isinstance(pipe.collectors[0], GitHubCollector)
    assert pipe.scorer is None
    assert pipe.rewriter is None
    assert pipe.feishu is None


def test_build_pipeline_with_secrets_enables_ai_and_feishu(tmp_path: Path,
                                                            monkeypatch) -> None:
    monkeypatch.setenv("AI_API_KEY", "sk")
    monkeypatch.setenv("FEISHU_APP_ID", "id")
    monkeypatch.setenv("FEISHU_APP_SECRET", "secret")
    monkeypatch.setenv("FEISHU_APP_TOKEN", "appT")
    monkeypatch.setenv("FEISHU_TABLE_ID", "tblT")
    cfg = load_config(_write_cfg(tmp_path), load_env=False)
    pipe = build_pipeline(cfg, client=httpx.Client())

    assert pipe.scorer is not None
    assert pipe.rewriter is not None
    assert pipe.feishu is not None


def test_build_pipeline_enables_all_configured_sources(tmp_path: Path,
                                                        monkeypatch) -> None:
    monkeypatch.delenv("AI_API_KEY", raising=False)
    path = tmp_path / "all.yaml"
    path.write_text("""
sources:
  github: {enabled: true, query: godot, pushed_within_days: 14, min_stars: 30, per_page: 20}
  hackernews: {enabled: true, lists: [topstories], top_n: 5}
  godot: {enabled: true, godot_version: "4.x", sort: updated, max_results: 5}
scoring: {weights: {relevance: 0.3, risk: 0.1}, score_threshold: 70}
ai: {base_url: "https://api.test/v4/", cheap_model: c, strong_model: s, temperature: 0.2, timeout_seconds: 30}
feishu: {app_token: "", table_id: "", dedup_lookback_days: 7}
""", encoding="utf-8")
    cfg = load_config(path, load_env=False)
    pipe = build_pipeline(cfg, client=httpx.Client())

    types = {type(c).__name__ for c in pipe.collectors}
    assert types == {"GodotCollector", "HackerNewsCollector", "GitHubCollector"}


def test_main_dry_run_returns_zero(tmp_path: Path, monkeypatch) -> None:
    captured: dict = {}

    class _FakePipe:
        collectors: list = []

        def run(self, *, dry_run: bool = False, limit: int | None = None) -> PipelineResult:
            captured["dry_run"] = dry_run
            captured["limit"] = limit
            return PipelineResult(collected=1, new_after_dedup=1, processed=1)

    monkeypatch.setattr("pipeline.build_pipeline", lambda cfg, **kw: _FakePipe())
    rc = main(["--config", str(_write_cfg(tmp_path)), "--dry-run"])

    assert rc == 0
    assert captured["dry_run"] is True


def test_main_returns_one_on_ai_abort(tmp_path: Path, monkeypatch) -> None:
    class _FakePipe:
        collectors: list = []
        feishu = None

        def run(self, *, dry_run: bool = False, limit: int | None = None) -> PipelineResult:
            return PipelineResult(collected=3, new_after_dedup=3, ai_aborted=True)

    monkeypatch.setattr("pipeline.build_pipeline", lambda cfg, **kw: _FakePipe())
    rc = main(["--config", str(_write_cfg(tmp_path)), "--dry-run"])
    assert rc == 1
