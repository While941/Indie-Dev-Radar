"""Configuration loading for Indie-Dev-Radar.

Reads ``config.yaml`` (non-secret structure) and merges secrets from the
process environment / a local ``.env``. Values referenced as ``${VAR}`` inside
the YAML are expanded from the environment so that secrets never live in the
repo.

Design notes:
- Pure parsing + typed dataclasses (frozen) — easy to test, no I/O surprises.
- Missing referenced env vars expand to an empty string instead of crashing,
  so ``--dry-run`` works without Feishu/AI credentials. Callers validate at
  the point of use (fail fast where it actually matters).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - dotenv is a runtime dep, fallback is benign
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

log = logging.getLogger(__name__)

_ENV_VAR = re.compile(r"\$\{(\w+)\}")


@dataclass(frozen=True)
class GitHubSourceConfig:
    enabled: bool
    query: str
    pushed_within_days: int
    min_stars: int
    per_page: int


@dataclass(frozen=True)
class HackerNewsSourceConfig:
    enabled: bool
    lists: tuple[str, ...]
    top_n: int


@dataclass(frozen=True)
class GodotSourceConfig:
    enabled: bool
    godot_version: str
    sort: str
    max_results: int


@dataclass(frozen=True)
class SourcesConfig:
    github: GitHubSourceConfig
    hackernews: HackerNewsSourceConfig
    godot: GodotSourceConfig
    github_ai: GitHubSourceConfig = field(default_factory=lambda: GitHubSourceConfig(
        enabled=False, query="", pushed_within_days=14, min_stars=0, per_page=30,
    ))


@dataclass(frozen=True)
class ScoringConfig:
    weights: dict[str, float]
    score_threshold: float


@dataclass(frozen=True)
class AIConfig:
    base_url: str
    cheap_model: str
    strong_model: str
    temperature: float
    timeout_seconds: int


@dataclass(frozen=True)
class FeishuConfig:
    app_token: str
    table_id: str
    dedup_lookback_days: int


@dataclass(frozen=True)
class DigestConfig:
    """Daily / weekly copy-ready packages (human publishes manually)."""

    daily_enabled: bool
    weekly_enabled: bool
    max_items_daily: int
    max_items_weekly: int
    weekly_lookback_days: int
    # When True, still generate per-item platform posts (expensive; usually off).
    rewrite_per_item: bool
    output_dir: str
    ai_daily_enabled: bool = True
    ai_weekly_enabled: bool = True
    max_items_ai_daily: int = 6
    max_items_ai_weekly: int = 12


@dataclass(frozen=True)
class Config:
    sources: SourcesConfig
    scoring: ScoringConfig
    ai: AIConfig
    feishu: FeishuConfig
    digest: DigestConfig = field(default_factory=lambda: DigestConfig(
        daily_enabled=True,
        weekly_enabled=False,
        max_items_daily=8,
        max_items_weekly=15,
        weekly_lookback_days=7,
        rewrite_per_item=False,
        output_dir="output",
        ai_daily_enabled=True,
        ai_weekly_enabled=True,
        max_items_ai_daily=6,
        max_items_ai_weekly=12,
    ))
    max_items_per_run: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def github_token(self) -> str:
        return os.environ.get("GH_TOKEN", "")

    @property
    def ai_api_key(self) -> str:
        return os.environ.get("AI_API_KEY", "")

    @property
    def feishu_app_id(self) -> str:
        return os.environ.get("FEISHU_APP_ID", "")

    @property
    def feishu_app_secret(self) -> str:
        return os.environ.get("FEISHU_APP_SECRET", "")


def _expand(value: Any) -> Any:
    """Recursively expand ``${VAR}`` references from the environment."""
    if isinstance(value, str):
        def _sub(match: re.Match[str]) -> str:
            return os.environ.get(match.group(1), "")
        return _ENV_VAR.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _github_source(raw: dict[str, Any] | None) -> GitHubSourceConfig:
    gh = raw or {}
    return GitHubSourceConfig(
        enabled=_as_bool(gh.get("enabled", False)),
        query=str(gh.get("query", "")),
        pushed_within_days=int(gh.get("pushed_within_days", 14)),
        min_stars=int(gh.get("min_stars", 0)),
        per_page=int(gh.get("per_page", 30)),
    )


def _build_config(data: dict[str, Any]) -> Config:
    src = data.get("sources", {})
    hn = src.get("hackernews", {})
    gd = src.get("godot", {})

    sources = SourcesConfig(
        github=_github_source(src.get("github")),
        github_ai=_github_source(src.get("github_ai")),
        hackernews=HackerNewsSourceConfig(
            enabled=_as_bool(hn.get("enabled", False)),
            lists=tuple(hn.get("lists", []) or []),
            top_n=int(hn.get("top_n", 20)),
        ),
        godot=GodotSourceConfig(
            enabled=_as_bool(gd.get("enabled", False)),
            godot_version=str(gd.get("godot_version", "")),
            sort=str(gd.get("sort", "updated")),
            max_results=int(gd.get("max_results", 30)),
        ),
    )

    scoring_raw = data.get("scoring", {})
    scoring = ScoringConfig(
        weights={k: float(v) for k, v in (scoring_raw.get("weights", {}) or {}).items()},
        score_threshold=float(scoring_raw.get("score_threshold", 70)),
    )

    ai_raw = data.get("ai", {})
    # AI_BASE_URL env (a secret) overrides the YAML default, so a deploy can
    # swap provider (GLM/DeepSeek/OpenAI) without editing the repo.
    ai = AIConfig(
        base_url=os.environ.get("AI_BASE_URL") or str(ai_raw.get("base_url", "")),
        cheap_model=str(ai_raw.get("cheap_model", "")),
        strong_model=str(ai_raw.get("strong_model", "")),
        temperature=float(ai_raw.get("temperature", 0.2)),
        timeout_seconds=int(ai_raw.get("timeout_seconds", 60)),
    )

    fs_raw = data.get("feishu", {})
    feishu = FeishuConfig(
        app_token=str(fs_raw.get("app_token", "")),
        table_id=str(fs_raw.get("table_id", "")),
        dedup_lookback_days=int(fs_raw.get("dedup_lookback_days", 14)),
    )

    dg_raw = data.get("digest", {}) or {}
    digest = DigestConfig(
        daily_enabled=_as_bool(dg_raw.get("daily_enabled", True)),
        weekly_enabled=_as_bool(dg_raw.get("weekly_enabled", False)),
        ai_daily_enabled=_as_bool(dg_raw.get("ai_daily_enabled", True)),
        ai_weekly_enabled=_as_bool(dg_raw.get("ai_weekly_enabled", True)),
        max_items_daily=int(dg_raw.get("max_items_daily", 8)),
        max_items_weekly=int(dg_raw.get("max_items_weekly", 15)),
        max_items_ai_daily=int(dg_raw.get("max_items_ai_daily", 6)),
        max_items_ai_weekly=int(dg_raw.get("max_items_ai_weekly", 12)),
        weekly_lookback_days=int(dg_raw.get("weekly_lookback_days", 7)),
        rewrite_per_item=_as_bool(dg_raw.get("rewrite_per_item", False)),
        output_dir=str(dg_raw.get("output_dir", "output")),
    )

    max_raw = data.get("max_items_per_run")
    max_items: int | None
    if max_raw is None or max_raw == "":
        max_items = None
    else:
        max_items = int(max_raw)
        if max_items <= 0:
            max_items = None

    return Config(
        sources=sources,
        scoring=scoring,
        ai=ai,
        feishu=feishu,
        digest=digest,
        max_items_per_run=max_items,
        raw=data,
    )


def load_config(path: str | Path = "config.yaml", *, load_env: bool = True) -> Config:
    """Load configuration from YAML, expanding ``${VAR}`` from the environment.

    Args:
        path: Path to the YAML config file.
        load_env: When True, load a sibling ``.env`` first (local dev convenience).
    """
    path = Path(path)
    if load_env:
        env_path = path.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    expanded = _expand(data)
    if not isinstance(expanded, dict):
        raise ValueError(f"Config root must be a mapping, got {type(expanded).__name__}")
    return _build_config(expanded)
