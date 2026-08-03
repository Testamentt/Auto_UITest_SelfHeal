"""单元测试：自愈看板 v0 渲染（不依赖浏览器）。"""

import pytest

from selfheal.reporting.dashboard import render_dashboard, write_dashboard
from selfheal.reporting.hooks import HealingRecord

pytestmark = pytest.mark.unit


def test_render_contains_audit_row():
    rec = HealingRecord(
        original_selector="#old",
        new_selector='[data-testid="submit-btn"]',
        strategy="heuristic",
        confidence=0.95,
        root_cause="not_found",
        success=True,
    )
    html = render_dashboard([rec])
    assert "自愈审计看板" in html
    assert "#old" in html
    assert "heuristic" in html
    assert "0.95" in html


def test_render_escapes_html():
    rec = HealingRecord("#<script>", None, "h", 0.5, "x", True)
    html = render_dashboard([rec])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_render_empty():
    assert "(暂无自愈记录)" in render_dashboard([])


def test_write_dashboard(tmp_path):
    out = write_dashboard([], tmp_path / "dash.html")
    assert out.exists()
    assert "自愈审计看板" in out.read_text(encoding="utf-8")
