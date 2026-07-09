"""Pipeline: collect -> dedup -> score -> digests (日贴/周贴/AI*) -> export + Feishu.

Human only reviews and copy-publishes. No auto-posting to social platforms.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from analysis.ai_client import AIClient
from analysis.digest import DigestBuilder
from analysis.rewriter import Rewriter
from analysis.scorer import Scorer
from collectors import Collector, GitHubCollector, GodotCollector, HackerNewsCollector
from config import Config, load_config
from models.digest import (
    AI_INTEL_SOURCE,
    GENERAL_INTEL_SOURCES,
    DigestPackage,
)
from models.item import IntelligenceItem
from storage.dedup import filter_new
from storage.export_copy import export_package
from storage.feishu import FeishuClient

log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    collected: int = 0
    new_after_dedup: int = 0
    processed: int = 0
    scored: int = 0
    rewritten: int = 0
    digests: int = 0
    pushed: int = 0
    ai_aborted: bool = False
    items: list[IntelligenceItem] = field(default_factory=list, repr=False)
    digest_packages: list[DigestPackage] = field(default_factory=list, repr=False)
    export_paths: list[str] = field(default_factory=list)


def _is_pushable(item: IntelligenceItem) -> bool:
    """Only scored items that are not model-deleted belong in the review queue."""
    if not item.is_scored:
        return False
    if item.recommended_action == "删除":
        return False
    return True


def _split_pools(
    items: Sequence[IntelligenceItem],
) -> tuple[list[IntelligenceItem], list[IntelligenceItem]]:
    """Partition scored intel into general vs AI tracks (by source label)."""
    ai = [it for it in items if it.source == AI_INTEL_SOURCE]
    general = [it for it in items if it.source != AI_INTEL_SOURCE]
    return general, ai


@dataclass
class Pipeline:
    """Orchestrates the flow. Components are duck-typed for easy testing."""

    collectors: list[Collector]
    scorer: Scorer | None = None
    rewriter: Rewriter | None = None
    digest_builder: DigestBuilder | None = None
    feishu: FeishuClient | None = None
    daily_enabled: bool = True
    weekly_enabled: bool = False
    ai_daily_enabled: bool = True
    ai_weekly_enabled: bool = True
    weekly_lookback_days: int = 7
    max_items_weekly: int = 15
    max_items_ai_daily: int = 6
    max_items_ai_weekly: int = 12
    output_dir: str = "output"
    out: Callable[[str], None] = print

    def run(
        self,
        *,
        dry_run: bool = False,
        limit: int | None = None,
        weekly: bool | None = None,
        weekly_only: bool = False,
    ) -> PipelineResult:
        collected = self._collect()

        seen: set[str] = set()
        if self.feishu is not None:
            try:
                seen = self.feishu.list_source_urls()
            except Exception as exc:  # noqa: BLE001
                if dry_run:
                    log.warning("Feishu list_source_urls failed in dry-run: %s", exc)
                else:
                    raise

        new_items = filter_new(collected, seen)
        new_after_dedup = len(new_items)

        items = new_items
        if limit is not None and limit > 0:
            log.info("Limiting run to %d items (had %d new)", limit, len(items))
            items = items[:limit]

        ai_aborted = False
        if self.scorer is not None:
            items = self.scorer.score_all(items)
            if getattr(self.scorer, "aborted", False):
                ai_aborted = True
        if self.rewriter is not None and not ai_aborted:
            items = self.rewriter.rewrite_all(items)
            if getattr(self.rewriter, "aborted", False):
                ai_aborted = True

        scored_count = sum(1 for i in items if i.is_scored)
        if self.scorer is not None and items and scored_count == 0:
            ai_aborted = True

        packages: list[DigestPackage] = []
        export_paths: list[str] = []
        do_weekly = self.weekly_enabled if weekly is None else weekly
        do_daily = self.daily_enabled and not weekly_only
        do_ai_daily = self.ai_daily_enabled and not weekly_only
        do_ai_weekly = self.ai_weekly_enabled and do_weekly

        if self.digest_builder is not None and not ai_aborted:
            general_items, ai_items = _split_pools(items)

            if do_daily:
                pkg = self._try_digest(
                    general_items, kind="日贴", sources=GENERAL_INTEL_SOURCES,
                )
                if pkg is not None:
                    packages.append(pkg)
                if getattr(self.digest_builder, "aborted", False):
                    ai_aborted = True

            if do_ai_daily and not ai_aborted:
                pkg = self._try_digest(
                    ai_items,
                    kind="AI日贴",
                    sources={AI_INTEL_SOURCE},
                    max_items=self.max_items_ai_daily,
                )
                if pkg is not None:
                    packages.append(pkg)
                if getattr(self.digest_builder, "aborted", False):
                    ai_aborted = True

            week_general: list[IntelligenceItem] | None = None
            week_ai: list[IntelligenceItem] | None = None
            if (do_weekly or do_ai_weekly) and not ai_aborted:
                week_general, week_ai = self._weekly_pools(general_items, ai_items)

            if do_weekly and not ai_aborted and week_general is not None:
                pkg = self._try_digest(
                    week_general,
                    kind="周贴",
                    sources=GENERAL_INTEL_SOURCES,
                    max_items=self.max_items_weekly,
                )
                if pkg is not None:
                    packages.append(pkg)
                if getattr(self.digest_builder, "aborted", False):
                    ai_aborted = True

            if do_ai_weekly and not ai_aborted and week_ai is not None:
                pkg = self._try_digest(
                    week_ai,
                    kind="AI周贴",
                    sources={AI_INTEL_SOURCE},
                    max_items=self.max_items_ai_weekly,
                )
                if pkg is not None:
                    packages.append(pkg)
                if getattr(self.digest_builder, "aborted", False):
                    ai_aborted = True

            out_dir = Path(self.output_dir)
            for pkg in packages:
                paths = export_package(pkg, out_dir)
                export_paths.extend(str(p) for p in paths.values())
                log.info("Exported %s → %s", pkg.kind, paths.get("html"))

        digest_items = [p.to_item() for p in packages]
        all_for_push = list(items) + digest_items

        pushed = 0
        if dry_run or self.feishu is None:
            self._print_dry_run(items, packages, export_paths)
        else:
            to_push = [i for i in all_for_push if _is_pushable(i)]
            skipped = len(all_for_push) - len(to_push)
            if skipped:
                log.info("Skipping %d unscored/deleted items (not pushing)", skipped)
            if to_push:
                pushed = self.feishu.batch_create(to_push)
                log.info("Pushed %d records to Feishu", pushed)
            else:
                log.info("Nothing to push after filters")

        return PipelineResult(
            collected=len(collected),
            new_after_dedup=new_after_dedup,
            processed=len(items),
            scored=scored_count,
            rewritten=sum(1 for i in items if i.has_publish_content),
            digests=len(packages),
            pushed=pushed,
            ai_aborted=ai_aborted,
            items=items,
            digest_packages=packages,
            export_paths=export_paths,
        )

    def _try_digest(
        self,
        items: Sequence[IntelligenceItem],
        *,
        kind: str,
        sources: Collection[str] | None = None,
        max_items: int | None = None,
    ) -> DigestPackage | None:
        assert self.digest_builder is not None
        try:
            return self.digest_builder.build(
                items,
                kind=kind,
                max_items=max_items,
                sources=sources,
            )
        except Exception as exc:  # noqa: BLE001 — AIAuthError sets aborted
            if getattr(self.digest_builder, "aborted", False):
                log.error("%s digest aborted: %s", kind, exc)
            else:
                log.warning("%s digest failed: %s", kind, exc)
            return None

    def _weekly_pools(
        self,
        general_items: list[IntelligenceItem],
        ai_items: list[IntelligenceItem],
    ) -> tuple[list[IntelligenceItem], list[IntelligenceItem]]:
        """Merge Feishu lookback (when available) then split general vs AI."""
        week_pool = list(general_items) + list(ai_items)
        if self.feishu is not None:
            try:
                prior = self.feishu.list_scored_candidates(
                    days=self.weekly_lookback_days,
                    min_score=0.0,
                )
                week_pool = _merge_by_url(prior, week_pool)
            except Exception as exc:  # noqa: BLE001
                log.warning("Weekly candidate load failed: %s", exc)
        return _split_pools(week_pool)

    def _collect(self) -> list[IntelligenceItem]:
        all_items: list[IntelligenceItem] = []
        for collector in self.collectors:
            try:
                all_items.extend(collector.collect())
            except Exception as exc:  # noqa: BLE001
                log.error("Collector %s failed: %s", type(collector).__name__, exc)
        return all_items

    def _print_dry_run(
        self,
        items: list[IntelligenceItem],
        packages: list[DigestPackage],
        export_paths: list[str],
    ) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.out(f"=== Indie-Dev-Radar dry-run @ {now} ===")
        self.out(f"{len(items)} scored item(s) after dedup:\n")
        for i, it in enumerate(items, 1):
            score = f"{it.score:.0f}" if it.score is not None else "—"
            self.out(f"[{i}] ({score:>3}) {it.source} · {it.title}")
            if it.one_line_summary:
                self.out(f"     → {it.one_line_summary}")
            self.out(f"     {it.source_url}\n")

        if packages:
            self.out("--- 日贴 / 周贴 / AI 贴（可一键复制发布）---\n")
            for pkg in packages:
                self.out(f"【{pkg.kind}】{pkg.period_label} · {pkg.item_count} 条")
                self.out(f"  总标题: {pkg.recommended_title or pkg.package_title}")
                for plat, post in pkg.platform_posts.items():
                    t = (post.get("title") or "").strip()
                    b = (post.get("body") or "").strip()
                    preview = (b[:50] + "…") if len(b) > 50 else b
                    self.out(f"  [{plat}] {t or '（无标题）'}")
                    if preview:
                        self.out(f"       {preview}")
                self.out("")
        if export_paths:
            self.out("导出文件（浏览器打开 HTML 可一键复制）:")
            for p in export_paths:
                self.out(f"  {p}")
            self.out("")


def _merge_by_url(
    prior: list[IntelligenceItem],
    current: list[IntelligenceItem],
) -> list[IntelligenceItem]:
    best: dict[str, IntelligenceItem] = {}
    for it in list(prior) + list(current):
        if not it.source_url:
            continue
        prev = best.get(it.source_url)
        if prev is None or (it.score or 0) >= (prev.score or 0):
            best[it.source_url] = it
    return sorted(best.values(), key=lambda x: x.score or 0.0, reverse=True)


# --- construction --------------------------------------------------------

def build_pipeline(
    cfg: Config,
    *,
    client: httpx.Client | None = None,
) -> Pipeline:
    """Wire a real pipeline from config + environment secrets."""
    client = client or httpx.Client(timeout=cfg.ai.timeout_seconds)

    collectors: list[Collector] = []
    if cfg.sources.godot.enabled:
        collectors.append(GodotCollector(cfg.sources.godot, client))
    if cfg.sources.hackernews.enabled:
        collectors.append(HackerNewsCollector(cfg.sources.hackernews, client))
    if cfg.sources.github.enabled:
        collectors.append(GitHubCollector(
            cfg.sources.github, client, token=cfg.github_token,
        ))
    if cfg.sources.github_ai.enabled:
        collectors.append(GitHubCollector(
            cfg.sources.github_ai, client, token=cfg.github_token,
            source_label=AI_INTEL_SOURCE,
        ))

    scorer: Scorer | None = None
    rewriter: Rewriter | None = None
    digest_builder: DigestBuilder | None = None
    if cfg.ai_api_key:
        ai_client = AIClient(cfg.ai.base_url, cfg.ai_api_key, client=client,
                             timeout=cfg.ai.timeout_seconds)
        scorer = Scorer(
            cfg.ai.cheap_model, cfg.scoring.weights, ai_client,
            temperature=cfg.ai.temperature, timeout=cfg.ai.timeout_seconds,
        )
        if cfg.digest.rewrite_per_item:
            rewriter = Rewriter(
                cfg.ai.strong_model, cfg.scoring.score_threshold, ai_client,
                temperature=min(cfg.ai.temperature + 0.2, 1.0),
                timeout=cfg.ai.timeout_seconds,
            )
        digest_builder = DigestBuilder(
            cfg.ai.strong_model,
            ai_client,
            score_threshold=cfg.scoring.score_threshold,
            max_items=cfg.digest.max_items_daily,
            temperature=min(cfg.ai.temperature + 0.2, 1.0),
            timeout=max(cfg.ai.timeout_seconds, 90),
        )
    else:
        log.warning("AI_API_KEY not set — scoring/digests disabled")

    feishu: FeishuClient | None = None
    if cfg.feishu_app_id and cfg.feishu_app_secret and cfg.feishu.app_token and cfg.feishu.table_id:
        feishu = FeishuClient(
            cfg.feishu_app_id, cfg.feishu_app_secret,
            cfg.feishu.app_token, cfg.feishu.table_id,
            client=client,
            dedup_lookback_days=cfg.feishu.dedup_lookback_days,
        )
    else:
        log.warning("Feishu credentials incomplete — push disabled (dry-run mode implied)")

    return Pipeline(
        collectors=collectors,
        scorer=scorer,
        rewriter=rewriter,
        digest_builder=digest_builder,
        feishu=feishu,
        daily_enabled=cfg.digest.daily_enabled,
        weekly_enabled=cfg.digest.weekly_enabled,
        ai_daily_enabled=cfg.digest.ai_daily_enabled,
        ai_weekly_enabled=cfg.digest.ai_weekly_enabled,
        weekly_lookback_days=cfg.digest.weekly_lookback_days,
        max_items_weekly=cfg.digest.max_items_weekly,
        max_items_ai_daily=cfg.digest.max_items_ai_daily,
        max_items_ai_weekly=cfg.digest.max_items_ai_weekly,
        output_dir=cfg.digest.output_dir,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Indie-Dev-Radar pipeline")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="collect + score + digests + export, do not push Feishu")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap the number of items processed (overrides config max_items_per_run)")
    parser.add_argument("--weekly", action="store_true",
                        help="also generate 周贴 / AI周贴 (uses Feishu history when available)")
    parser.add_argument("--weekly-only", action="store_true",
                        help="with --weekly: skip 日贴/AI日贴 (Friday afternoon cron)")
    parser.add_argument("--clear", action="store_true",
                        help="delete all Feishu records before running (reset the table)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (TypeError, ValueError):
                pass

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    client = httpx.Client(timeout=cfg.ai.timeout_seconds)
    try:
        pipeline = build_pipeline(cfg, client=client)
        if args.weekly:
            pipeline.weekly_enabled = True
        if args.weekly_only and not args.weekly:
            log.warning("--weekly-only without --weekly: enabling weekly digests")
            pipeline.weekly_enabled = True
        if args.clear:
            if pipeline.feishu is None:
                log.warning("--clear ignored: Feishu not configured")
            else:
                log.info("Clearing all Feishu records (--clear)")
                pipeline.feishu.clear_all()
        dry_run = args.dry_run or pipeline.feishu is None
        limit = args.limit if args.limit is not None else cfg.max_items_per_run
        result = pipeline.run(
            dry_run=dry_run,
            limit=limit,
            weekly=True if (args.weekly or args.weekly_only) else None,
            weekly_only=bool(args.weekly_only),
        )
        log.info(
            "Done: new=%d processed=%d scored=%d digests=%d pushed=%d aborted=%s",
            result.new_after_dedup, result.processed, result.scored,
            result.digests, result.pushed, result.ai_aborted,
        )
        if result.export_paths:
            log.info("Open HTML for one-click copy: %s",
                     next((p for p in result.export_paths if p.endswith(".html")), result.export_paths[0]))
        if result.ai_aborted:
            log.error("Exiting with code 1 due to AI failure/abort")
            return 1
        collectors = getattr(pipeline, "collectors", None) or []
        if not result.collected and collectors:
            log.error("Exiting with code 1: all collectors returned nothing / failed")
            return 1
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
