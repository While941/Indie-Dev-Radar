"""Export digests to Markdown + HTML with one-click copy buttons.

Human publishes manually: open the HTML in a browser, click 复制标题 / 复制正文.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from models.digest import DigestPackage
from models.item import PUBLISH_PLATFORMS

PLATFORMS = PUBLISH_PLATFORMS


def export_digest_markdown(package: DigestPackage, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {package.kind} · {package.period_label}",
        "",
        f"- 收录条目: {package.item_count}",
        f"- 总标题: {package.recommended_title or package.package_title}",
        f"- 标签: {', '.join(package.tags) if package.tags else '（无）'}",
        "",
        "---",
        "",
    ]
    for plat in PLATFORMS:
        post = package.platform_posts.get(plat) or {}
        title = (post.get("title") or "").strip()
        body = (post.get("body") or "").strip()
        lines.extend([
            f"## {plat}",
            "",
            "### 标题",
            "",
            "```",
            title or "（空）",
            "```",
            "",
            "### 正文",
            "",
            "```",
            body or "（空）",
            "```",
            "",
            "---",
            "",
        ])
    if package.source_urls:
        lines.append("## 来源链接")
        lines.append("")
        for u in package.source_urls:
            lines.append(f"- {u}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def export_digest_html(package: DigestPackage, path: Path) -> Path:
    """Self-contained HTML: each platform has copy-title / copy-body buttons."""
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks: list[str] = []
    for plat in PLATFORMS:
        post = package.platform_posts.get(plat) or {}
        title = (post.get("title") or "").strip()
        body = (post.get("body") or "").strip()
        tid = html.escape(plat)
        # Embed raw text in data attributes via JSON for safe JS copy
        title_js = html.escape(json.dumps(title, ensure_ascii=False), quote=True)
        body_js = html.escape(json.dumps(body, ensure_ascii=False), quote=True)
        blocks.append(f"""
<section class="card">
  <h2>{tid}</h2>
  <div class="row">
    <h3>标题</h3>
    <button type="button" data-copy={title_js}>一键复制标题</button>
  </div>
  <pre class="title-box">{html.escape(title) or "（空）"}</pre>
  <div class="row">
    <h3>正文</h3>
    <button type="button" data-copy={body_js}>一键复制正文</button>
  </div>
  <pre class="body-box">{html.escape(body) or "（空）"}</pre>
</section>
""")

    sources = "".join(f"<li><a href=\"{html.escape(u)}\">{html.escape(u)}</a></li>"
                      for u in package.source_urls)
    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html.escape(package.kind)} · {html.escape(package.period_label)}</title>
<style>
  :root {{ font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }}
  body {{ max-width: 820px; margin: 24px auto; padding: 0 16px 48px; color: #1a1a1a; background: #f6f7f9; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 4px; }}
  .meta {{ color: #666; margin-bottom: 20px; font-size: 0.95rem; }}
  .card {{ background: #fff; border-radius: 12px; padding: 16px 18px 20px; margin-bottom: 16px;
           box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  .row {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
  h2 {{ margin: 0 0 12px; font-size: 1.15rem; }}
  h3 {{ margin: 12px 0 6px; font-size: 0.95rem; color: #444; }}
  button {{ cursor: pointer; border: 0; border-radius: 8px; padding: 8px 14px;
            background: #1677ff; color: #fff; font-size: 0.9rem; white-space: nowrap; }}
  button:active {{ opacity: .85; }}
  button.ok {{ background: #52c41a; }}
  pre {{ white-space: pre-wrap; word-break: break-word; background: #f0f2f5; padding: 12px;
         border-radius: 8px; font-size: 0.92rem; line-height: 1.55; margin: 0; }}
  .toast {{ position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
            background: #111; color: #fff; padding: 10px 18px; border-radius: 8px;
            opacity: 0; transition: opacity .2s; pointer-events: none; }}
  .toast.show {{ opacity: .92; }}
  ul {{ padding-left: 1.2em; }}
</style>
</head>
<body>
  <h1>{html.escape(package.kind)} · {html.escape(package.period_label)}</h1>
  <p class="meta">收录 {package.item_count} 条 · {html.escape(package.recommended_title or package.package_title)}
  <br/>打开本页 → 点按钮复制 → 粘贴到对应平台发布（无需再改）</p>
  {"".join(blocks)}
  <section class="card">
    <h2>来源链接</h2>
    <ul>{sources or "<li>（无）</li>"}</ul>
  </section>
  <div class="toast" id="toast">已复制</div>
<script>
const toast = document.getElementById('toast');
function showToast(msg) {{
  toast.textContent = msg;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 1200);
}}
document.querySelectorAll('button[data-copy]').forEach(btn => {{
  btn.addEventListener('click', async () => {{
    const text = JSON.parse(btn.getAttribute('data-copy'));
    try {{
      await navigator.clipboard.writeText(text || '');
      const old = btn.textContent;
      btn.textContent = '已复制 ✓';
      btn.classList.add('ok');
      showToast('已复制到剪贴板');
      setTimeout(() => {{ btn.textContent = old; btn.classList.remove('ok'); }}, 1500);
    }} catch (e) {{
      showToast('复制失败，请手动全选复制');
    }}
  }});
}});
</script>
</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")
    return path


def export_package(package: DigestPackage, output_dir: Path) -> dict[str, Path]:
    """Write both markdown and HTML; return paths."""
    safe = package.period_label.replace(":", "-")
    stem = f"{package.kind}-{safe}"
    md = export_digest_markdown(package, output_dir / f"{stem}.md")
    ht = export_digest_html(package, output_dir / f"{stem}.html")
    # latest shortcuts for daily convenience
    if package.kind == "日贴":
        export_digest_html(package, output_dir / "latest-daily.html")
        export_digest_markdown(package, output_dir / "latest-daily.md")
    if package.kind == "周贴":
        export_digest_html(package, output_dir / "latest-weekly.html")
        export_digest_markdown(package, output_dir / "latest-weekly.md")
    if package.kind == "AI日贴":
        export_digest_html(package, output_dir / "latest-ai-daily.html")
        export_digest_markdown(package, output_dir / "latest-ai-daily.md")
    if package.kind == "AI周贴":
        export_digest_html(package, output_dir / "latest-ai-weekly.html")
        export_digest_markdown(package, output_dir / "latest-ai-weekly.md")
    return {"markdown": md, "html": ht}
