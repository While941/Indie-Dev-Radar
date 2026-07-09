"""Tests for config loading & env-var expansion."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from config import load_config


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
sources:
  github:
    enabled: true
    query: "godot OR gamedev"
    pushed_within_days: 7
    min_stars: 25
    per_page: 20
  github_ai:
    enabled: true
    query: "llm game"
    pushed_within_days: 14
    min_stars: 20
    per_page: 25
  hackernews:
    enabled: true
    lists: ["topstories"]
    top_n: 15
  godot:
    enabled: false
    godot_version: "4.x"
    sort: "new"
    max_results: 10

scoring:
  weights: {relevance: 0.30, utility: 0.25, freshness: 0.15,
            popularity: 0.10, differentiation: 0.10, biz_value: 0.10, risk: 0.10}
  score_threshold: 65

ai:
  base_url: "https://example.test/v4/"
  cheap_model: "cheap"
  strong_model: "strong"
  temperature: 0.1
  timeout_seconds: 30

feishu:
  app_token: "${FEISHU_APP_TOKEN}"
  table_id: "${FEISHU_TABLE_ID}"
  dedup_lookback_days: 7

max_items_per_run: 40

digest:
  daily_enabled: true
  weekly_enabled: false
  ai_daily_enabled: true
  ai_weekly_enabled: false
  max_items_daily: 5
  max_items_ai_daily: 4
  rewrite_per_item: false
  output_dir: "out-test"
        """,
        encoding="utf-8",
    )
    return path


def test_loads_typed_config(cfg_path: Path) -> None:
    cfg = load_config(cfg_path, load_env=False)

    assert cfg.sources.github.enabled is True
    assert cfg.sources.github.query == "godot OR gamedev"
    assert cfg.sources.github.min_stars == 25
    assert cfg.sources.github.pushed_within_days == 7
    # defaults when not specified in yaml
    assert cfg.sources.github.max_age_days == 3
    assert cfg.sources.github.path_sorts == ("stars", "updated")

    assert cfg.sources.github_ai.enabled is True
    assert cfg.sources.github_ai.query == "llm game"
    assert cfg.sources.github_ai.min_stars == 20

    assert cfg.sources.hackernews.lists == ("topstories",)
    assert cfg.sources.hackernews.top_n == 15
    assert cfg.sources.hackernews.require_topic_match is True

    assert cfg.sources.godot.enabled is False
    # legacy single ``sort`` folds into sorts
    assert cfg.sources.godot.sorts == ("new",)
    assert cfg.scoring.score_threshold == pytest.approx(65.0)
    assert cfg.scoring.weights.get("path_corroboration") == pytest.approx(0.10)
    assert cfg.ai.cheap_model == "cheap"
    assert cfg.feishu.dedup_lookback_days == 7
    assert cfg.max_items_per_run == 40
    assert cfg.digest.daily_enabled is True
    assert cfg.digest.ai_daily_enabled is True
    assert cfg.digest.ai_weekly_enabled is False
    assert cfg.digest.max_items_daily == 5
    assert cfg.digest.max_items_ai_daily == 4
    assert cfg.digest.rewrite_per_item is False
    assert cfg.digest.output_dir == "out-test"


def test_env_var_expansion(cfg_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEISHU_APP_TOKEN", "tok123")
    monkeypatch.setenv("FEISHU_TABLE_ID", "tbl456")
    cfg = load_config(cfg_path, load_env=False)

    assert cfg.feishu.app_token == "tok123"
    assert cfg.feishu.table_id == "tbl456"


def test_missing_env_var_expands_to_empty(cfg_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FEISHU_APP_TOKEN", raising=False)
    monkeypatch.delenv("FEISHU_TABLE_ID", raising=False)
    cfg = load_config(cfg_path, load_env=False)

    # dry-run friendly: missing secrets expand to "" instead of crashing
    assert cfg.feishu.app_token == ""
    assert cfg.feishu.table_id == ""


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml", load_env=False)


def test_secret_accessors_from_env(cfg_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "ghp_xxx")
    monkeypatch.setenv("AI_API_KEY", "sk_yyy")
    cfg = load_config(cfg_path, load_env=False)

    assert cfg.github_token == "ghp_xxx"
    assert cfg.ai_api_key == "sk_yyy"
