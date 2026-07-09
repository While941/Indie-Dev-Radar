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
        """,
        encoding="utf-8",
    )
    return path


def test_loads_typed_config(cfg_path: Path) -> None:
    cfg = load_config(cfg_path, load_env=False)

    assert cfg.sources.github.enabled is True
    assert cfg.sources.github.query == "godot OR gamedev"
    assert cfg.sources.github.min_stars == 25

    assert cfg.sources.hackernews.lists == ("topstories",)
    assert cfg.sources.hackernews.top_n == 15

    assert cfg.sources.godot.enabled is False
    assert cfg.scoring.score_threshold == pytest.approx(65.0)
    assert cfg.ai.cheap_model == "cheap"
    assert cfg.feishu.dedup_lookback_days == 7
    assert cfg.max_items_per_run == 40


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
