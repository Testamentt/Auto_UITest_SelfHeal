"""单元测试：自愈看板渲染（指标摘要 + 审计表，T3，不依赖浏览器）。"""

import pytest

from selfheal.reporting.dashboard import render_dashboard, write_dashboard
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


def test_write_dashboard(tmp_path):
    out = write_dashboard([_rec()], tmp_path / "nested" / "dash.html")
    assert out.exists()
    assert "AI 自愈看板" in out.read_text(encoding="utf-8")
