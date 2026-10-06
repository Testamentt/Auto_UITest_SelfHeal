"""单元测试：2026-10-08 代码审查修复回归（H1/H2/H3/M2/M6/M7/M9/M11 + 配置值域）。

每条用例标题都带发现项编号，便于与 `docs/reviews/2026-10-08-code-review.md` 对齐。
不依赖浏览器（engine 侧一律用 fake page / fake playwright）。
"""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from selfheal.agent.persistence import PersistenceHandler
from selfheal.config import ActionWaitConfig, LLMConfig, Settings, VisionConfig
from selfheal.engine.popup_guard import (
    PopupGuard,
    _close_hint_strength,
    _is_close_hint,
)
from selfheal.engine.smart_wait import wait_until_stable
from selfheal.knowledge.schema import PopupFeature

pytestmark = pytest.mark.unit


# --- H3 / M6：配置值域与可配置性 ---


def test_llm_timeout_and_token_limits_are_configurable():
    """H3：llm 段此前无法配置 timeout_s/max_tokens（extra='forbid' 直接拒绝），现可配。"""
    settings = Settings.model_validate(
        {"llm": {"timeout_s": 30, "max_tokens": 4096, "max_retries": 0}}
    )
    assert settings.llm.timeout_s == 30
    assert settings.llm.max_tokens == 4096
    assert settings.llm.max_retries == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"llm": {"timeout_s": 0}},
        {"llm": {"timeout_s": -1}},
        {"llm": {"max_tokens": 0}},
        {"llm": {"max_retries": -1}},
        {"vision": {"timeout_s": 0}},
        {"vision": {"max_tokens": 0}},
        {"vision": {"max_image_bytes": 0}},
        {"healing": {"action_wait": {"timeout_ms": 0}}},
    ],
)
def test_invalid_bounds_are_rejected_at_load(payload):
    """M6/H3：非法值域在加载期报错，而不是运行期变成"无界等待/不可用"。"""
    with pytest.raises(ValidationError):
        Settings.model_validate(payload)


def test_action_wait_defaults_are_positive():
    cfg = ActionWaitConfig()
    assert cfg.timeout_ms > 0 and cfg.stable_ms >= 0
    assert LLMConfig().max_tokens > 0 and VisionConfig().max_image_bytes > 0


# --- M6：smart_wait 边界与预算 ---


class _WaitLocator:
    """记录 wait_for(timeout=...) 的 fake Locator。"""

    def __init__(self, box=(0, 0, 10, 10)):
        self._box = box
        self.timeouts: list[int] = []

    def wait_for(self, state, timeout):  # noqa: ARG002 - 只关心 timeout
        self.timeouts.append(timeout)

    def bounding_box(self):
        return self._box


def test_wait_rejects_non_positive_timeout():
    """M6：timeout=0 在 Playwright 里是"不超时"，必须显式拒绝而非静默无界等待。"""
    with pytest.raises(ValueError):
        wait_until_stable(_WaitLocator(), timeout_ms=0)
    with pytest.raises(ValueError):
        wait_until_stable(_WaitLocator(), timeout_ms=1000, stable_ms=-5)


def test_wait_reserves_budget_for_stability_window():
    """M6：可见性等待只占 (timeout - stable)，给稳定判定留独立窗口。"""
    locator = _WaitLocator()
    wait_until_stable(locator, timeout_ms=2000, stable_ms=300, poll_ms=100)
    assert locator.timeouts == [1700]


def test_wait_poll_interval_has_floor(monkeypatch):
    """M6：poll_ms<=0 不再退化成忙等。"""
    import types

    from selfheal.engine import smart_wait

    class _JitterLocator(_WaitLocator):
        """位置每次都变 → 永远不稳定，走到超时分支。"""

        def __init__(self):
            super().__init__()
            self._n = 0

        def bounding_box(self):
            self._n += 1
            return (self._n, 0, 10, 10)

    sleeps: list[float] = []
    ticks = iter([0.0, 0.0, 0.5, 5.0, 9.0])
    fake_time = types.SimpleNamespace(monotonic=lambda: next(ticks), sleep=sleeps.append)
    monkeypatch.setattr(smart_wait, "time", fake_time)

    with pytest.raises(TimeoutError):
        wait_until_stable(_JitterLocator(), timeout_ms=1000, stable_ms=300, poll_ms=0)
    assert sleeps and min(sleeps) >= 0.02


