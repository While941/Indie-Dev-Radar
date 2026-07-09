# Indie-Dev-Radar

独立游戏开发者情报半自动化系统：

**自动采集 → AI 打分 → AI 整理「日贴 / 周贴」→ 本地一键复制标题与正文 → 你只做审核与手动发布。**

不做各平台自动发帖（降低封号与合规风险）。人工唯一必要操作：打开导出的 HTML，点「一键复制」，粘贴到小红书 / 知乎 / B站。

## 架构

```
GitHub Actions / 本地 pipeline
  采集 (GitHub · HN · Godot)
    → 去重 (飞书 source_url)
    → 便宜模型打分
    → 强模型生成「日贴」(可选「周贴」)
    → 导出 output/*.html（一键复制）+ 推送飞书备查
```

## 本地开发

需要 Python 3.9+。Windows 推荐 `py` 启动器：

```bash
py -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements-dev.txt
copy .env.example .env
# 填写 AI_API_KEY（DeepSeek）等
```

### 跑日贴（推荐）

```bash
py pipeline.py --dry-run --limit 15
```

生成文件示例：

- `output/日贴-YYYY-MM-DD.html` — **浏览器打开，点「一键复制标题/正文」**
- `output/latest-daily.html` — 最新日贴快捷入口
- 同名 `.md` 便于归档

### 额外生成周贴 / AI 周贴

```bash
py pipeline.py --weekly --limit 20
```

周贴与 AI周贴会尽量从飞书近 N 天已评分条目中汇总（需飞书凭证），再按来源拆池。

周五下午只跑周类（跳过日贴/AI日贴，避免与早上日跑重复）：

```bash
py pipeline.py --weekly --weekly-only
```

AI 日贴/周贴只收录 `GitHubAI` 源（`config.yaml` → `sources.github_ai`），导出：

- `output/AI日贴-YYYY-MM-DD.html` / `latest-ai-daily.html`
- `output/AI周贴-YYYY-Www.html` / `latest-ai-weekly.html`

### 真实写入飞书

```bash
py pipeline.py
```

## 你怎么用（人工只做这些）

1. 每天跑（或等 GitHub Actions）。
2. 浏览器打开 `output/latest-daily.html`。
3. 对小红书 / 知乎 / B站：点 **一键复制标题** → 粘贴；再 **一键复制正文** → 粘贴发布。
4. （可选）在飞书把对应「日贴/周贴」行的推荐动作改成「已发布」，填已发布链接。

单条情报仍会进飞书备查；**默认不再为每条单独写长文**（`digest.rewrite_per_item: false`），以日贴/周贴为主，省费用。

## 测试

```bash
py -m pytest
py -m pytest --cov
```

## 飞书

一键建表：

```bash
py setup_feishu_table.py --dry-run
py setup_feishu_table.py
```

### 已有表请增量添加

| 字段 | 类型 |
|------|------|
| 内容类型 | 单选：单条情报 / 日贴 / 周贴 |
| 维度评分 | 文本 |
| 小红书标题 / 小红书正文 | 文本 |
| 知乎标题 / 知乎正文 | 文本 |
| B站标题 / B站正文 | 文本 |

「来源」增加选项：日贴、周贴。  
「推荐发布平台」：小红书、知乎、B站。

表格链接示例（把 token 换成你的 `.env`）：

`https://feishu.cn/base/<FEISHU_APP_TOKEN>?table=<FEISHU_TABLE_ID>`

## GitHub Actions

`.github/workflows/daily-collect.yml`（北京时间，UTC+8）：

| 调度 | UTC cron | 行为 |
|------|----------|------|
| 每天 10:00 | `0 2 * * *` | 日贴 + AI日贴 |
| 周五 16:00 | `0 8 * * 5` | 仅 周贴 + AI周贴（`--weekly --weekly-only`） |

免费仓库的 schedule 可能延迟数分钟，属平台限制。也可 `workflow_dispatch` 手动选 weekly / dry_run。

Secrets：`GH_TOKEN`、`AI_API_KEY`、`AI_BASE_URL=https://api.deepseek.com`、飞书四件套。

产物上传为 artifact `digests`（`output/`）。

## 配置要点（`config.yaml`）

采集侧默认 **多路径 + 短窗口新鲜度**；评分侧用 **typed `DiscoverySignals` 锚定维度后只算一次 `compute_score`**：

| 源 | 多路径 | 默认新鲜度 |
|----|--------|------------|
| GitHub / GitHubAI | `path_sorts` + `created_within_days`（stars/updated 同一 family） | `max_age_days` / `freshness_horizon_days` ≈ 3 |
| Godot | `sorts: updated, new, rating` 合并同资产 | 同上 ≈ 3 天 |
| Hacker News | `topstories` + `showstories` | ≈ 2 天 + 游戏关键词门控 |

```yaml
sources:
  github:
    pushed_within_days: 3
    max_age_days: 3
    freshness_horizon_days: 3
    min_stars: 50
    path_sorts: ["stars", "updated"]
    created_within_days: 7
  github_ai:
    enabled: true
  godot:
    sorts: ["updated", "new", "rating"]
  hackernews:
    max_age_days: 2
    require_topic_match: true
scoring:
  weights:
    path_corroboration: 0.10   # 多路径正交命中写入的维度
  score_threshold: 70
```

### 已有飞书表升级

若表是旧版创建，请在「来源」单选增加：`GitHubAI`、`AI日贴`、`AI周贴`；在「内容类型」增加：`AI日贴`、`AI周贴`。新建表可直接 `py setup_feishu_table.py`。

## 合规

只用公开 API 采集；发布由你在官方 App/网页完成；文案保留出处链接。详见 `Plan.md`。
