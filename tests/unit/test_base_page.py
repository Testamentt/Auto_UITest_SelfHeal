"""单元测试：POM 基类 BasePage（基础框架 B1，不依赖浏览器）。"""

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
    """有 description/fallback → 透传给 HealingPage 增强参数。"""
    page = _FakePage()
    _Demo(page).locator("#a", description="登录按钮", fallback="#b")
    assert page.locator_calls == [("#a", {"description": "登录按钮", "fallback": "#b"})]