# --- M7：BrowserManager 生命周期 ---


def test_browser_manager_cleans_up_when_launch_fails(monkeypatch):
    """M7：launch 失败时也必须回收驱动子进程（此前 __exit__ 不会执行 → 进程泄漏）。"""
    from selfheal.engine import browser as browser_mod

    stopped = {"n": 0}

    class _Chromium:
        def launch(self, **kwargs):
            raise RuntimeError("chrome not installed")

    class _Driver:
        chromium = _Chromium()

        def stop(self):
            stopped["n"] += 1

    class _SyncPlaywright:
        def start(self):
            return _Driver()

    monkeypatch.setattr(browser_mod, "sync_playwright", lambda: _SyncPlaywright())
    with (
        pytest.raises(RuntimeError, match="chrome not installed"),
        browser_mod.BrowserManager(Settings()),
    ):
        pass
    assert stopped["n"] == 1


def test_browser_manager_exit_is_idempotent(monkeypatch):
    """M7：重复 __exit__ 不再重复 close/stop；创建的 context 由管理器统一关闭。"""
    from selfheal.engine import browser as browser_mod

    calls = {"browser_close": 0, "stop": 0, "context_close": 0}

    class _Context:
        def close(self):
            calls["context_close"] += 1

        def new_page(self):
            return object()

    class _Browser:
        def new_context(self, **kwargs):
            return _Context()

        def close(self):
            calls["browser_close"] += 1

    class _Driver:
        def __init__(self):
            self.chromium = type("C", (), {"launch": lambda self, **kw: _Browser()})()

        def stop(self):
            calls["stop"] += 1

    class _SyncPlaywright:
        def start(self):
            return _Driver()

    monkeypatch.setattr(browser_mod, "sync_playwright", lambda: _SyncPlaywright())
    manager = browser_mod.BrowserManager(Settings())
    with manager:
        manager.new_page()
    manager.__exit__(None, None, None)  # 再次退出必须是 no-op
    assert calls == {"browser_close": 1, "stop": 1, "context_close": 1}


def test_browser_manager_requires_enter_before_new_context():
    """M7：未进入上下文时显式报错（此前是 assert，`python -O` 下会被剥离）。"""
    from selfheal.engine.browser import BrowserManager

    with pytest.raises(RuntimeError):
        BrowserManager(Settings()).new_context()


# --- M9：暂存淘汰 / 显式丢弃 / 幂等窗口有界 ---


def _handler() -> PersistenceHandler:
    settings = Settings()
    settings.healing.enabled = True
    return PersistenceHandler(
        settings, knowledge=object(), reporter=object(), embedding=None, page=None
    )


def _outcome(attempt_id: str | None = None):
    from selfheal.agent.context import HealOutcome

    return HealOutcome(success=True, new_selector="#new", confidence=0.9, attempt_id=attempt_id)


def _context():
    from selfheal.agent.context import HealingContext
    from selfheal.collect.collector import Scene

    return HealingContext(
        scene=Scene(url="u"),
        original_selector="#old",
        description=None,
        dom_fingerprint=None,
        page_fingerprint="",
    )


def test_pending_overflow_is_logged(caplog):
    """M9：暂存超上限被丢弃时必须可见（此前静默 pop）。"""
    handler = _handler()
    with caplog.at_level(logging.WARNING):
        for _ in range(handler._PENDING_LIMIT + 2):
            handler.stage(_context(), _outcome())
    # 上限语义：达到上限即淘汰最旧，故稳态长度为 LIMIT-1；关键是"有界 + 有日志"
    assert len(handler._pending) <= handler._PENDING_LIMIT
    assert any("丢弃最旧暂存" in record.getMessage() for record in caplog.records)


def test_discard_pending_logs_and_removes(caplog):
    """M9：二次自愈替换暂存前显式丢弃，并记日志；未知 id 是安全 no-op。"""
    handler = _handler()
    handler.stage(_context(), _outcome())
    attempt_id = next(iter(handler._pending))
    with caplog.at_level(logging.WARNING):
        handler.discard_pending(attempt_id)
        handler.discard_pending("nope")
        handler.discard_pending(None)
    assert attempt_id not in handler._pending
    assert sum("显式丢弃" in record.getMessage() for record in caplog.records) == 1


