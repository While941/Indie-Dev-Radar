"""Common collector infrastructure.

- ``Collector``: the abstract base every source collector implements.
- ``get_json``: an HTTP GET helper with bounded retry/backoff for transient
  failures (429 / 5xx), honouring ``Retry-After``. Returns parsed JSON or
  raises ``httpx.HTTPStatusError`` for non-retryable/exhausted errors.
- timestamp parsing helpers shared across sources.

Collectors receive an injected ``httpx.Client`` so tests can drive them with
``respx`` or a fake transport.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

import httpx

from models.item import IntelligenceItem

log = logging.getLogger(__name__)

# Status codes worth retrying with backoff.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Polite, identifiable UA for public APIs that require one (e.g. GitHub).
USER_AGENT = "Indie-Dev-Radar/0.1 (indie-game intel bot; +https://github.com/)"


class Collector(ABC):
    """A source-specific collector producing normalised ``IntelligenceItem``s."""

    @abstractmethod
    def collect(self) -> list[IntelligenceItem]:
        """Return collected items. Should never raise on a single bad row —
        log and skip, surfacing only hard transport errors."""
        raise NotImplementedError


def parse_epoch(value: Any) -> datetime | None:
    """Parse a Unix epoch (seconds) into an aware UTC datetime, or None."""
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp (e.g. GitHub's ``2026-06-01T12:00:00Z``)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _backoff_delay(response: httpx.Response, base: float, attempt: int) -> float:
    """Compute a delay in seconds, honouring ``Retry-After`` when present."""
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), 30.0)
        except ValueError:
            pass
    return min(base * (2 ** attempt), 30.0)


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    retries: int = 3,
    base_backoff: float = 0.5,
    timeout: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """GET ``url`` and return parsed JSON with bounded retry/backoff.

    Retries 429/5xx up to ``retries`` times (exponential backoff, capped 30s,
    honouring ``Retry-After``). Any other 4xx, or retryable errors after the
    final attempt, propagate as ``httpx.HTTPStatusError``.
    """
    response: httpx.Response | None = None
    for attempt in range(max(1, retries)):
        response = client.get(url, params=params, headers=headers, timeout=timeout)
        if response.status_code < 400:
            return response.json()
        if response.status_code in RETRYABLE_STATUS:
            if attempt < retries - 1:
                delay = _backoff_delay(response, base_backoff, attempt)
                log.warning(
                    "GET %s -> %s; retry %d/%d in %.2fs",
                    url, response.status_code, attempt + 1, retries - 1, delay,
                )
                sleep(delay)
                continue
            break  # exhausted retries on a retryable status
        # non-retryable error
        response.raise_for_status()
    raise RuntimeError(
        f"GET {url} failed after {retries} attempts "
        f"(last status {response.status_code if response else 'n/a'})"
    )
