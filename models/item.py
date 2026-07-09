"""Core data model: ``IntelligenceItem``.

Frozen (immutable) dataclass — the pipeline produces new instances via
``dataclasses.replace`` at each stage (collect → score → rewrite) rather than
mutating in place. Aligns with the unified field table in ``Plan.md`` §7.2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from models.signals import DiscoverySignals


# Platforms we generate publish-ready title+body for (review → later auto-publish).
PUBLISH_PLATFORMS = ("小红书", "知乎", "B站")


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
    # Multi-path / freshness signals (set by collectors; optional for tests/fakes).
    signals: DiscoverySignals | None = None

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
    recommended_title: str | None = None       # generic fallback title
    # Per-platform publish payload: {"小红书": {"title": "...", "body": "..."}, ...}
    platform_posts: dict[str, dict[str, str]] = field(default_factory=dict)
    # Legacy body-only map (kept filled from platform_posts for logs / older tests)
    drafts: dict[str, str] = field(default_factory=dict)

    @property
    def is_scored(self) -> bool:
        return self.score is not None

    @property
    def has_publish_content(self) -> bool:
        """True when at least one platform has a non-empty body ready for review."""
        for post in self.platform_posts.values():
            if isinstance(post, dict) and (post.get("body") or "").strip():
                return True
        return bool(self.drafts)
