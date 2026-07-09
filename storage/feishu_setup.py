"""Provision a Feishu Bitable (app + table + fields) from the shared schema.

Run once via ``setup_feishu_table.py``; the resulting app_token/table_id go into
``.env`` / GitHub Secrets and are then used by ``FeishuClient`` for daily writes.

Endpoints:
- POST /bitable/v1/apps                                   create a Bitable app
- POST /bitable/v1/apps/{app_token}/tables                create a table
- POST /bitable/v1/apps/{app_token}/tables/{table_id}/fields   add a field
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .feishu import BASE_URL, FeishuAuth
from .feishu_schema import (
    FIELDS,
    FIELD_NAMES,
    PRIMARY_FIELD,
    TABLE_NAME,
    FieldSpec,
    FieldType,
    field_payload,
)

log = logging.getLogger(__name__)


class FeishuSetup(FeishuAuth):
    """Creates the Bitable app, table, and all fields defined in the schema."""

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post(
            f"{self._base_url}{path}", headers=self._headers(), json=body, timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu {path} error: {data.get('code')} {data.get('msg')}")
        return data.get("data") or {}

    def create_app(self, name: str, folder_token: str | None = None) -> str:
        body: dict[str, Any] = {"name": name}
        if folder_token:
            body["folder_token"] = folder_token
        data = self._post("/bitable/v1/apps", body)
        app_token = (data.get("app") or {}).get("app_token")
        if not app_token:
            raise RuntimeError(f"create_app returned no app_token: {data!r}")
        return app_token

    def create_table(self, app_token: str, table_name: str = TABLE_NAME,
                     primary_field: str = PRIMARY_FIELD) -> str:
        body = {"table": {"name": table_name, "fields": [
            {"field_name": primary_field, "type": int(FieldType.TEXT)},
        ]}}
        data = self._post(f"/bitable/v1/apps/{app_token}/tables", body)
        table_id = data.get("table_id")
        if not table_id:
            raise RuntimeError(f"create_table returned no table_id: {data!r}")
        return table_id

    def add_field(self, app_token: str, table_id: str, spec: FieldSpec) -> None:
        self._post(f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", field_payload(spec))

    def provision(
        self,
        *,
        app_name: str = "Indie-Dev-Radar 情报库",
        table_name: str = TABLE_NAME,
        primary_field: str = PRIMARY_FIELD,
        fields: tuple[FieldSpec, ...] = FIELDS,
        folder_token: str | None = None,
    ) -> tuple[str, str]:
        """Create the app, the table, and every non-primary field. Returns (app_token, table_id)."""
        app_token = self.create_app(app_name, folder_token)
        table_id = self.create_table(app_token, table_name, primary_field)
        added = 0
        for spec in fields:
            if spec.name == primary_field:
                continue  # already created as the table's primary field
            self.add_field(app_token, table_id, spec)
            added += 1
        log.info("Provisioned table %s with %d extra fields (app_token=%s, table_id=%s)",
                 table_name, added, app_token, table_id)
        return app_token, table_id


__all__ = ["FeishuSetup", "FIELDS", "FIELD_NAMES"]
