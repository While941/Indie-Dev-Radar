"""Prompt templates for scoring (cheap model) and rewriting (strong model).

Kept in one module so wording can be tuned without touching logic. Both
prompts demand strict JSON output (the client also sets response_format).
"""
from __future__ import annotations

from models.item import IntelligenceItem

# --- Scoring (cheap model) ------------------------------------------------

SCORE_SYSTEM = (
    "你是独立游戏开发领域的资深技术情报分析师。服务对象：Godot/Unity/Unreal 初中级开发者、"
    "2D 横版动作/Roguelike/像素风开发者、AI 辅助游戏开发人群。"
    "你的任务是对单条情报做结构化评估，严格只输出一个 JSON 对象，不要任何额外文字。"
)

SCORE_USER_TEMPLATE = """对下面这条情报，从「独立游戏开发者能用上」的视角评估。

来源: {source}
标题: {title}
摘要: {summary}
作者: {author}
原始热度指标: {score_raw}
链接: {url}

严格输出 JSON，字段如下：
- relevance(相关度,0-10整数): 与独立游戏开发的关联度
- utility(实用性,0-10): 能否立刻用于项目
- freshness(新鲜度,0-10): 是否近期发布/更新
- popularity(热度,0-10): star/upvote/讨论量
- differentiation(差异化,0-10): 是否非大众都在转的内容
- biz_value(商业价值,0-10): 能否导向工具/模板/资料包/课程
- risk(风险,0-10): 版权/平台规则/虚假信息风险，数值越大风险越高
- category(分类): 简短分类，如「Godot 插件」「AI 工具」「开源项目」「游戏趋势」「素材资源」
- tags(字符串数组): 3-5 个关键词标签
- risk_level(低|中|高)
- one_line_summary(一句话总结,<=40字): 这是什么、对独立开发者有什么用
- recommended_action(待审核|发布|暂存|加入周报|删除)
- recommended_platforms(数组,从[小红书,知乎,B站]中选): 适合发布平台
- target_audience(适合人群)
"""


def build_score_user(item: IntelligenceItem) -> str:
    return SCORE_USER_TEMPLATE.format(
        source=item.source,
        title=item.title,
        summary=(item.summary_raw or "（无摘要）").strip()[:600],
        author=item.author or "未知",
        score_raw=item.score_raw,
        url=item.source_url,
    )


# --- Rewriting (strong model) --------------------------------------------

REWRITE_SYSTEM = (
    "你是独立游戏开发领域的内容主笔，擅长把技术情报改写成有判断、有观点、"
    "可直接在小红书/知乎/B站发布的中文内容。"
    "语气专业但口语化，避免营销腔和空话。严格只输出一个 JSON 对象。"
)

REWRITE_USER_TEMPLATE = """基于以下情报与分析结论，为三个平台各写「独立标题 + 正文」。

原始标题: {title}
一句话总结: {summary}
分类: {category}
AI 评分: {score}/100
适合人群: {audience}
原始链接: {url}

严格输出 JSON，字段：
- recommended_title(字符串): 通用推荐标题（<=30字，不要标题党），作兜底
- tags(字符串数组): 3-6 个适合带流量的中文标签
- platforms(对象): 键固定为 "小红书"/"知乎"/"B站"，每个值是对象 {{"title": "...", "body": "..."}}
  规格：
  - 小红书.title: <=20字，痛点或收益导向
  - 小红书.body: 300-600字，分点+适量emoji，开头抓人，结尾引导收藏；自然点明对独立开发者的用处；文末保留原始链接
  - 知乎.title: 问题式或判断式，<=40字
  - 知乎.body: 800-1500字，有观点的长文/短文，分段落；说明是什么、为什么值得关注、怎么用；保留出处链接；禁止洗稿口吻
  - B站.title: 动态钩子/首行标题感，<=30字
  - B站.body: 100-300字口语化动态正文；可带话题标签；保留原始链接
要求：三个平台标题不要完全相同；正文必须可独立发布，不要只写一句话摘要。
"""


def build_rewrite_user(item: IntelligenceItem) -> str:
    return REWRITE_USER_TEMPLATE.format(
        title=item.title,
        summary=item.one_line_summary or item.summary_raw or "（无）",
        category=item.category or "未分类",
        score=item.score if item.score is not None else "N/A",
        audience=item.target_audience or "独立游戏开发者",
        url=item.source_url,
    )
