"""Provider-agnostic chat client (OpenAI-compatible /chat/completions).

Works with any OpenAI-compatible endpoint (DeepSeek, Zhipu GLM, OpenAI, ...).
The base_url from config points at the API root (e.g.
``https://api.deepseek.com``); we append ``chat/completions``.

Forces JSON object output via ``response_format`` so downstream parsing is
reliable. The client is injected so tests can stub it.

Retries 429/5xx with bounded backoff. Auth failures (401/403) raise
``AIAuthError`` immediately so callers can circuit-break the batch.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Protocol

import httpx

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
AUTH_STATUS = frozenset({401, 403})


class AIAuthError(RuntimeError):
    """Raised on AI provider auth failures (401/403). Not retryable."""


class ChatClient(Protocol):
    """Minimal interface Scorer/Rewriter depend on (duck-typed)."""

    def chat_json(
        self, *, model: str, system: str, user: str,
        temperature: float, timeout: float,
    ) -> dict[str, Any]:
        ...


class AIClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 60.0,
        retries: int = 3,
        base_backoff: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("AIClient requires a non-empty API key")
        if not base_url:
            raise ValueError("AIClient requires a non-empty base_url")
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=timeout)
        self._timeout = timeout
        self._retries = max(1, retries)
        self._base_backoff = base_backoff
        self._sleep = sleep

    def chat_json(
        self, *, model: str, system: str, user: str,
        temperature: float = 0.2, timeout: float | None = None,
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        req_timeout = timeout if timeout is not None else self._timeout
        response: httpx.Response | None = None

        for attempt in range(self._retries):
            response = self._client.post(
                self._url, headers=headers, json=payload, timeout=req_timeout,
            )
            status = response.status_code
            if status < 400:
                data = response.json()
                content = _extract_content(data)
                return _parse_content_json(content)

            if status in AUTH_STATUS:
                raise AIAuthError(
                    f"AI auth failed ({status}) for {self._url}: "
                    f"{response.text[:200]}"
                )

            if status in RETRYABLE_STATUS and attempt < self._retries - 1:
                delay = _backoff_delay(response, self._base_backoff, attempt)
                log.warning(
                    "AI POST %s -> %s; retry %d/%d in %.2fs",
                    self._url, status, attempt + 1, self._retries - 1, delay,
                )
                self._sleep(delay)
                continue

            response.raise_for_status()

        # Exhausted retries on a retryable status
        if response is not None:
            response.raise_for_status()
        raise RuntimeError(f"AI POST {self._url} failed after {self._retries} attempts")


def _backoff_delay(response: httpx.Response, base: float, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), 30.0)
        except ValueError:
            pass
    return min(base * (2 ** attempt), 30.0)


def _extract_content(data: dict[str, Any]) -> str:
    """Pull the assistant message text out of a chat-completions response."""
    choices = data.get("choices") or [{}]
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content")
    if not content:
        raise ValueError(f"Empty model content: {data!r}")
    return content


def _parse_content_json(content: str) -> dict[str, Any]:
    """Parse a JSON object from model content.

    Tolerates models/gateways that ignore ``response_format: json_object`` and
    wrap output in markdown code fences or surround it with prose.
    """
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].lstrip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start:end + 1])
            except json.JSONDecodeError as exc:
                raise ValueError(f"Model did not return valid JSON: {content[:200]!r}") from exc
        else:
            raise ValueError(f"Model did not return valid JSON: {content[:200]!r}")
    if not isinstance(parsed, dict):
        raise ValueError(f"Model JSON is not an object: {parsed!r}")
    return parsed
