"""Pipeline orchestration: collect -> dedup -> score -> rewrite -> push.

Construction (env/config dependent) is separated from orchestration logic so
the ``Pipeline.run`` flow can be unit-tested with fakes. ``main`` wires a real
pipeline from config + environment for CLI / GitHub Actions use.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from analysis.ai_client import AIClient
from analysis.rewriter import Rewriter
from analysis.scorer import Scorer
from collectors import Collector, GitHubCollector, GodotCollector, HackerNewsCollector
from config import Config, load_config
from models.item import IntelligenceItem
from storage.dedup import filter_new
from storage.feishu import FeishuClient

log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    collected: int = 0
    new_after_dedup: int = 0
    processed: int = 0
    scored: int = 0
    rewritten: int = 0
    pushed: int = 0
    ai_aborted: bool = False
    items: list[IntelligenceItem] = field(default_factory=list, repr=False)


def _is_pushable(item: IntelligenceItem) -> bool:
    """Only scored items that are not model-deleted belong in the review queue."""
    if not item.is_scored:
        return False
    if item.recommended_action == "删除":
        return False
    return True


@dataclass
class Pipeline:
    """Orchestrates the flow. Components are duck-typed for easy testing."""

    collectors: list[Collector]
    scorer: Scorer | None = None
    rewriter: Rewriter | None = None
    feishu: FeishuClient | None = None
    out: Callable[[str], None] = print

    def run(self, *, dry_run: bool = False, limit: int | None = None) -> PipelineResult:
        collected = self._collect()

        # Always read existing URLs when Feishu is configured (including dry-run)
        # so operators see the true "new today" set. Writes are skipped on dry-run.
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
        # AI configured but nothing scored for a non-empty batch (e.g. all 5xx).
        if self.scorer is not None and items and scored_count == 0:
            ai_aborted = True

        pushed = 0
        if dry_run or self.feishu is None:
            self._print_dry_run(items)
        else:
            to_push = [i for i in items if _is_pushable(i)]
            skipped = len(items) - len(to_push)
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
            pushed=pushed,
            ai_aborted=ai_aborted,
            items=items,
        )

    def _collect(self) -> list[IntelligenceItem]:
        all_items: list[IntelligenceItem] = []
        for collector in self.collectors:
            try:
                all_items.extend(collector.collect())
            except Exception as exc:  # noqa: BLE001 - one source failing must not abort
                log.error("Collector %s failed: %s", type(collector).__name__, exc)
        return all_items

    def _print_dry_run(self, items: list[IntelligenceItem]) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.out(f"=== Indie-Dev-Radar dry-run @ {now} ===")
        self.out(f"{len(items)} item(s) after dedup:\n")
        for i, it in enumerate(items, 1):
            score = f"{it.score:.0f}" if it.score is not None else "—"
            self.out(f"[{i}] ({score:>3}) {it.source} · {it.title}")
            if it.one_line_summary:
                self.out(f"     → {it.one_line_summary}")
            if it.recommended_action:
                self.out(f"     action: {it.recommended_action}")
            if it.platform_posts:
                for plat, post in it.platform_posts.items():
                    t = (post.get("title") or "").strip()
                    b = (post.get("body") or "").strip()
                    preview = (b[:40] + "…") if len(b) > 40 else b
                    self.out(f"     [{plat}] {t or '（无标题）'} | {preview or '（无正文）'}")
            elif it.drafts:
                self.out(f"     drafts: {', '.join(it.drafts)}")
            self.out(f"     {it.source_url}\n")


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
        collectors.append(GitHubCollector(cfg.sources.github, client, token=cfg.github_token))

    ai_client: AIClient | None = None
    scorer: Scorer | None = None
    rewriter: Rewriter | None = None
    if cfg.ai_api_key:
        ai_client = AIClient(cfg.ai.base_url, cfg.ai_api_key, client=client,
                             timeout=cfg.ai.timeout_seconds)
        scorer = Scorer(cfg.ai.cheap_model, cfg.scoring.weights, ai_client,
                        temperature=cfg.ai.temperature, timeout=cfg.ai.timeout_seconds)
        rewriter = Rewriter(cfg.ai.strong_model, cfg.scoring.score_threshold, ai_client,
                            temperature=min(cfg.ai.temperature + 0.2, 1.0),
                            timeout=cfg.ai.timeout_seconds)
    else:
        log.warning("AI_API_KEY not set — scoring/rewriting disabled")

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

    return Pipeline(collectors=collectors, scorer=scorer, rewriter=rewriter, feishu=feishu)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Indie-Dev-Radar pipeline")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="collect + score + print, do not push to Feishu")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap the number of items processed (overrides config max_items_per_run)")
    parser.add_argument("--clear", action="store_true",
                        help="delete all Feishu records before running (reset the table)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    # Ensure unicode renders on Windows consoles (titles/summaries are Chinese).
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
        if args.clear:
            if pipeline.feishu is None:
                log.warning("--clear ignored: Feishu not configured")
            else:
                log.info("Clearing all Feishu records (--clear)")
                pipeline.feishu.clear_all()
        dry_run = args.dry_run or pipeline.feishu is None
        limit = args.limit if args.limit is not None else cfg.max_items_per_run
        result = pipeline.run(dry_run=dry_run, limit=limit)
        log.info(
            "Done: new=%d processed=%d scored=%d rewritten=%d pushed=%d aborted=%s",
            result.new_after_dedup, result.processed, result.scored,
            result.rewritten, result.pushed, result.ai_aborted,
        )
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
