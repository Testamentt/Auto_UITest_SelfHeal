"""单元测试：H2 wrapped 自愈重试闭环（纯逻辑，不依赖浏览器）。

覆盖 HealingLocator.__getattr__→_healing_action 的 wrapped 完整循环：
动作超时→自愈→重试→再次超时→二次自愈(use_knowledge=False)→最终重试，
以及非超时异常原样上抛与 _is_timeout_error 判定。
"""

import pytest

from selfheal.agent.orchestrator import HealOutcome
from selfheal.config import HealingConfig
from selfheal.engine.healing_locator import HealingLocator, _is_timeout_error

pytestmark = pytest.mark.unit


class _MyTimeout(Exception):
    """类名含 Timeout，模拟 playwright 超时（供无浏览器单测）。"""


def _raise(exc):
    def _fn():
        raise exc

    return _fn


def _clickable(fn):
    class _L:
        def click(self, *a, **k):
            return fn()

    return _L()


class _Page:
    """selector → 对应 fake locator。"""

    def __init__(self, resolved):
        self._resolved = resolved

    def locator(self, selector, **k):
        return self._resolved[selector]


class _RecOrch:
    """记录 use_knowledge 调用，按序弹 HealOutcome。"""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls: list[bool] = []

    def run(self, sel, desc, failure=None, use_knowledge=True):
        self.calls.append(use_knowledge)
        return self._outcomes.pop(0)


def test_heal_and_retry():
    orch = _RecOrch([HealOutcome(success=True, new_selector="#new", confidence=0.9)])
    page = _Page(
        {
            "#old": _clickable(_raise(_MyTimeout("not found"))),
            "#new": _clickable(lambda: "relocated-ok"),
        }
    )
    hl = HealingLocator(page.locator("#old"), page, "#old", HealingConfig(), orch)
    assert hl.click() == "relocated-ok"
    assert orch.calls == [True]  # 首次自愈用知识库


def test_secondary_healing_bounded():
    """自愈后的选择器仍超时 → 二次自愈（use_knowledge=False），且最多两次。"""
    orch = _RecOrch(
        [
            HealOutcome(success=True, new_selector="#new", confidence=0.9),
            HealOutcome(success=True, new_selector="#new2", confidence=0.9),
        ]
    )
    page = _Page(
        {
            "#old": _clickable(_raise(_MyTimeout("not found"))),
            "#new": _clickable(_raise(_MyTimeout("also failed"))),
            "#new2": _clickable(lambda: "second-ok"),
        }
    )
    hl = HealingLocator(page.locator("#old"), page, "#old", HealingConfig(), orch)
    assert hl.click() == "second-ok"
    assert orch.calls == [True, False]  # T4 不变量：第二次跳过知识缓存


def test_non_timeout_raises():
    orch = _RecOrch([])
    page = _Page({"#old": _clickable(_raise(ValueError("boom")))})
    hl = HealingLocator(page.locator("#old"), page, "#old", HealingConfig(), orch)
    with pytest.raises(ValueError):
        hl.click()
    assert orch.calls == []  # 非超时异常不触发自愈


def test_is_timeout_error():
    assert _is_timeout_error(_MyTimeout("x")) is True  # 类名含 Timeout
    assert _is_timeout_error(TimeoutError("x")) is True  # 内置 TimeoutError
    assert _is_timeout_error(ValueError("x")) is False
