"""Throwaway diagnostic: report Feishu table record state (total / scored / unscored)."""
from config import load_config
from storage.feishu import FeishuClient

c = load_config()
fc = FeishuClient(c.feishu_app_id, c.feishu_app_secret, c.feishu.app_token, c.feishu.table_id)

total = scored = 0
pt = None
while True:
    params = {"page_size": "500"}
    if pt:
        params["page_token"] = pt
    resp = fc._client.get(fc._records_path, headers=fc._headers(), params=params).json()
    data = resp.get("data", {}) or {}
    for rec in data.get("items", []) or []:
        total += 1
        if (rec.get("fields") or {}).get("AI 评分") is not None:
            scored += 1
    pt = data.get("page_token")
    if not data.get("has_more"):
        break

print(f"total={total} scored={scored} unscored={total - scored}")
