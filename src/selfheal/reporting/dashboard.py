"""自愈看板 v0 —— 纯 HTML 审计表（零依赖，供面试展示）。

消费 reporter.records，渲染一张自愈过程审计表：
原定位器 → 新定位器 / 策略 / 置信度 / 根因 / 结果。
字段经 html.escape 转义，避免注入与乱码。
"""

from __future__ import annotations

import html
from pathlib import Path

from selfheal.reporting.hooks import HealingRecord


def render_dashboard(records: list[HealingRecord]) -> str:
    """把自愈记录渲染为一张完整 HTML 页面（内嵌样式，零依赖）。"""
    rows: list[str] = []
    for rec in records:
        rows.append(
            "<tr>"
            f"<td>{html.escape(rec.original_selector)}</td>"
            f"<td>{html.escape(rec.new_selector or '')}</td>"
            f"<td>{html.escape(rec.strategy or '')}</td>"
            f"<td>{rec.confidence:.2f}</td>"
            f"<td>{html.escape(rec.root_cause or '')}</td>"
            f"<td>{'✅' if rec.success else '❌'}</td>"
            "</tr>"
        )
    body = "\n".join(rows) or "<tr><td colspan='6'>(暂无自愈记录)</td></tr>"
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>自愈看板</title>
<style>
  body {{ font-family: sans-serif; margin: 24px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; font-size: 13px; }}
  th {{ background: #f5f5f5; }}
</style></head>
<body>
<h2>AI 自愈审计看板（v0）</h2>
<p>共 {len(records)} 次自愈记录</p>
<table>
<thead><tr><th>原定位器</th><th>新定位器</th><th>策略</th><th>置信度</th><th>根因</th><th>结果</th></tr></thead>
<tbody>
{body}
</tbody></table>
</body></html>
"""


def write_dashboard(records: list[HealingRecord], out_path: str | Path) -> Path:
    """把看板写入文件并返回路径。"""
    path = Path(out_path)
    path.write_text(render_dashboard(records), encoding="utf-8")
    return path
