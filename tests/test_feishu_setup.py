"""Tests for storage.feishu_setup — provisioning flow via respx."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from storage.feishu_schema import FIELDS, PRIMARY_FIELD
from storage.feishu_setup import FeishuSetup

BASE = "https://open.feishu.cn/open-apis"
TOKEN_URL = f"{BASE}/auth/v3/tenant_access_token/internal"
APPS_URL = f"{BASE}/bitable/v1/apps"
TABLES_URL = f"{BASE}/bitable/v1/apps/appT/tables"
FIELDS_URL = f"{BASE}/bitable/v1/apps/appT/tables/tblT/fields"


def _setup() -> FeishuSetup:
    return FeishuSetup("id", "secret", client=httpx.Client())


@respx.mock
def test_create_app_returns_app_token() -> None:
    respx.post(TOKEN_URL).respond(200, json={"code": 0, "tenant_access_token": "t", "expire": 7200})
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "data": {"app": {"app_token": "appT"}}})

    respx.post(APPS_URL).mock(side_effect=handler)
    assert _setup().create_app("情报库") == "appT"
    assert captured["body"]["name"] == "情报库"


@respx.mock
def test_create_table_uses_primary_field() -> None:
    respx.post(TOKEN_URL).respond(200, json={"code": 0, "tenant_access_token": "t", "expire": 7200})
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "data": {"table_id": "tblT"}})

    respx.post(TABLES_URL).mock(side_effect=handler)
    assert _setup().create_table("appT") == "tblT"
    fields = captured["body"]["table"]["fields"]
    assert fields[0]["field_name"] == PRIMARY_FIELD


@respx.mock
def test_provision_creates_all_non_primary_fields() -> None:
    respx.post(TOKEN_URL).respond(200, json={"code": 0, "tenant_access_token": "t", "expire": 7200})
    respx.post(APPS_URL).respond(200, json={"code": 0, "data": {"app": {"app_token": "appT"}}})
    respx.post(TABLES_URL).respond(200, json={"code": 0, "data": {"table_id": "tblT"}})
    field_route = respx.post(FIELDS_URL).respond(200, json={"code": 0, "data": {"field": {}}})

    app_token, table_id = _setup().provision(app_name="x")

    assert app_token == "appT"
    assert table_id == "tblT"
    expected_fields = sum(1 for f in FIELDS if f.name != PRIMARY_FIELD)
    assert field_route.call_count == expected_fields


@respx.mock
def test_provision_sends_select_options() -> None:
    respx.post(TOKEN_URL).respond(200, json={"code": 0, "tenant_access_token": "t", "expire": 7200})
    respx.post(APPS_URL).respond(200, json={"code": 0, "data": {"app": {"app_token": "appT"}}})
    respx.post(TABLES_URL).respond(200, json={"code": 0, "data": {"table_id": "tblT"}})
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "data": {"field": {}}})

    respx.post(FIELDS_URL).mock(side_effect=handler)
    _setup().provision()

    laiyuan = next(b for b in bodies if b.get("field_name") == "来源")
    assert laiyuan["type"] == 3
    assert [o["name"] for o in laiyuan["property"]["options"]] == ["GitHub", "HackerNews", "Godot"]


@respx.mock
def test_provision_error_code_raises() -> None:
    respx.post(TOKEN_URL).respond(200, json={"code": 0, "tenant_access_token": "t", "expire": 7200})
    respx.post(APPS_URL).respond(200, json={"code": 99991668, "msg": "no permission"})
    with pytest.raises(RuntimeError):
        _setup().create_app("x")
