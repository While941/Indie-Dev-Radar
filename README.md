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

### 额外生成周贴

```bash
py pipeline.py --weekly --limit 20
```

周贴会尽量从飞书近 N 天已评分条目中汇总（需飞书凭证）。

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

`.github/workflows/daily-collect.yml`：每天 UTC 01:00（北京 09:00）。  
Secrets：`GH_TOKEN`、`AI_API_KEY`、`AI_BASE_URL=https://api.deepseek.com`、飞书四件套。

Actions 产物默认在 runner 上；本地或自托管更适合保存 `output/` HTML。若需要可后续加 artifact 上传。

## 配置要点（`config.yaml`）

```yaml
digest:
  daily_enabled: true
  weekly_enabled: false   # 或用 --weekly
  max_items_daily: 8
  max_items_weekly: 15
  rewrite_per_item: false
  output_dir: "output"
scoring:
  score_threshold: 70     # 进入日贴/周贴候选的最低分
```

## 合规

只用公开 API 采集；发布由你在官方 App/网页完成；文案保留出处链接。详见 `Plan.md`。
