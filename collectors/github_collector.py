"""GitHub collector (Search API).

    GET https://api.github.com/search/repositories?q=...&sort=stars&order=desc
        -> {"items": [{full_name, html_url, description, owner, ...}, ...]}

Auth via ``GH_TOKEN`` lifts the search rate limit from 10/min to 30/min and the
core limit to 5000/h. A ``User-Agent`` is required by the API.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from config import GitHubSourceConfig
from models.item import IntelligenceItem

from .base import Collector, USER_AGENT, get_json, parse_iso

log = logging.getLogger(__name__)

BASE_URL = "https://api.github.com"


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

    def _build_query(self) -> str:
        cutoff = (self._now - timedelta(days=self._cfg.pushed_within_days)).date().isoformat()
        return f"{self._cfg.query} pushed:>{cutoff} stars:>{self._cfg.min_stars}"

    def collect(self) -> list[IntelligenceItem]:
        params = {
            "q": self._build_query(),
            "sort": "stars",
            "order": "desc",
            "per_page": str(self._cfg.per_page),
        }
        data = get_json(self._client, f"{self._base_url}/search/repositories",
                        params=params, headers=self._headers())
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
        log.info("%s: collected %d items", self._source_label, len(items))
        return items

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

        return IntelligenceItem(
            source=source,
            source_url=html_url,
            title=repo.get("full_name") or repo.get("name") or html_url,
            summary_raw=(repo.get("description") or "").strip(),
            author=owner.get("login"),
            published_at=parse_iso(repo.get("pushed_at")),
            fetched_at=fetched_at,
            score_raw={
                "stars": repo.get("stargazers_count"),
                "forks": repo.get("forks_count"),
                "language": repo.get("language"),
                "topics": topics_tuple,
                "homepage": repo.get("homepage"),
            },
        )
