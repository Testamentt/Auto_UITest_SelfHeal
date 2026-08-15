"""自愈看板（T3）—— 指标摘要 + 纯 HTML 审计表（零依赖，供面试展示）。

消费 reporter.records：顶部渲染量化指标（自愈总数/成功率/策略分布/根因分布），
下方渲染自愈过程审计表（原定位器 → 新定位器 / 策略 / 置信度 / 根因 / 结果）。
字段经 html.escape 转义，避免注入与乱码。
"""

from __future__ import annotations

import html
from pathlib import Path

from selfheal.reporting.hooks import HealingRecord
from selfheal.reporting.metrics import MetricsSnapshot, compute_metrics


def _render_metrics(metrics: MetricsSnapshot) -> str:
    """渲染指标摘要区（成功率卡片 + 真自愈/flaky + 策略分布 + 根因分布）。"""
    rate = metrics.success_rate
    strategy_items = "".join(
        f"<li>{html.escape(k)}：{v} 次</li>"
        for k, v in sorted(metrics.strategy_distribution.items())
    ) or "<li>（无）</li>"
    rootcause_items = "".join(
        f"<li>{html.escape(k)}：{v} 次</li>"
        for k, v in sorted(metrics.root_cause_distribution.items())
    ) or "<li>（无）</li>"
    return f"""<div class="metrics">
  <div class="card"><div class="num">{metrics.total}</div><div class="label">自愈总数</div></div>
  <div class="card"><div class="num">{metrics.success}</div><div class="label">成功次数</div></div>
  <div class="card"><div class="num">{rate:.0%}</div><div class="label">自愈成功率</div></div>
  <div class="card"><div class="num">{metrics.verified}</div><div class="label">真自愈 (verified)</div></div>
  <div class="card"><div class="num">{metrics.flaky}</div><div class="label">flaky 侥幸通过</div></div>
</div>
<div class="dist">
  <div><h3>策略命中分布</h3><ul>{strategy_items}</ul></div>
  <div><h3>根因分布</h3><ul>{rootcause_items}</ul></div>
</div>"""


def _render_cost(cost: dict) -> str:
    """T17：多模态成本卡片（LLM/VLM 调用次数 + 估算费用）。

    用 .get 防护缺键（审查 nit）：cost 契约来自 estimate_cost，但不依赖其必然完整。
    """
    return f"""<div class="metrics">
  <div class="card"><div class="num">{cost.get('llm_calls', 0)}</div><div class="label">LLM 调用次数</div></div>
  <div class="card"><div class="num">{cost.get('vlm_calls', 0)}</div><div class="label">VLM 调用次数</div></div>
  <div class="card"><div class="num">¥{cost.get('total_cost', 0.0):.4f}</div><div class="label">估算费用</div></div>
</div>"""


def render_dashboard(records: list[HealingRecord], cost: dict | None = None) -> str:
    """把自愈记录渲染为一张完整 HTML 页面（内嵌样式，零依赖）。

    cost（T17）为可选成本统计（HealingReporter.cost_summary() 产出），提供时渲染成本卡片。
    """
    metrics = compute_metrics(records)
    rows: list[str] = []
    for rec in records:
        kind = "✅ 真自愈" if rec.verified else "⚠️ flaky"
        rows.append(
            "<tr>"
            f"<td>{html.escape(rec.original_selector)}</td>"
            f"<td>{html.escape(rec.new_selector or '')}</td>"
            f"<td>{html.escape(rec.strategy or '')}</td>"
            f"<td>{rec.confidence:.2f}</td>"
            f"<td>{html.escape(rec.root_cause or '')}</td>"
            f"<td>{kind}</td>"
            f"<td>{'✅' if rec.success else '❌'}</td>"
            "</tr>"
        )
    body = "\n".join(rows) or "<tr><td colspan='7'>(暂无自愈记录)</td></tr>"
    cost_html = _render_cost(cost) if cost else ""
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>自愈看板</title>
<style>
  body {{ font-family: sans-serif; margin: 24px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; font-size: 13px; }}
  th {{ background: #f5f5f5; }}
  .metrics {{ display: flex; gap: 16px; margin-bottom: 16px; }}
  .card {{ border: 1px solid #ccc; border-radius: 8px; padding: 12px 24px; text-align: center; }}
  .card .num {{ font-size: 28px; font-weight: bold; }}
  .card .label {{ font-size: 13px; color: #666; }}
  .dist {{ display: flex; gap: 32px; margin-bottom: 16px; }}
  .dist h3 {{ font-size: 14px; margin: 0 0 6px; }}
  .dist ul {{ margin: 0; padding-left: 18px; font-size: 13px; }}
</style></head>
<body>
<h2>AI 自愈看板（指标 + 审计）</h2>
{_render_metrics(metrics)}
{cost_html}
<h3>自愈审计明细</h3>
<table>
<thead><tr><th>原定位器</th><th>新定位器</th><th>策略</th><th>置信度</th><th>根因</th><th>类型</th><th>结果</th></tr></thead>
<tbody>
{body}
</tbody></table>
</body></html>
"""


def write_dashboard(records: list[HealingRecord], out_path: str | Path, cost: dict | None = None) -> Path:
    """把看板写入文件并返回路径。"""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard(records, cost), encoding="utf-8")
    return path
