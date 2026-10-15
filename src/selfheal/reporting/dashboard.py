"""自愈看板（T3）—— 指标摘要 + 纯 HTML 审计表（零依赖，供面试展示）。

消费 reporter.records：顶部渲染量化指标（自愈总数/成功率/策略分布/根因分布），
下方渲染自愈过程审计表（原定位器 → 新定位器 / 策略 / 置信度 / 根因 / 结果）。
字段经 html.escape 转义，避免注入与乱码。

成本卡片（T17）的数值一律经 _safe_number / _safe_count 归一（L5）：外部 JSON 里的 null、
缺键、空串、非数值都回 0，单个字段异常不再让**整张看板**渲染失败。
"""

from __future__ import annotations

import html
import math
from pathlib import Path
from typing import Any

from selfheal.reporting.hooks import HealingRecord
from selfheal.reporting.metrics import MetricsSnapshot, compute_metrics


def _safe_number(value: Any, default: float = 0.0) -> float:
    """成本字段安全取数（L5）：None / 缺键 / 非数值 / nan / inf 一律回 default。

    成本契约来自 `reporting.fix_proposals.estimate_cost`（llm_calls / vlm_calls /
    llm_cost / vlm_cost / total_cost），但看板也会消费外部 JSON（报告合并、CI 分片汇总），
    那些来源可能给出 `null`——旧实现 `.get(key, 0.0)` 只防缺键，`null` 会 TypeError 让整张
    看板渲染失败。这里统一归一：宁可个别字段归零，不可丢整张审计报告。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _safe_count(value: Any) -> int:
    """调用次数安全取整（L5）：走 _safe_number，非数值/None → 0。"""
    return int(_safe_number(value))


def _render_metrics(metrics: MetricsSnapshot) -> str:
    """渲染指标摘要区（成功率卡片 + 真自愈/flaky + 策略分布 + 根因分布）。"""
    rate = metrics.success_rate
    strategy_items = (
        "".join(
            f"<li>{html.escape(k)}：{v} 次</li>"
            for k, v in sorted(metrics.strategy_distribution.items())
        )
        or "<li>（无）</li>"
    )
    rootcause_items = (
        "".join(
            f"<li>{html.escape(k)}：{v} 次</li>"
            for k, v in sorted(metrics.root_cause_distribution.items())
        )
        or "<li>（无）</li>"
    )
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

    所有取值点（llm_calls / vlm_calls / llm_cost / vlm_cost / total_cost）都经
    _safe_number / _safe_count 归一（L5）：外部 JSON 的 null、缺键、空串、非数值回 0，
    不再出现"一个 null 崩掉整张看板"。单项费用一并展示，便于与 estimate_cost 对账。
    """
    return f"""<div class="metrics">
  <div class="card"><div class="num">{_safe_count(cost.get("llm_calls"))}</div><div class="label">LLM 调用次数</div></div>
  <div class="card"><div class="num">{_safe_count(cost.get("vlm_calls"))}</div><div class="label">VLM 调用次数</div></div>
  <div class="card"><div class="num">¥{_safe_number(cost.get("llm_cost")):.4f}</div><div class="label">LLM 估算费用</div></div>
  <div class="card"><div class="num">¥{_safe_number(cost.get("vlm_cost")):.4f}</div><div class="label">VLM 估算费用</div></div>
  <div class="card"><div class="num">¥{_safe_number(cost.get("total_cost")):.4f}</div><div class="label">估算费用</div></div>
</div>"""


def render_dashboard(records: list[HealingRecord], cost: dict | None = None) -> str:
    """把自愈记录渲染为一张完整 HTML 页面（内嵌样式，零依赖）。

    cost（T17）为可选成本统计（HealingReporter.cost_summary() 产出），提供时渲染成本卡片；
    其字段容错见 _render_cost（L5：null / 缺键 / 非数值一律回 0，不影响整页渲染）。
    """
    metrics = compute_metrics(records)
    rows: list[str] = []
    for rec in records:
        # H6 口径对齐：未成功（豁免 / dry_run / on_uncertain=fail）既非真自愈也非 flaky，
        # 只有成功的记录才按 verified 区分"真自愈 vs flaky 侥幸通过"。
        if not rec.success:
            kind = "➖ 未成功"
        elif rec.verified:
            kind = "✅ 真自愈"
        else:
            kind = "⚠️ flaky"
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


def write_dashboard(
    records: list[HealingRecord], out_path: str | Path, cost: dict | None = None
) -> Path:
    """把看板写入文件并返回路径。"""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard(records, cost), encoding="utf-8")
    return path
