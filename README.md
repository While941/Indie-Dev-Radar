# Indie-Dev-Radar

独立游戏开发者情报半自动化系统 — 自动采集（GitHub / Hacker News / Godot Asset Library）→ AI 打分摘要 → 高分内容生成多平台草稿 → 推送到飞书多维表格的「待审核」队列，人工复核后复制发布。

详见 [`Plan.md`](./Plan.md)。本 README 只讲怎么跑。

## 架构

```
GitHub Actions (cron 每日 09:00 北京 + 手动 workflow_dispatch)
   └─ pipeline.py: 采集 → 去重 → 便宜模型打分 → 强模型重写高分 → 推送飞书
```

- **编排**：Python + GitHub Actions（免费、无服务器）。
- **存储与审核**：飞书多维表格（唯一数据源，按 `source_url` 去重；默认只看最近 `dedup_lookback_days` 天）。
- **AI**：OpenAI 兼容接口，默认 [DeepSeek 官方 API](https://api-docs.deepseek.com/)（`deepseek-chat`）。鉴权失败会熔断本轮，避免空打；只推送**已评分**且推荐动作不是「删除」的记录。

## 本地开发

需要 Python 3.9+。**Windows 用 `py` 启动器**（本机 `python` 是商店占位符，不可用）；macOS/Linux 用 `python3`/`pip`。推荐建虚拟环境：

```bash
py -m venv .venv
.venv\Scripts\activate          # Windows
py -m pip install -r requirements-dev.txt
```

复制密钥模板并填写（至少 `AI_API_KEY`；有 `GH_TOKEN` 时 GitHub 搜索限额更高）：

```bash
copy .env.example .env
# 编辑 .env
```

`.env` 关键项：

| 变量 | 说明 |
|------|------|
| `AI_API_KEY` | DeepSeek API Key（在 [platform.deepseek.com](https://platform.deepseek.com/) 申请） |
| `AI_BASE_URL` | 默认 `https://api.deepseek.com` |
| `GH_TOKEN` | 可选，GitHub token |
| `FEISHU_*` | 飞书自建应用与多维表格凭证 |

### Dry-run（采集 + 打分 + 打印，不写入飞书）

若已配置飞书，dry-run **仍会只读去重**（拉取已有 `source_url`），只是不 `batch_create`：

```bash
py pipeline.py --dry-run
py pipeline.py --dry-run --limit 3   # 限制条数，省 AI 费用
```

### 真实推送（需配齐飞书凭证）

```bash
py pipeline.py
```

默认最多处理 `config.yaml` 里的 `max_items_per_run`（当前 40）条；可用 `--limit N` 覆盖。

## 测试

```bash
py -m pytest                        # 全部测试
py -m pytest --cov                  # 带覆盖率（目标 ≥ 80%）
```

## 飞书多维表格准备（一键建表）

1. 在飞书创建一个「自建应用」，拿到 `app_id` / `app_secret`，授予 `bitable:app` 权限（创建多维表格 + 读写记录）。
2. 把凭证填入 `.env`（`FEISHU_APP_ID` / `FEISHU_APP_SECRET`）。
3. 运行一键建表脚本：

   ```bash
   py setup_feishu_table.py --dry-run    # 先预览字段（不调 API）
   py setup_feishu_table.py              # 真正创建 app + 表 + 字段
   ```

   脚本会打印 `FEISHU_APP_TOKEN` 与 `FEISHU_TABLE_ID`，把它们填回 `.env` / GitHub Secrets。
4. 把该自建应用添加进新建的多维表格（右上角「…」→「添加文档应用」），授予读写权限，应用才能每日写入。

> 字段定义集中在 `storage/feishu_schema.py`（唯一真相源），`storage/feishu.py` 的写入映射与建表脚本都引用它；改字段只需改一处，并有单测保证两边不漂移。

### 已有表的增量字段

若表是旧版 schema 建的，请在多维表格中**手工新增**下列文本字段（名称必须一致）：

| 字段名 | 类型 |
|--------|------|
| `维度评分` | 多行文本 |
| `小红书标题` | 多行文本 |
| `小红书正文` | 多行文本 |
| `知乎标题` | 多行文本 |
| `知乎正文` | 多行文本 |
| `B站标题` | 多行文本 |
| `B站正文` | 多行文本 |

并在「推荐发布平台」多选中增加选项 **知乎**（可去掉不再使用的「公众号」）。推荐动作可选增加 **发布失败**（给后续自动发布用）。

缺少字段时 `batch_create` 可能整批失败。新跑 `setup_feishu_table.py` 建的表已包含完整字段。

### 审核时看什么（内容）

高分条目（默认 ≥ `score_threshold`）会生成分平台 **标题 + 正文**，写入上表列。你只需在飞书改「推荐动作」（通过/暂存/删除等）；后续发布 Worker（未实现）将读取这些列自动发帖。

## GitHub Actions

`.github/workflows/daily-collect.yml` 每天 UTC 01:00（北京 09:00）自动运行，也可在 Actions 页面手动触发（`workflow_dispatch`）。需在仓库 Settings → Secrets 配置：`GH_TOKEN`、`AI_API_KEY`、`AI_BASE_URL`（建议 `https://api.deepseek.com`）、`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_APP_TOKEN`、`FEISHU_TABLE_ID`。

AI 鉴权失败或本轮 0 条有效打分时，进程以非 0 退出，便于 Actions 标红。

## 合规

只用官方公开 API，保留原始来源链接，AI 输出标注为草稿、人工审核为必经环节。详见 `Plan.md` §8.F / §12。
