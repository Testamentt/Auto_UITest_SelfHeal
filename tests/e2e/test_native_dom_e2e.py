"""端到端测试：T8 Playwright 原生解析与静态 HTMLParser 交叉校验（需浏览器内核，-m e2e）。

验证真实页面上：
1. SceneCollector.capture() 产出原生解析结果（native_elements）与交叉校验（dom_cross_check）；
2. 两来源（静态 HTMLParser vs Playwright 原生）交互候选一致（consistent）——
   自解析未偏离真实 DOM 结构（T8 的"交叉校验"价值：静态口径可信）；
3. 策略链经 interactive_candidates 用原生候选能正常产出修复候选（native 路径跑通）。
"""

import pytest

from selfheal.agent.dom import build_stable_selector, interactive_candidates
from selfheal.agent.strategies.heuristic import HeuristicStrategy
from selfheal.collect.collector import SceneCollector
from tests.e2e.pages.demo_page import DemoPage
from tests.e2e.pages.popup_page import PopupPage

pytestmark = pytest.mark.e2e


def test_demo_page_native_and_static_consistent(page):
    """演示页：两来源交互候选一致，且 [data-testid="submit-btn"] 落在对齐集合内。"""
    demo = DemoPage(page)
    demo.open()
    scene = SceneCollector(page).capture()
    assert scene.native_elements  # 原生解析已产出候选
    assert scene.dom_cross_check is not None
    check = scene.dom_cross_check
    assert check.consistent is True  # 静态解析未偏离真实 DOM
    assert check.total >= 1
    # 登录按钮的稳定定位器必须两来源都解析得出（matched 参与计数）
    assert any(
        build_stable_selector(el) == '[data-testid="submit-btn"]' for el in scene.native_elements
    )


def test_popup_page_native_and_static_consistent(page):
    """弹窗演示页（含弹窗 overlay 元素）同样两来源一致。"""
    demo = PopupPage(page)
    demo.open()
    scene = SceneCollector(page).capture()
    assert scene.dom_cross_check is not None
    assert scene.dom_cross_check.consistent is True


def test_heuristic_uses_native_candidates(page):
    """策略链候选来源优先原生：真实场景下 heuristic 用原生候选正常产出修复。"""
    demo = DemoPage(page)
    demo.open()
    scene = SceneCollector(page).capture()
    # 原生候选是策略链实际使用的候选（interactive_candidates 优先 native_elements）
    assert len(interactive_candidates(scene)) == len(scene.native_elements)
    cand = HeuristicStrategy().repair(scene, "#submit-btn-old", "登录按钮")
    assert cand is not None
    assert cand.confidence >= 0.85  # 登录按钮强信号：子串匹配
