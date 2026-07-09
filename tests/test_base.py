"""Tests for collectors.base — get_json retry/backoff + timestamp helpers."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from collectors.base import get_json, parse_epoch, parse_iso


# --- timestamp helpers -----------------------------------------------------

def test_parse_epoch_valid() -> None:
    dt = parse_epoch(1700000000)
    assert dt == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_parse_epoch_invalid_returns_none() -> None:
    assert parse_epoch("not-a-number") is None
    assert parse_epoch(None) is None


def test_parse_iso_with_z_suffix() -> None:
    dt = parse_iso("2026-06-01T12:00:00Z")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.year == 2026 and dt.month == 6


def test_parse_iso_invalid_returns_none() -> None:
    assert parse_iso("") is None
    assert parse_iso(None) is None
    assert parse_iso("nope") is None


# --- get_json --------------------------------------------------------------

@respx.mock
def test_get_json_success_first_try() -> None:
    respx.get("https://api.test/x").respond(200, json={"ok": True})
    with httpx.Client() as client:
        data = get_json(client, "https://api.test/x", sleep=lambda _: None)
    assert data == {"ok": True}


@respx.mock
def test_get_json_retries_on_503_then_succeeds() -> None:
    route = respx.get("https://api.test/x").mock(side_effect=[
        httpx.Response(503),
        httpx.Response(200, json={"ok": True}),
    ])
    with httpx.Client() as client:
        data = get_json(client, "https://api.test/x", retries=3,
                        base_backoff=0.0, sleep=lambda _: None)
    assert data == {"ok": True}
    assert route.call_count == 2


@respx.mock
def test_get_json_honours_retry_after_header() -> None:
    delays: list[float] = []
    route = respx.get("https://api.test/x").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, json={"ok": True}),
    ])
    with httpx.Client() as client:
        get_json(client, "https://api.test/x", retries=3, sleep=delays.append)
    assert route.call_count == 2
    assert delays == [7.0]


@respx.mock
def test_get_json_exhausts_retries_then_raises() -> None:
    route = respx.get("https://api.test/x").mock(return_value=httpx.Response(503))
    with httpx.Client() as client, pytest.raises(Exception):
        get_json(client, "https://api.test/x", retries=2,
                 base_backoff=0.0, sleep=lambda _: None)
    assert route.call_count == 2


@respx.mock
def test_get_json_non_retryable_raises_immediately() -> None:
    route = respx.get("https://api.test/x").respond(404, json={"message": "nope"})
    with httpx.Client() as client, pytest.raises(httpx.HTTPStatusError):
        get_json(client, "https://api.test/x", retries=3, sleep=lambda _: None)
    assert route.call_count == 1  # no retry on 404
