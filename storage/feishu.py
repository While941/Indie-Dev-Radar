"""Feishu (Lark) Bitable client.

Pushes scored ``IntelligenceItem``s into a multidimensional table and reads
back already-seen source URLs for de-duplication. Uses tenant_access_token
auth (cached ~2h). Endpoints:

- POST /auth/v3/tenant_access_token/internal
- GET  /bitable/v1/apps/{app_token}/tables/{table_id}/records
- POST /bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create

The field mapping (``item_to_fields``) uses the Chinese field names from the
schema in the plan. Date fields are sent as millisecond epochs; missing values
are omitted so Feishu's strict field typing does not reject the row.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from models.item import IntelligenceItem

from .feishu_schema import (  # shared schema (single source of truth)
    DIMENSION_LABELS,
    DIMENSIONS_FIELD,
    DRAFT_FIELDS,
    URL_FIELD,
)

log = logging.getLogger(__name__)

BASE_URL = "https://open.feishu.cn/open-apis"
BATCH_SIZE = 500               # Feishu batch_create cap
FETCHED_AT_FIELD = "抓取时间"


def _to_ms(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    return int(dt.timestamp() * 1000)


def _extract_url(value: Any) -> str:
    """Normalise a Feishu field value into a URL string.

    Feishu returns text/url fields variously as a plain string, a single
    segment dict (``{text, link}``), or a list of segments — depending on field
    type. Handle all of them so dedup is robust.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("link") or value.get("text") or "").strip()
    if isinstance(value, list):
        for seg in value:
            if isinstance(seg, dict):
                got = seg.get("link") or seg.get("text")
                if got:
                    return str(got).strip()
            elif isinstance(seg, str) and seg.strip():
                return seg.strip()
        return ""
    return str(value).strip()


def _parse_feishu_ms(value: Any) -> datetime | None:
    """Parse a Feishu date/datetime field into an aware UTC datetime."""
    if value is None or value == "":
        return None
    try:
        # Feishu returns ms epoch as int/float; sometimes as digit string.
        ms = int(float(value))
        # Heuristic: seconds vs milliseconds
        if ms < 1_000_000_000_000:
            ms *= 1000
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def format_dimensions(dimensions: dict[str, float]) -> str:
    """Render dimension scores as a short Chinese label string for Feishu."""
    if not dimensions:
        return ""
    parts: list[str] = []
    for key, label in DIMENSION_LABELS.items():
        if key not in dimensions:
            continue
        val = dimensions[key]
        # Prefer integer display when whole number
        if isinstance(val, float) and val == int(val):
            parts.append(f"{label}:{int(val)}")
        else:
            parts.append(f"{label}:{val}")
    return " ".join(parts)


def item_to_fields(item: IntelligenceItem) -> dict[str, Any]:
    """Map an ``IntelligenceItem`` to a Feishu ``fields`` payload."""
    fields: dict[str, Any] = {
        "来源": item.source,
        "原始链接": item.source_url,
        "标题": item.title,
        "风险等级": item.risk_level,
        "推荐动作": item.recommended_action or "待审核",
    }
    if item.summary_raw:
        fields["原始摘要"] = item.summary_raw
    if item.author:
        fields["作者"] = item.author
    if item.category:
        fields["分类"] = item.category
    if item.tags:
        fields["标签"] = list(item.tags)
    if item.score is not None:
        fields["AI 评分"] = item.score
    dims_text = format_dimensions(item.dimensions)
    if dims_text:
        fields[DIMENSIONS_FIELD] = dims_text
    if item.one_line_summary:
        fields["一句话总结"] = item.one_line_summary
    if item.target_audience:
        fields["适合人群"] = item.target_audience
    if item.recommended_platforms:
        fields["推荐发布平台"] = list(item.recommended_platforms)
    if item.recommended_title:
        fields["推荐标题"] = item.recommended_title
    for label, field_name in DRAFT_FIELDS.items():
        draft = item.drafts.get(label)
        if draft:
            fields[field_name] = draft

    published_ms = _to_ms(item.published_at)
    if published_ms is not None:
        fields["发布时间"] = published_ms
    fetched_ms = _to_ms(item.fetched_at)
    if fetched_ms is not None:
        fields["抓取时间"] = fetched_ms
    return fields


class FeishuAuth:
    """Shared tenant_access_token auth for Feishu read/write and setup."""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        client: httpx.Client | None = None,
        base_url: str = BASE_URL,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._client = client or httpx.Client(timeout=30.0)
        self._base_url = base_url.rstrip("/")
        self._token: str | None = None
        self._token_expires: float = 0.0

    # --- auth -------------------------------------------------------------

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        resp = self._client.post(
            f"{self._base_url}/auth/v3/tenant_access_token/internal",
            json={"app_id": self._app_id, "app_secret": self._app_secret},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu token error: {data.get('code')} {data.get('msg')}")
        self._token = data["tenant_access_token"]
        self._token_expires = time.time() + int(data.get("expire", 7200))
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }


