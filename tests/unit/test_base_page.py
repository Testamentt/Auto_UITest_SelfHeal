# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""单元测试：POM 基类 BasePage（不依赖浏览器）。"""

import logging

import pytest

from selfheal.engine.base_page import BasePage

pytestmark = pytest.mark.unit


class _FakePage:
    def __init__(self):
        self.goto_calls = []
        self.locator_calls = []

    def goto(self, url):
        self.goto_calls.append(url)

    def locator(self, selector, **kwargs):
        self.locator_calls.append((selector, kwargs))
        return ("LOC", selector)


class _HealingFakePage(_FakePage):
    """模拟 HealingPage：声明能力标记，接受 description/fallback。"""

    supports_healing_kwargs = True


class _StrictFakePage(_FakePage):
    """模拟原生 Page 的真实签名：未知关键字参数直接 TypeError（防"吞 kwargs"掩盖缺陷）。"""

    def locator(self, selector, **kwargs):
        for key in ("description", "fallback"):
            if key in kwargs:
                raise TypeError(f"Locator.locator() got an unexpected keyword argument {key!r}")
        return super().locator(selector, **kwargs)


class _NativeLike:
    """断言用原生 Locator 替身：没有 native 属性。"""

    def __init__(self, text):
        self.text = text


class _ProxyLike:
    """断言用 HealingLocator 替身：有 native 属性返回原生对象。"""

    def __init__(self, native):
        self.native = native


class _Demo(BasePage):
    url = "file:///demo"


class _NoUrl(BasePage):
    pass


def test_open_uses_declared_url():
    page = _FakePage()
    _Demo(page).open()
    assert page.goto_calls == ["file:///demo"]


def test_open_with_explicit_url():
    page = _FakePage()
    _Demo(page).open("https://example.com")
    assert page.goto_calls == ["https://example.com"]


def test_open_without_url_raises():
    page = _FakePage()
    with pytest.raises(NotImplementedError):
        _NoUrl(page).open()


def test_locator_passthrough_native():
    """无 description/fallback → 等价原生调用（基座兼容原生 Page）。"""
    page = _FakePage()
    _Demo(page).locator("#a")
    assert page.locator_calls == [("#a", {})]


def test_locator_passes_healing_kwargs():
    """page 声明 supports_healing_kwargs → 增强参数透传（HealingPage 路径）。"""
    page = _HealingFakePage()
    _Demo(page).locator("#a", description="登录按钮", fallback="#b")
    assert page.locator_calls == [("#a", {"description": "登录按钮", "fallback": "#b"})]


def test_locator_drops_healing_kwargs_on_native_page():
    """原生 Page 不接受 description/fallback：丢弃而非透传（否则 TypeError）。"""
    page = _StrictFakePage()
    _Demo(page).locator("#a", description="登录按钮", fallback="#b")
    assert page.locator_calls == [("#a", {})]


def test_locator_warns_once_when_kwargs_ignored(caplog):
    """关闭自愈时增强参数被忽略：只告警一次，避免每个定位刷屏。"""
    page = _StrictFakePage()
    demo = _Demo(page)
    with caplog.at_level(logging.WARNING, logger="selfheal.engine.base_page"):
        demo.locator("#a", description="登录按钮")
        demo.locator("#b", description="密码框")
    warnings = [r for r in caplog.records if "不支持自愈增强参数" in r.getMessage()]
    assert len(warnings) == 1
    assert "#a" in warnings[0].getMessage()


def test_native_unwraps_proxy_and_passes_raw():
    """_native：HealingLocator 替身取 native，原生对象原样返回。"""
    raw = _NativeLike("x")
    assert BasePage._native(raw) is raw
    assert BasePage._native(_ProxyLike(raw)) is raw


def test_expect_text_unwraps_healing_locator():
    """expect_text 必须把原生 Locator 交给 expect()（代理对象会被 expect 拒绝）。"""
    received = []

    class _FakeExpect:
        def __init__(self, actual):
            received.append(actual)

        def to_contain_text(self, text, timeout=None):
            received.append((text, timeout))

    raw = _NativeLike("body")
    page = _FakePage()
    page.locator = lambda selector, **kw: _ProxyLike(raw)  # HealingLocator 替身
    demo = _Demo(page)

    # 惰性导入发生在方法内，patch playwright.sync_api.expect 即可
    import playwright.sync_api as sync_api

    original = sync_api.expect
    sync_api.expect = _FakeExpect
    try:
        demo.expect_text("登录成功", timeout_ms=3000)
    finally:
        sync_api.expect = original
    assert received[0] is raw, "expect() 收到的必须是原生 Locator，不能是代理对象"
    assert received[1] == ("登录成功", 3000)
