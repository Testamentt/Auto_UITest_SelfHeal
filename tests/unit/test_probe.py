# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""单元测试：自愈触发前的快速探测（不依赖浏览器）。

回归的问题：开启自愈时，定位失败要先等满 Playwright 默认超时（30s）才进自愈闭环，
单条失效步骤因此 +30s（用例内实测 30.63s → 0.63s）。快速探测以短超时提前判定"目标当下
不可用"，直接进自愈，把代价降到 `probe.timeout_ms`。

本文件锁定的契约：
1. 探测通过 → 走原生动作，**不触发自愈**（正常路径零额外代价）；
2. 探测未通过 → **立刻**进自愈，不再白等原生超时（核心收益）；
3. `probe.enabled=false` → 行为与改动前完全一致（零回归）；
4. 探测自身异常 / 非超时异常 → 不作为自愈触发（只提前发现"不可用"，不替换原生错误语义）；
5. 未开启自愈 → 恒不探测。
"""

from __future__ import annotations

import pytest

from selfheal.agent.orchestrator import HealOutcome
from selfheal.config import HealingConfig, ProbeConfig
from selfheal.engine.healing_locator import HealingLocator, _is_timeout_error, _ProbeTimeout

pytestmark = pytest.mark.unit


class _MyTimeout(Exception):
    """类名含 Timeout：模拟 Playwright 超时（无浏览器单测的既有约定）。"""


class _ProbeLocator:
    """可配置探测结果的 locator 替身。

    实现生产 Locator 的探测 API：`count` / `wait_for`（探测主路径）。
    `wait_for` 按 `wait_ok` 决定正常返回还是抛超时——模拟"元素在预算内就绪 / 未就绪"。
    """

    def __init__(
        self,
        *,
        count: int = 1,
        visible: bool = True,
        enabled: bool = True,
        action_result: object = "ok",
        action_raises: BaseException | None = None,
        probe_raises: BaseException | None = None,
        wait_ok: bool | None = None,
        timeout_calls: list | None = None,
    ):
        self._count = count
        self._visible = visible
        self._enabled = enabled
        self._action_result = action_result
        self._action_raises = action_raises
        self._probe_raises = probe_raises
        # wait_ok=None 时按可见性推导（visible 即"能在预算内就绪"）
        self._wait_ok = visible if wait_ok is None else wait_ok
        self._timeout_calls = timeout_calls if timeout_calls is not None else []
        self.action_calls = 0

    def count(self):
        return self._count

    def wait_for(self, state=None, timeout=None):
        self._timeout_calls.append(timeout)
        if self._probe_raises is not None:
            raise self._probe_raises
        if not self._wait_ok:
            raise _MyTimeout(f"Timeout {timeout}ms exceeded waiting for state={state}")
        return None

    def is_visible(self, timeout=None):
        self._timeout_calls.append(timeout)
        if self._probe_raises is not None:
            raise self._probe_raises
        return self._visible

    def click(self, *args, **kwargs):
        self.action_calls += 1
        if self._action_raises is not None:
            raise self._action_raises
        return self._action_result


class _Page:
    """selector → fake locator。"""

    def __init__(self, resolved: dict):
        self._resolved = resolved

    def locator(self, selector, **kwargs):
        return self._resolved[selector]


class _RecOrch:
    """记录闭环调用；按序返回 HealOutcome。"""

    def __init__(self, outcomes: list | None = None):
        self._outcomes = list(outcomes or [])
        self.calls: list[str] = []
        self.failures: list = []

    def run(self, sel, desc, failure=None, use_knowledge=True, action=None):
        self.calls.append(sel)
        self.failures.append(failure)
        if self._outcomes:
            return self._outcomes.pop(0)
        return HealOutcome(success=True, new_selector="#new", confidence=0.9)

    def commit_pending(self, attempt_id):
        return None

    def discard_pending(self, attempt_id):
        return None


def _cfg(*, probe_enabled: bool = True, timeout_ms: int = 2500) -> HealingConfig:
    cfg = HealingConfig()
    cfg.probe = ProbeConfig(enabled=probe_enabled, timeout_ms=timeout_ms)
    return cfg


# ---------------------------------------------------------------------------
# 1. 探测通过 → 不触发自愈
# ---------------------------------------------------------------------------


def test_probe_passes_means_native_action_only():
    """目标当下可用：直接执行原生动作，闭环一次都不跑（正常路径零额外代价）。"""
    orch = _RecOrch()
    target = _ProbeLocator(action_result="clicked")
    page = _Page({"#btn": target})
    locator = HealingLocator(page.locator("#btn"), page, "#btn", _cfg(), orch)

    assert locator.click() == "clicked"
    assert orch.calls == []  # 未触发自愈
    assert target.action_calls == 1


def test_probe_uses_configured_timeout():
    """探测用的是配置里的 timeout_ms，而不是默认 30s。"""
    calls: list = []
    target = _ProbeLocator(timeout_calls=calls)
    page = _Page({"#btn": target})
    locator = HealingLocator(page.locator("#btn"), page, "#btn", _cfg(timeout_ms=1234), _RecOrch())

    locator.click()
    assert calls and all(t == 1234 for t in calls)


# ---------------------------------------------------------------------------
# 2. 探测未通过 → 立刻进自愈（核心收益）
# ---------------------------------------------------------------------------


def test_invisible_target_triggers_healing_without_native_timeout():
    """元素不可见：**不调用原生动作**（不白等 30s），直接走自愈 + 用新定位器重试。"""
    orch = _RecOrch([HealOutcome(success=True, new_selector="#new", confidence=0.9)])
    broken = _ProbeLocator(visible=False)
    fixed = _ProbeLocator(action_result="healed-click")
    page = _Page({"#old": broken, "#new": fixed})
    locator = HealingLocator(page.locator("#old"), page, "#old", _cfg(), orch)

    assert locator.click() == "healed-click"
    assert broken.action_calls == 0  # 关键：没有执行注定超时的原生动作
    assert orch.calls == ["#old"]
    assert fixed.action_calls == 1


def test_disabled_target_does_not_trigger_healing():
    """元素可见但 disabled：**不**提前自愈——交原生动作等待（避免误杀"稍后启用"）。

    `disabled` 是常见且**可恢复**的状态（表单校验通过后提交按钮才启用），提前判"不可用"
    会让每次正常交互都多跑一轮自愈。探测只负责发现"选择器失效"这类注定失败的场景。
    """
    orch = _RecOrch()
    target = _ProbeLocator(enabled=False, action_result="native")
    page = _Page({"#btn": target})
    locator = HealingLocator(page.locator("#btn"), page, "#btn", _cfg(), orch)

    assert locator.click() == "native"
    assert orch.calls == []
    assert target.action_calls == 1


def test_probe_failure_reaches_orchestrator_as_timeout_semantics():
    """探测未通过时交给闭环的 failure 与真实超时同形（诊断/归因口径不分叉）。"""
    orch = _RecOrch()
    broken = _ProbeLocator(visible=False)
    page = _Page({"#old": broken, "#new": _ProbeLocator()})
    locator = HealingLocator(page.locator("#old"), page, "#old", _cfg(), orch)

    locator.click()
    failure = orch.failures[0]
    assert failure is not None
    assert failure.failure_type == "_ProbeTimeout"
    assert "快速探测" in (failure.message or "")


def test_probe_timeout_is_recognized_as_timeout():
    """`_ProbeTimeout` 必须被 `_is_timeout_error` 认作超时（两端继承契约）。"""
    assert _is_timeout_error(_ProbeTimeout("probe miss"))
    assert issubclass(_ProbeTimeout, TimeoutError)


# ---------------------------------------------------------------------------
# 3. 关闭探测 → 零回归
# ---------------------------------------------------------------------------


def test_probe_disabled_restores_original_flow():
    """probe.enabled=false：照旧先执行原生动作，超时后才自愈（行为与改动前一致）。"""
    orch = _RecOrch([HealOutcome(success=True, new_selector="#new", confidence=0.9)])
    broken = _ProbeLocator(visible=False, action_raises=_MyTimeout("not found"))
    fixed = _ProbeLocator(action_result="ok")
    page = _Page({"#old": broken, "#new": fixed})
    locator = HealingLocator(page.locator("#old"), page, "#old", _cfg(probe_enabled=False), orch)

    assert locator.click() == "ok"
    assert broken.action_calls == 1  # 探测关闭 → 原生动作照常执行（再超时）
    assert orch.calls == ["#old"]


def test_probe_disabled_invisible_target_does_not_skip_action():
    """关闭探测时，即使元素不可见也照常执行原生动作（不做任何提前判断）。"""
    orch = _RecOrch()
    target = _ProbeLocator(visible=False, action_result="native")
    page = _Page({"#btn": target})
    locator = HealingLocator(page.locator("#btn"), page, "#btn", _cfg(probe_enabled=False), orch)

    assert locator.click() == "native"
    assert target.action_calls == 1
    assert orch.calls == []


# ---------------------------------------------------------------------------
# 4. 探测自身异常 / 非超时异常不触发自愈
# ---------------------------------------------------------------------------


def test_probe_internal_error_falls_back_to_native_action():
    """探测自身抛非超时异常（页面已关闭等）：视为探测通过，交原生动作报真正的错。"""
    orch = _RecOrch()
    target = _ProbeLocator(probe_raises=RuntimeError("target closed"), action_result="native")
    page = _Page({"#btn": target})
    locator = HealingLocator(page.locator("#btn"), page, "#btn", _cfg(), orch)

    assert locator.click() == "native"
    assert orch.calls == []


def test_probe_timeout_error_is_treated_as_miss():
    """探测真的抛超时异常 → 视为探测未通过（提前进自愈）。"""
    orch = _RecOrch([HealOutcome(success=True, new_selector="#new", confidence=0.9)])
    broken = _ProbeLocator(probe_raises=_MyTimeout("probe timed out"))
    fixed = _ProbeLocator(action_result="ok")
    page = _Page({"#old": broken, "#new": fixed})
    locator = HealingLocator(page.locator("#old"), page, "#old", _cfg(), orch)

    assert locator.click() == "ok"
    assert broken.action_calls == 0
    assert orch.calls == ["#old"]


def test_probe_multiple_matches_does_not_trigger_healing():
    """多命中：探测放行，交原生动作按 strict-mode 原语义报错。

    多命中是"选择器写得过宽"（编程错误），不是"元素暂时不可用"；提前自愈会掩盖它。
    （对比：`selector_exists` 在**采纳候选时**要求 count==1，那是另一层——见 D21。）
    """
    orch = _RecOrch()
    target = _ProbeLocator(count=2, action_result="native")
    page = _Page({"#btn": target})
    locator = HealingLocator(page.locator("#btn"), page, "#btn", _cfg(), orch)

    assert locator.click() == "native"
    assert orch.calls == []


# ---------------------------------------------------------------------------
# 5. 未开启自愈 → 恒不探测
# ---------------------------------------------------------------------------


def test_probe_skipped_when_healing_disabled():
    """enabled=False（关闭自愈）：探测不介入，动作直接透传。"""
    calls: list = []
    target = _ProbeLocator(visible=False, timeout_calls=calls, action_result="native")
    page = _Page({"#btn": target})
    locator = HealingLocator(page.locator("#btn"), page, "#btn", _cfg(), _RecOrch(), enabled=False)

    assert locator.click() == "native"
    assert calls == []  # 探测压根没跑


# ---------------------------------------------------------------------------
# 6. 探测不改变自愈失败时的兜底语义
# ---------------------------------------------------------------------------


def test_probe_miss_with_unhealable_target_raises_healing_failed():
    """探测未通过 + 自愈也救不回 + 无 fallback → 与"等满超时后自愈失败"同一异常。"""
    from selfheal.engine.healing_locator import HealingFailedError

    orch = _RecOrch([HealOutcome(success=False, root_cause="not_found")])
    broken = _ProbeLocator(visible=False)
    page = _Page({"#old": broken})
    locator = HealingLocator(page.locator("#old"), page, "#old", _cfg(), orch)

    with pytest.raises(HealingFailedError):
        locator.click()


def test_probe_miss_uses_fallback_when_configured():
    """探测未通过 + 自愈失败 + 配了 fallback → 用备用定位器完成动作（D6 兜底不变）。"""
    orch = _RecOrch([HealOutcome(success=False, root_cause="not_found")])
    broken = _ProbeLocator(visible=False)
    fallback = _ProbeLocator(action_result="fallback-ok")
    page = _Page({"#old": broken, "#fb": fallback})
    locator = HealingLocator(page.locator("#old"), page, "#old", _cfg(), orch, fallback="#fb")

    assert locator.click() == "fallback-ok"
    assert broken.action_calls == 0
