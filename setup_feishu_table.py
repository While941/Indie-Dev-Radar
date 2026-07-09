"""One-time Feishu Bitable provisioning for Indie-Dev-Radar.

Creates a Bitable app, a table, and all fields from ``storage.feishu_schema``.
After running, copy the printed app_token / table_id into ``.env``
(``FEISHU_APP_TOKEN`` / ``FEISHU_TABLE_ID``) and GitHub Secrets.

Requires a self-built Feishu app with the ``bitable:app`` scope; set
``FEISHU_APP_ID`` and ``FEISHU_APP_SECRET`` in your environment / ``.env``.

Usage:
    python setup_feishu_table.py --dry-run     # print the field plan, no API calls
    python setup_feishu_table.py               # create app + table + fields
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from storage.feishu_schema import FIELDS, PRIMARY_FIELD, TABLE_NAME, FieldType
from storage.feishu_setup import FeishuSetup

_TYPE_LABELS = {
    FieldType.TEXT: "文本",
    FieldType.NUMBER: "数字",
    FieldType.SINGLE_SELECT: "单选",
    FieldType.MULTI_SELECT: "多选",
    FieldType.DATETIME: "日期",
}


def _print_plan() -> None:
    print(f"表名: {TABLE_NAME}   主字段(文本): {PRIMARY_FIELD}")
    print(f"共 {len(FIELDS)} 个字段:\n")
    for spec in FIELDS:
        label = _TYPE_LABELS.get(spec.type, str(int(spec.type)))
        opts = f"  选项: {', '.join(spec.options)}" if spec.options else ""
        marker = "  (主字段,随建表创建)" if spec.name == PRIMARY_FIELD else ""
        print(f"  - {spec.name:<10} [{label}]{opts}{marker}")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()  # read FEISHU_APP_ID / FEISHU_APP_SECRET from a local .env
    parser = argparse.ArgumentParser(description="Provision the Feishu Bitable for Indie-Dev-Radar")
    parser.add_argument("--app-name", default="Indie-Dev-Radar 情报库")
    parser.add_argument("--folder-token", default=os.environ.get("FEISHU_FOLDER_TOKEN"),
                        help="optional folder_token to create the app in a specific folder")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the field plan and exit without calling Feishu")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (TypeError, ValueError):
                pass

    if args.dry_run:
        print("=== Indie-Dev-Radar Feishu schema (dry-run) ===")
        _print_plan()
        return 0

    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        print("ERROR: FEISHU_APP_ID and FEISHU_APP_SECRET must be set.", file=sys.stderr)
        print("Create a self-built Feishu app with the bitable:app scope, then set both.",
              file=sys.stderr)
        return 2

    setup = FeishuSetup(app_id, app_secret)
    app_token, table_id = setup.provision(
        app_name=args.app_name, folder_token=args.folder_token,
    )

    print("\n✅ Feishu Bitable provisioned.")
    print(f"   app_name : {args.app_name}")
    print(f"   table    : {TABLE_NAME}  ({len(FIELDS)} fields)")
    print("\nPut these into .env / GitHub Secrets:")
    print(f"   FEISHU_APP_TOKEN = {app_token}")
    print(f"   FEISHU_TABLE_ID  = {table_id}")
    print("\nThen verify with:  python pipeline.py --dry-run   # uses no Feishu creds")
    print("And a real push :  python pipeline.py             # once creds are set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