def test_committed_window_is_bounded(monkeypatch):
    """M9：幂等记忆有界，防长会话无界增长。"""
    handler = _handler()
    monkeypatch.setattr(PersistenceHandler, "_COMMITTED_LIMIT", 3)
    monkeypatch.setattr(handler, "_persist", lambda context, outcome: None)
    for _ in range(6):
        handler.stage(_context(), _outcome())
        handler.commit_pending(next(iter(handler._pending)))
    assert len(handler._committed) == 3


# --- M11：选择器字面量转义 ---


class _El:
    def __init__(self, attrs=None, text=""):
        self._attrs = attrs or {}
        self.text = text

    def attr(self, name):
        return self._attrs.get(name)

    def field(self, name):
        return self.text if name == "text" else self._attrs.get(name)


def test_stable_selector_escapes_quotes_and_newlines():
    """M11：值里带引号会生成非法选择器，必须转义；换行折成空格。"""
    from selfheal.agent.dom.selector_builder import build_stable_selector

    assert build_stable_selector(_El(attrs={"data-testid": 'a"b'})) == '[data-testid="a\\"b"]'
    assert build_stable_selector(_El(text='点"我"呀')) == 'text="点\\"我\\"呀"'
    assert build_stable_selector(_El(text="两\n行")) == 'text="两 行"'
    assert build_stable_selector(_El(attrs={"aria-label": 'a"b'})) == '[aria-label="a\\"b"]'


# --- H1：弹窗关闭的分级命中与容器限定 ---


def test_close_hint_strength_levels():
    """H1：整串标签=强信号；子串命中（"关闭订单"）=弱信号；无关文案=不命中。"""
    assert _close_hint_strength("关闭", "", "") == 2
    assert _close_hint_strength("", "close", "") == 2
    assert _close_hint_strength("", "", "×") == 2
    assert _close_hint_strength("close-order", "", "关闭订单") == 1
    assert _close_hint_strength("提交", "submit-btn", "确定") == 0
    assert _is_close_hint("关闭订单", "", "") is True  # 兼容入口：弱信号也算命中


class _FakeEl:
    def __init__(self, attrs=None, text=""):
        self._attrs = attrs or {}
        self._text = text
        self.clicked = 0

    def get_attribute(self, name):
        return self._attrs.get(name)

    def inner_text(self):
        return self._text

    def is_visible(self):
        return True

    def click(self, **kwargs):
        self.clicked += 1


class _Collection:
    def __init__(self, items):
        self._items = list(items)

    def count(self):
        return len(self._items)

    def nth(self, index):
        return self._items[index]

    @property
    def first(self):
        return self._items[0]


class _Container(_FakeEl):
    def __init__(self, candidates=(), by_selector=None, text="欢迎弹窗"):
        super().__init__(text=text)
        self._candidates = list(candidates)
        self._by_selector = by_selector or {}
        self.looked_up: list[str] = []

    def locator(self, selector):
        self.looked_up.append(selector)
        if selector == "button, a, [role='button']":
            return _Collection(self._candidates)
        return _Collection(self._by_selector.get(selector, []))


class _Page:
    def __init__(self, container):
        self._container = container
        self.located: list[str] = []

    def locator(self, selector):
        self.located.append(selector)
        return _Collection([self._container])


class _Knowledge:
    def __init__(self, feature=None):
        self._feature = feature
        self.added: list[PopupFeature] = []

    def find_popup(self, signature):
        return self._feature

    def add_popup(self, feature):
        self.added.append(feature)


class _Clickable:
    def __init__(self, el):
        self._el = el

    def count(self):
        return 1

    @property
    def first(self):
        return self._el


def test_popup_knowledge_click_stays_inside_container():
    """H1：知识命中的关闭点击必须限定在弹窗容器内，不再全页取 .first。"""
    dismiss = _FakeEl(attrs={"data-testid": "dismiss"})
    container = _Container(by_selector={"[data-testid='dismiss']": [dismiss]})
    knowledge = _Knowledge(
        PopupFeature(signature="欢迎弹窗", dismiss_selector="[data-testid='dismiss']")
    )
    page = _Page(container)
    guard = PopupGuard(page, knowledge)

    assert guard.dismiss_if_present() is True
    assert dismiss.clicked == 1
    # 页面级 locator 只被用于"找弹窗容器"，关闭选择器只在容器内解析
    assert "[data-testid='dismiss']" not in page.located
    assert container.looked_up == ["[data-testid='dismiss']"]


