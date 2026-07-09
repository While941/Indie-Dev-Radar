"""GitHub collector (Search API) with multi-path discovery.

Paths (configurable via ``path_sorts`` / ``created_within_days``):

- ``stars`` / ``updated``: keyword + recent push + min_stars (same query family)
- ``created``: keyword + recently created repos (orthogonal family)

Results merge by URL; orthogonal multi-path hits raise path_corroboration.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx

from config import GitHubSourceConfig
from models.item import IntelligenceItem

from .base import Collector, USER_AGENT, get_json, parse_iso
from .multi_path import collect_paths, dedupe_strs

BASE_URL = "https://api.github.com"
DEFAULT_PATH_SORTS = ("stars", "updated")
QueryMode = Literal["pushed", "created"]


class GitHubCollector(Collector):
    def __init__(
        self,
        cfg: GitHubSourceConfig,
        client: httpx.Client,
        token: str = "",
        *,
        base_url: str = BASE_URL,
        now: datetime | None = None,
        source_label: str = "GitHub",
    ) -> None:
        self._cfg = cfg
        self._client = client
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._now = now or datetime.now(timezone.utc)
        self._source_label = source_label or "GitHub"

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _path_sorts(self) -> list[str]:
        sorts = list(self._cfg.path_sorts) if self._cfg.path_sorts else list(DEFAULT_PATH_SORTS)
        return dedupe_strs(sorts) or list(DEFAULT_PATH_SORTS)

    def _build_query(self, *, mode: QueryMode = "pushed") -> str:
        if mode == "created":
            days = self._cfg.created_within_days or self._cfg.pushed_within_days
            cutoff = (self._now - timedelta(days=max(1, days))).date().isoformat()
            return f"{self._cfg.query} created:>{cutoff} stars:>{self._cfg.min_stars}"
        days = self._cfg.pushed_within_days
        cutoff = (self._now - timedelta(days=max(1, days))).date().isoformat()
        return f"{self._cfg.query} pushed:>{cutoff} stars:>{self._cfg.min_stars}"

    def _search(self, *, sort: str, mode: QueryMode = "pushed") -> list[IntelligenceItem]:
        params = {
            "q": self._build_query(mode=mode),
            "sort": sort,
            "order": "desc",
            "per_page": str(self._cfg.per_page),
        }
        data = get_json(
            self._client,
            f"{self._base_url}/search/repositories",
            params=params,
            headers=self._headers(),
        )
        repos = data.get("items", []) if isinstance(data, dict) else []
        if not isinstance(repos, list):
            return []

        items: list[IntelligenceItem] = []
        for repo in repos:
            if not isinstance(repo, dict):
                continue
            item = self._to_item(repo, self._now, source=self._source_label)
            if item is not None:
                items.append(item)
        return items

    def collect(self) -> list[IntelligenceItem]:
        fetchers: dict[str, Callable[[], Sequence[IntelligenceItem]]] = {
            f"gh_{sort}": (lambda s=sort: self._search(sort=s, mode="pushed"))
            for sort in self._path_sorts()
        }
        if self._cfg.created_within_days > 0:
            fetchers["gh_created"] = lambda: self._search(sort="stars", mode="created")

        return collect_paths(
            fetchers,
            now=self._now,
            max_age_days=self._cfg.max_age_days,
            freshness_horizon_days=self._cfg.freshness_horizon_days,
            log_label=self._source_label,
        )

    @staticmethod
    def _to_item(
        repo: dict,
        fetched_at: datetime,
        *,
        source: str = "GitHub",
    ) -> IntelligenceItem | None:
        html_url = repo.get("html_url")
        if not html_url:
            return None

        owner = repo.get("owner") if isinstance(repo.get("owner"), dict) else {}
        topics = repo.get("topics")
        topics_tuple = tuple(topics) if isinstance(topics, list) else ()
        published = parse_iso(repo.get("pushed_at")) or parse_iso(repo.get("updated_at"))

        return IntelligenceItem(
            source=source,
            source_url=html_url,
            title=repo.get("full_name") or repo.get("name") or html_url,
            summary_raw=(repo.get("description") or "").strip(),
            author=owner.get("login"),
            published_at=published,
            fetched_at=fetched_at,
            score_raw={
                "stars": repo.get("stargazers_count"),
                "forks": repo.get("forks_count"),
                "language": repo.get("language"),
                "topics": topics_tuple,
                "homepage": repo.get("homepage"),
                "created_at": repo.get("created_at"),
            },
        )