class FeishuClient(FeishuAuth):
    """Read/write client for a specific Bitable table."""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        app_token: str,
        table_id: str,
        *,
        client: httpx.Client | None = None,
        base_url: str = BASE_URL,
        dedup_lookback_days: int = 14,
        now: datetime | None = None,
    ) -> None:
        super().__init__(app_id, app_secret, client=client, base_url=base_url)
        self._app_token = app_token
        self._table_id = table_id
        self._dedup_lookback_days = max(0, int(dedup_lookback_days))
        self._now = now

    @property
    def _records_path(self) -> str:
        return f"{self._base_url}/bitable/v1/apps/{self._app_token}/tables/{self._table_id}/records"

    # --- read for dedup ---------------------------------------------------

    def _iter_records(self, *, page_size: int = 500, max_pages: int = 100) -> Iterator[dict]:
        """Yield every record dict in the table, paginating safely."""
        page_token: str | None = None
        pages = 0
        while pages < max_pages:
            pages += 1
            params: dict[str, Any] = {"page_size": str(page_size)}
            if page_token:
                params["page_token"] = page_token
            resp = self._client.get(self._records_path, headers=self._headers(), params=params)
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") != 0:
                raise RuntimeError(
                    f"Feishu list records error: {body.get('code')} {body.get('msg')}"
                )
            data = body.get("data", {}) or {}
            for record in data.get("items", []) or []:
                yield record
            page_token = data.get("page_token") or None
            # Stop when there is no next page, or Feishu gave has_more without a
            # usable page_token (which would otherwise loop forever).
            if not data.get("has_more") or not page_token:
                break
        if pages >= max_pages:
            log.warning("Feishu _iter_records hit max_pages=%d cap", max_pages)

    def _within_lookback(self, fields: dict[str, Any], cutoff: datetime | None) -> bool:
        """True if the record should count as seen for dedup.

        - No lookback configured (days == 0): all records count.
        - Missing 抓取时间: still count as seen (conservative).
        - With timestamp: only if >= cutoff.
        """
        if cutoff is None:
            return True
        fetched = _parse_feishu_ms(fields.get(FETCHED_AT_FIELD))
        if fetched is None:
            return True
        return fetched >= cutoff

    def list_source_urls(self, *, url_field: str = URL_FIELD) -> set[str]:
        """Return source URLs already present within the lookback window."""
        now = self._now or datetime.now(timezone.utc)
        cutoff: datetime | None = None
        if self._dedup_lookback_days > 0:
            cutoff = now - timedelta(days=self._dedup_lookback_days)

        urls: set[str] = set()
        for rec in self._iter_records():
            fields = rec.get("fields") or {}
            if not self._within_lookback(fields, cutoff):
                continue
            url = _extract_url(fields.get(url_field))
            if url:
                urls.add(url)
        log.info(
            "Feishu: %d existing source URLs (lookback_days=%s)",
            len(urls), self._dedup_lookback_days or "all",
        )
        return urls

    def clear_all(self) -> int:
        """Delete every record in the table. Returns the count deleted."""
        ids = [rec.get("record_id") for rec in self._iter_records() if rec.get("record_id")]
        deleted = 0
        for start in range(0, len(ids), BATCH_SIZE):
            chunk = ids[start:start + BATCH_SIZE]
            resp = self._client.post(
                f"{self._records_path}/batch_delete",
                headers=self._headers(),
                json={"records": chunk},
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") != 0:
                raise RuntimeError(
                    f"Feishu batch_delete error: {body.get('code')} {body.get('msg')}"
                )
            deleted += len(chunk)
        log.info("Feishu: cleared %d records", deleted)
        return deleted

    # --- write ------------------------------------------------------------

    def batch_create(self, items: Iterable[IntelligenceItem]) -> int:
        records = [item_to_fields(it) for it in items]
        created = 0
        for start in range(0, len(records), BATCH_SIZE):
            chunk = records[start:start + BATCH_SIZE]
            resp = self._client.post(
                f"{self._records_path}/batch_create",
                headers=self._headers(),
                json={"records": [{"fields": f} for f in chunk]},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Feishu batch_create error: {data.get('code')} {data.get('msg')}")
            created += len((data.get("data") or {}).get("records") or [])
        log.info("Feishu: created %d records", created)
        return created
