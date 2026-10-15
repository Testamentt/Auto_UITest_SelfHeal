"""单元测试：自愈看板渲染（指标摘要 + 审计表，T3，不依赖浏览器）。"""

import pytest

from selfheal.reporting.dashboard import render_dashboard, write_dashboard
from selfheal.reporting.fix_proposals import estimate_cost
from selfheal.reporting.hooks import HealingRecord

pytestmark = pytest.mark.unit


def _rec(selector="#old", strategy="heuristic", root_cause="not_found", success=True):
    return HealingRecord(
        selector, '[data-testid="submit-btn"]', strategy, 0.95, root_cause, success
    )


def test_render_contains_audit_row():
    html = render_dashboard([_rec()])
    assert "AI 自愈看板" in html
    assert "#old" in html
    assert "heuristic" in html
    assert "0.95" in html


def test_render_contains_metrics_summary():
    records = [_rec(success=True), _rec(strategy="semantic", root_cause="covered", success=True)]
    html = render_dashboard(records)
    assert "自愈总数" in html
    assert "自愈成功率" in html
    assert "策略命中分布" in html
    assert "根因分布" in html
    assert "100%" in html  # 2/2 成功率


def test_render_escapes_html():
    rec = HealingRecord("#<script>", None, "h", 0.5, "x", True)
    html = render_dashboard([rec])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_empty():
    assert "(暂无自愈记录)" in render_dashboard([])


def test_render_failed_record_not_labeled_flaky():
    """H6 口径对齐：success=False（豁免/dry_run）不显示为 flaky 侥幸通过，也不算进卡片。"""
    rec = HealingRecord("#pay", None, None, 0.0, "high_risk_page_excluded", False, False)
    html = render_dashboard([rec])
    row = next(line for line in html.splitlines() if "#pay" in line)
    assert "➖ 未成功" in row
    assert "flaky" not in row
    assert '<div class="num">0</div><div class="label">flaky 侥幸通过</div>' in html


def test_write_dashboard(tmp_path):
    out = write_dashboard([_rec()], tmp_path / "nested" / "dash.html")
    assert out.exists()
    assert "AI 自愈看板" in out.read_text(encoding="utf-8")


# --- L5：成本卡片容错（外部 JSON 的 null 不得让整张看板渲染失败） ---


def test_l5_cost_card_renders_when_all_fields_null():
    """L5：cost 全字段为 null（外部 JSON）→ 归一为 0，整张看板（含审计表）仍渲染成功。"""
    cost = {
        "llm_calls": None,
        "vlm_calls": None,
        "llm_cost": None,
        "vlm_cost": None,
        "total_cost": None,
    }
    html = render_dashboard([_rec()], cost)  # 修复前：total_cost 为 None → TypeError
    assert "#old" in html  # 审计表未被成本字段拖垮
    assert "LLM 调用次数" in html and "估算费用" in html
    assert html.count("¥0.0000") == 3  # LLM / VLM / 合计 三项费用均为 0
    assert '<div class="num">0</div><div class="label">LLM 调用次数</div>' in html


def test_l5_cost_card_tolerates_bad_values_and_missing_keys():
    """L5：缺键 / 空串 / 非数值 / nan / inf 一律回 0（不抛 TypeError/ValueError）。"""
    cost = {
        "llm_calls": "",
        "vlm_calls": "abc",
        "llm_cost": float("nan"),
        "vlm_cost": float("inf"),
        # total_cost 故意缺键
    }
    html = render_dashboard([], cost)
    assert "¥0.0000" in html
    assert '<div class="num">0</div><div class="label">LLM 调用次数</div>' in html
    assert '<div class="num">0</div><div class="label">VLM 调用次数</div>' in html


def test_l5_cost_card_still_renders_contract_values():
    """L5：正常数值（含 estimate_cost 契约产出）不被归一化误伤，精度保留。"""
    html = render_dashboard([_rec()], estimate_cost(2, 1))
    assert "¥0.0400" in html  # LLM 2 × 0.02
    assert "¥0.0500" in html  # VLM 1 × 0.05
    assert "¥0.0900" in html  # 合计
    assert "LLM 估算费用" in html and "VLM 估算费用" in html


def test_l5_cost_card_accepts_numeric_strings_and_ignores_na():
    """L5：数值字符串可解析；"N/A" 之类不可解析值回 0（外部汇总来源常见形态）。"""
    html = render_dashboard([], {"llm_calls": "3", "total_cost": "N/A"})
    assert '<div class="num">3</div><div class="label">LLM 调用次数</div>' in html
    assert "¥0.0000" in html
