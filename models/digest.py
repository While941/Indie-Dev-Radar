"""Digest packages: daily / weekly multi-item posts ready for human copy-publish."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from models.item import PUBLISH_PLATFORMS, IntelligenceItem

# Package rows written to Feishu / excluded from weekly candidate pools.
DIGEST_SOURCES = frozenset({"日贴", "周贴", "AI日贴", "AI周贴"})
DIGEST_KINDS = DIGEST_SOURCES  # alias: valid DigestPackage.kind values

# Source label used by the dedicated GitHub AI collector.
AI_INTEL_SOURCE = "GitHubAI"
GENERAL_INTEL_SOURCES = frozenset({"GitHub", "HackerNews", "Godot"})


@dataclass(frozen=True)
class DigestPackage:
    """One publish-ready package covering multiple intelligence items."""

    kind: str  # 日贴 | 周贴 | AI日贴 | AI周贴
    period_label: str  # e.g. 2026-07-09 or 2026-W28
    platform_posts: dict[str, dict[str, str]]  # {平台: {title, body}}
    source_urls: tuple[str, ...] = ()
    item_count: int = 0
    recommended_title: str | None = None
    tags: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def stable_url(self) -> str:
        """Dedup key so re-running the same day does not flood the table."""
        return f"digest://{self.kind}/{self.period_label}"

    @property
    def package_title(self) -> str:
        if self.recommended_title:
            return self.recommended_title
        if self.kind.startswith("AI"):
            return f"独立游戏 AI 情报{self.kind} · {self.period_label}"
        return f"独立游戏情报{self.kind} · {self.period_label}"

    def to_item(self) -> IntelligenceItem:
        """Map into IntelligenceItem so Feishu writer / export stay unified."""
        drafts = {
            k: (v.get("body") or "")
            for k, v in self.platform_posts.items()
            if (v.get("body") or "").strip()
        }
        summary_bits = [
            f"{self.kind} · {self.period_label} · 收录 {self.item_count} 条",
            "内容已由 AI 整理，可直接复制各平台标题与正文发布。",
        ]
        if self.source_urls:
            summary_bits.append("来源: " + " | ".join(self.source_urls[:12]))
        return IntelligenceItem(
            source=self.kind,
            source_url=self.stable_url,
            title=self.package_title,
            summary_raw="\n".join(summary_bits),
            author="Indie-Dev-Radar",
            published_at=None,
            fetched_at=self.created_at,
            score_raw={"item_count": self.item_count, "kind": self.kind},
            category=self.kind,
            tags=self.tags,
            score=100.0,  # always pushable; not a model score
            risk_level="低",
            one_line_summary=f"{self.kind}可复制发布包（{self.item_count} 条情报）",
            recommended_action="待审核",
            recommended_platforms=tuple(
                p for p in PUBLISH_PLATFORMS if p in self.platform_posts
            ),
            target_audience="独立游戏开发者",
            recommended_title=self.recommended_title or self.package_title,
            platform_posts=dict(self.platform_posts),
            drafts=drafts,
        )


def period_label_daily(d: date | None = None) -> str:
    return (d or date.today()).isoformat()


def period_label_weekly(d: date | None = None) -> str:
    """ISO week label, e.g. 2026-W28."""
    day = d or date.today()
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"
