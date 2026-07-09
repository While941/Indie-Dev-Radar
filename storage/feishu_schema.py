"""Feishu Bitable field schema — single source of truth for the table layout.

Both ``storage.feishu.item_to_fields`` (writing records) and the setup script
(creating the table) derive from ``FIELDS`` here, so a field name can never
silently drift between the writer and the table definition.

Field types follow Feishu's numeric codes:
  1=多行文本 2=数字 3=单选 4=多选 5=日期.
``原始链接`` is a plain text field (not a URL/super-link field) on purpose:
text fields round-trip strings verbatim, which keeps source_url dedup reliable.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class FieldType(IntEnum):
    TEXT = 1
    NUMBER = 2
    SINGLE_SELECT = 3
    MULTI_SELECT = 4
    DATETIME = 5


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: FieldType
    options: tuple[str, ...] = ()


TABLE_NAME = "情报候选"
PRIMARY_FIELD = "标题"            # text field created as the table's primary column
URL_FIELD = "原始链接"             # dedup key

# In-memory platform key -> Feishu column names for title / body.
PLATFORM_CONTENT_FIELDS: dict[str, tuple[str, str]] = {
    "小红书": ("小红书标题", "小红书正文"),
    "知乎": ("知乎标题", "知乎正文"),
    "B站": ("B站标题", "B站正文"),
}

FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("来源", FieldType.SINGLE_SELECT,
              ("GitHub", "GitHubAI", "HackerNews", "Godot",
               "日贴", "周贴", "AI日贴", "AI周贴")),
    FieldSpec("内容类型", FieldType.SINGLE_SELECT,
              ("单条情报", "日贴", "周贴", "AI日贴", "AI周贴")),
    FieldSpec(URL_FIELD, FieldType.TEXT),
    FieldSpec(PRIMARY_FIELD, FieldType.TEXT),
    FieldSpec("原始摘要", FieldType.TEXT),
    FieldSpec("作者", FieldType.TEXT),
    FieldSpec("发布时间", FieldType.DATETIME),
    FieldSpec("抓取时间", FieldType.DATETIME),
    FieldSpec("分类", FieldType.SINGLE_SELECT),           # options populated dynamically by AI
    FieldSpec("标签", FieldType.MULTI_SELECT),
    FieldSpec("AI 评分", FieldType.NUMBER),
    FieldSpec("维度评分", FieldType.TEXT),
    FieldSpec("风险等级", FieldType.SINGLE_SELECT, ("低", "中", "高")),
    FieldSpec("一句话总结", FieldType.TEXT),
    FieldSpec("适合人群", FieldType.TEXT),
    FieldSpec("推荐动作", FieldType.SINGLE_SELECT,
              ("待审核", "发布", "暂存", "加入周报", "删除", "已发布", "发布失败")),
    FieldSpec("推荐发布平台", FieldType.MULTI_SELECT, ("小红书", "知乎", "B站")),
    FieldSpec("推荐标题", FieldType.TEXT),
    FieldSpec("小红书标题", FieldType.TEXT),
    FieldSpec("小红书正文", FieldType.TEXT),
    FieldSpec("知乎标题", FieldType.TEXT),
    FieldSpec("知乎正文", FieldType.TEXT),
    FieldSpec("B站标题", FieldType.TEXT),
    FieldSpec("B站正文", FieldType.TEXT),
    FieldSpec("人工备注", FieldType.TEXT),
    FieldSpec("已发布链接", FieldType.TEXT),
)

FIELD_NAMES = frozenset(f.name for f in FIELDS)

# Human-readable labels for dimension keys written into the 「维度评分」 field.
DIMENSION_LABELS = {
    "relevance": "相关度",
    "utility": "实用",
    "freshness": "新鲜",
    "popularity": "热度",
    "differentiation": "差异化",
    "biz_value": "商业",
    "risk": "风险",
}

DIMENSIONS_FIELD = "维度评分"


def field_payload(spec: FieldSpec) -> dict:
    """Build the JSON body for creating one field via the Feishu fields API."""
    body: dict = {"field_name": spec.name, "type": int(spec.type)}
    if spec.options:
        body["property"] = {"options": [{"name": opt} for opt in spec.options]}
    return body