def test_popup_close_button_prefers_strong_signal():
    """H1：弹窗里同时存在"关闭订单"（弱）与 aria-label=关闭（强）时，点强信号。"""
    weak = _FakeEl(text="关闭订单")
    strong = _FakeEl(attrs={"aria-label": "关闭"})
    container = _Container(candidates=[weak, strong])
    guard = PopupGuard(_Page(container), None)

    assert guard.dismiss_if_present() is True
    assert (weak.clicked, strong.clicked) == (0, 1)


def test_popup_close_button_falls_back_to_weak_signal():
    """H1：没有强信号时仍保留原有能力（弱信号兜底），不给自动化留死角。"""
    weak = _FakeEl(text="关闭订单")
    container = _Container(candidates=[weak])
    guard = PopupGuard(_Page(container), None)

    assert guard.dismiss_if_present() is True
    assert weak.clicked == 1


# --- H2：HealingPage 自建知识库的关闭责任 ---


class _TrackedStore:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def _offline_settings() -> Settings:
    settings = Settings()
    settings.llm.enabled = False
    settings.vision.enabled = False
    settings.embedding.enabled = False
    return settings


def test_healing_page_closes_self_built_knowledge(monkeypatch):
    """H2：未注入时 HealingPage 自建的知识库必须由 close() 释放（此前无人关 → 连接泄漏）。"""
    from selfheal.engine import healing_locator as hl_mod

    store = _TrackedStore()
    monkeypatch.setattr(hl_mod, "build_knowledge_store", lambda settings: store)

    page = hl_mod.HealingPage(object(), _offline_settings())
    assert page._owns_knowledge is True
    page.close()
    assert store.closed == 1


def test_healing_page_leaves_injected_knowledge_alone(monkeypatch):
    """H2：注入的知识库由注入方关闭（所有权边界不变）。"""
    from selfheal.engine import healing_locator as hl_mod

    store = _TrackedStore()
    monkeypatch.setattr(hl_mod, "build_knowledge_store", lambda settings: pytest.fail("不应自建"))

    page = hl_mod.HealingPage(object(), _offline_settings(), knowledge=store)
    assert page._owns_knowledge is False
    page.close()
    assert store.closed == 0


# --- M2：HealingFailedError 兼容 Playwright TimeoutError 语义 ---


def test_healing_failed_error_is_a_playwright_timeout():
    """M2：开启自愈后失败也应能被 `except TimeoutError` 捕获（与关闭自愈时一致）。

    两种写法都要兼容：Playwright 的 TimeoutError（POM 常 catch 的那个）与内置 TimeoutError。
    """
    from playwright.sync_api import TimeoutError as PWTimeoutError

    from selfheal.engine.healing_locator import HealingFailedError

    assert issubclass(HealingFailedError, PWTimeoutError)
    assert issubclass(HealingFailedError, TimeoutError)
    for catch in (PWTimeoutError, TimeoutError):
        with pytest.raises(catch):
            raise HealingFailedError("自愈失败")


def test_default_uncertain_path_raises_timeout_compatible_error():
    """M2：默认配置（use_fallback 且无备用）走到 fail 分支时异常类型仍兼容 TimeoutError。"""
    from selfheal.engine.healing_locator import HealingLocator

    class _Outcome:
        success = False
        new_selector = None
        proposed_selector = None
        root_cause = "not_found"
        confidence = 0.0
        attempt_id = None

    page = type("P", (), {"locator": lambda self, sel, **kw: object()})()
    locator = HealingLocator(object(), page, "#old", Settings().healing, _OrchStub(_Outcome()))
    with pytest.raises(TimeoutError):
        locator._resolve_uncertain(_Outcome())


class _OrchStub:
    """最小编排器替身：run() 恒返回给定 outcome。"""

    def __init__(self, outcome):
        self._outcome = outcome

    def run(self, *args, **kwargs):
        return self._outcome

    def discard_pending(self, attempt_id):
        return None
