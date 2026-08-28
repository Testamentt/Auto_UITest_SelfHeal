"""单元测试：T8 Playwright 原生解析 + 交叉校验（不触网/不依赖浏览器）。

覆盖：
1. cross_validate_interactive：一致 / 静态独有 / 原生独有 / 空集 / 无稳定定位器元素不参与；
2. interactive_candidates：原生优先、静态兜底、缺省回退；
3. parse_interactive_elements_native：无 page 返回 []。
"""

import pytest

from selfheal.agent.dom import (
    Element,
    cross_validate_interactive,
    interactive_candidates,
    parse_interactive_elements_native,
)
from selfheal.collect.collector import Scene

pytestmark = pytest.mark.unit


def _el(tag: str, testid: str | None = None, text: str = "") -> Element:
    """构造带 data-testid 的候选元素（稳定定位器 = [data-testid="..."]）。"""
    attrs = [("data-testid", testid)] if testid else []
    el = Element(tag, attrs)
    el.text = text
    return el


def test_cross_validate_consistent_when_same():
    """两来源候选完全一致（稳定定位器对齐）→ consistent，无单侧缺失。"""
    static = [_el("button", "submit-btn"), _el("input", "username")]
    native = [_el("button", "submit-btn"), _el("input", "username")]
    check = cross_validate_interactive(static, native)
    assert check.consistent is True
    assert check.total == 2 and check.matched == 2
    assert check.only_static == [] and check.only_native == []


def test_cross_validate_detects_static_only():
    """原生缺失某候选（静态独有）→ 不一致，明细可溯源。"""
    static = [_el("button", "submit-btn"), _el("input", "extra-old")]
    native = [_el("button", "submit-btn")]
    check = cross_validate_interactive(static, native)
    assert check.consistent is False
    assert check.only_static == ['[data-testid="extra-old"]']
    assert check.only_native == []


def test_cross_validate_detects_native_only():
    """静态缺失某候选（原生独有）→ 不一致。"""
    static = [_el("button", "submit-btn")]
    native = [_el("button", "submit-btn"), _el("a", "link-new")]
    check = cross_validate_interactive(static, native)
    assert check.consistent is False
    assert check.only_static == []
    assert check.only_native == ['[data-testid="link-new"]']


def test_cross_validate_empty_set_not_consistent():
    """两来源都无候选（total=0）→ 不判一致（无可对齐对象）。"""
    check = cross_validate_interactive([], [])
    assert check.consistent is False and check.total == 0


def test_cross_validate_ignores_unstable_elements():
    """无稳定定位器的元素（无 testid/id/text/aria）不参与对齐。"""
    bare_static = Element("button", [])  # 四类属性全缺 → build_stable_selector 返回 None
    check = cross_validate_interactive(
        [bare_static, _el("button", "submit-btn")], [_el("button", "submit-btn")]
    )
    assert check.consistent is True and check.total == 1


def test_interactive_candidates_prefers_native():
    """有原生解析结果（page 采集）→ 优先原生，不再走静态解析。"""
    native = [_el("button", "native-btn")]
    scene = Scene(url="x", dom_snapshot="<html><body></body></html>", native_elements=native)
    assert interactive_candidates(scene) is native


def test_interactive_candidates_falls_back_to_static():
    """无原生结果（如无 page 的纯逻辑路径）→ 回退静态 DOM 快照解析。"""
    scene = Scene(
        url="x", dom_snapshot='<html><body><button data-testid="s">go</button></body></html>'
    )
    candidates = interactive_candidates(scene)
    assert len(candidates) == 1
    assert candidates[0].attr("data-testid") == "s"


def test_interactive_candidates_empty_without_dom():
    """既无原生也无 DOM 快照 → 空列表（不炸）。"""
    assert interactive_candidates(Scene(url="x")) == []


def test_native_parser_requires_page():
    """原生解析无 page → []（纯逻辑环境安全降级）。"""
    assert parse_interactive_elements_native(None) == []
