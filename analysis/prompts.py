"""Prompt templates for scoring (cheap model) and rewriting (strong model).

Kept in one module so wording can be tuned without touching logic. Both
prompts demand strict JSON output (the client also sets response_format).
"""
from __future__ import annotations

from models.item import IntelligenceItem

from .signals import signals_prompt_block

# --- Scoring (cheap model) ------------------------------------------------

SCORE_SYSTEM = (
    "你是独立游戏开发领域的资深技术情报分析师。服务对象：Godot/Unity/Unreal 初中级开发者、"
    "2D 横版动作/Roguelike/像素风开发者、AI 辅助游戏开发人群。"
    "你的任务是对单条情报做结构化评估，严格只输出一个 JSON 对象，不要任何额外文字。"
    "若来源为 GitHubAI：以「能否服务独立游戏制作」（美术/音频/文案/NPC/程序化/本地 LLM/"
    "编辑器插件/资产生成等）衡量相关度；泛大模型新闻、与游戏制作无关的给低 relevance。"
    "系统会在代码侧用日历年龄覆盖 freshness，并用多路径命中写入 path_corroboration；"
    "你仍需认真评 freshness/popularity，但陈旧内容请倾向 暂存/删除。"
)

SCORE_USER_TEMPLATE = """对下面这条情报，从「独立游戏开发者能用上」的视角评估。

来源: {source}
标题: {title}
摘要: {summary}
作者: {author}
发布时间/最近活动: {published_at}
原始热度与多路径信号: {signals}
链接: {url}

说明：
- discovery_paths：采集路径标签；path_count≥2 表示多路径命中。
- age_days / signal_freshness：日历新鲜度（系统会据此锚定 freshness 维度）。
- signal_popularity：原始热度映射，供你评 popularity，勿忽略极低热度。

严格输出 JSON，字段如下：
- relevance(相关度,0-10整数): 与独立游戏开发的关联度
- utility(实用性,0-10): 能否立刻用于项目
- freshness(新鲜度,0-10): 是否近期发布/更新（结合 age_days）
- popularity(热度,0-10): star/upvote/讨论量/评分
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
    pub = item.published_at.isoformat() if item.published_at else "未知"
    return SCORE_USER_TEMPLATE.format(
        source=item.source,
        title=item.title,
        summary=(item.summary_raw or "（无摘要）").strip()[:600],
        author=item.author or "未知",
        published_at=pub,
        signals=signals_prompt_block(item),
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


# --- Digest: daily / weekly multi-item packages --------------------------

DIGEST_SYSTEM = (
    "你是独立游戏开发领域的情报主编。把多条候选情报整理成可直接复制发布的日贴/周贴/"
    "AI日贴/AI周贴，有判断、有取舍、语气专业口语化，禁止营销腔和空话。"
    "输出必须可直接粘贴到小红书/知乎/B站，无需人工再润色。"
    "严格只输出一个 JSON 对象。"
)

DIGEST_USER_TEMPLATE = """请将下列情报整理成一份「{kind}」（{period}）。

候选情报（已按相关度/评分筛选）：
{bullet_list}

要求：
1. 不要逐条照搬，要有主编视角：挑重点、归类、写清「对独立游戏开发者有什么用」。
2. 保留关键出处链接（用候选里的 URL）。
3. 三个平台文案结构不同，但信息一致；标题不要完全相同。
4. 文案必须完整可发：不要「此处省略」「待补充」。

严格输出 JSON：
- recommended_title(字符串): 通用总标题（<=30字）
- tags(字符串数组): 4-8 个中文标签
- platforms(对象): 键固定 "小红书"/"知乎"/"B站"，值 {{"title","body"}}
  - 小红书: title<=20字；body 400-800字，分点+emoji，开头抓人，结尾收藏引导
  - 知乎: title 问题/判断式<=40字；body 1000-2000字，分小节，有观点
  - B站: title 钩子<=30字；body 150-400字口语动态，可带话题
{kind_hint}
"""

KIND_HINTS = {
    "日贴": "这是「今日速览」：条数少、节奏快，突出今天最值得点的 3–6 条。",
    "周贴": "这是「本周精选」：可分组（工具/插件/开源/趋势），做周回顾与下周可行动建议。",
    "AI日贴": (
        "这是「AI 工具今日速览」：只谈独立开发者能用上的 AI（美术/音频/文案/NPC/"
        "程序化/本地模型/工作流）。写清能省什么时间、接入难度；不做泛 AI 新闻。"
    ),
    "AI周贴": (
        "这是「AI 工具本周精选」：可分组（生成美术/对话与文本/管线自动化/本地 LLM），"
        "对比本周值得试的工具，给下周可行动建议；强调游戏制作落地，不做行业八卦。"
    ),
}


def build_digest_user(
    *,
    kind: str,
    period: str,
    items: list[IntelligenceItem],
) -> str:
    lines: list[str] = []
    for i, it in enumerate(items, 1):
        score = f"{it.score:.0f}" if it.score is not None else "?"
        summary = (it.one_line_summary or it.summary_raw or "（无摘要）").strip()[:200]
        lines.append(
            f"{i}. [{it.source}|{score}分] {it.title}\n"
            f"   摘要: {summary}\n"
            f"   分类: {it.category or '未分类'} | 链接: {it.source_url}"
        )
    return DIGEST_USER_TEMPLATE.format(
        kind=kind,
        period=period,
        bullet_list="\n".join(lines) if lines else "（无候选，请礼貌说明暂无高价值情报）",
        kind_hint=KIND_HINTS.get(kind, ""),
    )
