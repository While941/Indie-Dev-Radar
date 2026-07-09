"""Shared test fixtures."""
from __future__ import annotations

from typing import Any


class FakeChatClient:
    """Stand-in for AIClient: returns queued JSON dicts (or raises queued errors)."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat_json(self, *, model: str, system: str, user: str,
                  temperature: float, timeout: float) -> dict[str, Any]:
        self.calls.append({"model": model, "system": system, "user": user,
                           "temperature": temperature})
        if not self._responses:
            raise AssertionError("FakeChatClient response queue exhausted")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt
