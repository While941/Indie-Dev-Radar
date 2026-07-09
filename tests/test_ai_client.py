"""Tests for analysis.ai_client — OpenAI-compatible chat/completions client."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from analysis.ai_client import AIClient

URL = "https://api.test/v1/chat/completions"


def _client() -> AIClient:
    return AIClient("https://api.test/v1/", "sk_key", client=httpx.Client(), timeout=10.0)


@respx.mock
def test_chat_json_posts_payload_and_parses_content() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": '{"relevance": 8}'}}],
        })

    respx.post(URL).mock(side_effect=handler)
    data = _client().chat_json(model="cheap", system="s", user="u", temperature=0.1)

    assert data == {"relevance": 8}
    assert captured["body"]["model"] == "cheap"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["messages"][0]["role"] == "system"
    assert captured["headers"]["authorization"] == "Bearer sk_key"


@respx.mock
def test_chat_json_strips_markdown_fences() -> None:
    # Some gateways ignore response_format and wrap JSON in ```json fences.
    respx.post(URL).respond(200, json={
        "choices": [{"message": {"content": "```json\n{\"ok\": 1}\n```"}}],
    })
    assert _client().chat_json(model="m", system="s", user="u") == {"ok": 1}


@respx.mock
def test_chat_json_extracts_json_from_prose() -> None:
    respx.post(URL).respond(200, json={
        "choices": [{"message": {"content": "Sure! Here you go:\n{\"ok\": 2}\nHope that helps."}}],
    })
    assert _client().chat_json(model="m", system="s", user="u") == {"ok": 2}


def test_requires_non_empty_api_key() -> None:
    with pytest.raises(ValueError):
        AIClient("https://api.test/v1/", "")


def test_requires_non_empty_base_url() -> None:
    with pytest.raises(ValueError):
        AIClient("", "sk_key")


def test_appends_chat_completions_to_base_url() -> None:
    c = AIClient("https://open.bigmodel.cn/api/paas/v4/", "k")
    assert c._url == "https://open.bigmodel.cn/api/paas/v4/chat/completions"


@respx.mock
def test_raises_on_empty_content() -> None:
    respx.post(URL).respond(200, json={"choices": [{"message": {"content": ""}}]})
    with pytest.raises(ValueError):
        _client().chat_json(model="m", system="s", user="u")


@respx.mock
def test_raises_on_non_json_content() -> None:
    respx.post(URL).respond(200, json={
        "choices": [{"message": {"content": "this is not json"}}],
    })
    with pytest.raises(ValueError):
        _client().chat_json(model="m", system="s", user="u")


@respx.mock
def test_raises_auth_error_on_401() -> None:
    from analysis.ai_client import AIAuthError
    respx.post(URL).respond(401, json={"error": "bad key"})
    with pytest.raises(AIAuthError):
        _client().chat_json(model="m", system="s", user="u")


@respx.mock
def test_retries_on_429_then_succeeds() -> None:
    sleeps: list[float] = []
    route = respx.post(URL)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0.01"}, json={"error": "rate"}),
        httpx.Response(200, json={
            "choices": [{"message": {"content": '{"ok": 1}'}}],
        }),
    ]
    client = AIClient(
        "https://api.test/v1/", "sk_key", client=httpx.Client(), timeout=10.0,
        retries=3, sleep=sleeps.append,
    )
    assert client.chat_json(model="m", system="s", user="u") == {"ok": 1}
    assert len(sleeps) == 1
    assert route.call_count == 2
