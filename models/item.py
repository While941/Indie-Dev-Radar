"""Core data model: ``IntelligenceItem``.

Frozen (immutable) dataclass — the pipeline produces new instances via
``dataclasses.replace`` at each stage (collect → score → rewrite) rather than
mutating in place. Aligns with the unified field table in ``Plan.md`` §7.2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class IntelligenceItem:
    """A single normalised intelligence record flowing through the pipeline."""

    # --- identity / provenance (always present) ---
    source: str                      # GitHub | HackerNews | Godot
    source_url: str                  # dedup primary key
    title: str
    summary_raw: str
    author: str | None
    published_at: datetime | None
    fetched_at: datetime
    score_raw: dict[str, Any] = field(default_factory=dict)

    # --- AI-filled (None until scored) ---
    category: str | None = None
    tags: tuple[str, ...] = ()
    dimensions: dict[str, float] = field(default_factory=dict)
    score: float | None = None
    risk_level: str = "低"            # 低 | 中 | 高
    one_line_summary: str | None = None
    recommended_action: str | None = None      # 待审核 | 发布 | 暂存 | 加入周报 | 删除
    recommended_platforms: tuple[str, ...] = ()
    target_audience: str | None = None
    recommended_title: str | None = None
    drafts: dict[str, str] = field(default_factory=dict)  # {小红书/公众号/B站: ...}

    @property
    def is_scored(self) -> bool:
        return self.score is not None
