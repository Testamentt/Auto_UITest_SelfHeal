"""单元测试：现场采集器尽力而为（H1，不依赖浏览器）。"""

import pytest

from selfheal.collect.collector import SceneCollector

pytestmark = pytest.mark.unit


def test_capture_full():
    class _Page:
        @property
        def url(self):
            return "file:///demo"

        def screenshot(self, **k):
            return b"shot"

        def content(self):
            return "<html></html>"

    scene = SceneCollector(_Page()).capture()
    assert scene.url == "file:///demo"
    assert scene.screenshot == b"shot"
    assert scene.dom_snapshot == "<html></html>"


def test_capture_no_page():
    scene = SceneCollector(None).capture()
    assert scene.url == ""
    assert scene.screenshot is None
    assert scene.dom_snapshot is None


def test_capture_screenshot_fails_still_gets_dom():
    class _Page:
        @property
        def url(self):
            return "file:///demo"

        def screenshot(self, **k):
            raise RuntimeError("screenshot failed")

        def content(self):
            return "<html></html>"

    scene = SceneCollector(_Page()).capture()
    assert scene.url == "file:///demo"
    assert scene.screenshot is None  # 截图失败置 None，不炸闭环
    assert scene.dom_snapshot == "<html></html>"


def test_capture_all_fail():
    class _BadPage:
        @property
        def url(self):
            raise RuntimeError("page closed")

        def screenshot(self, **k):
            raise RuntimeError("closed")

        def content(self):
            raise RuntimeError("closed")

    scene = SceneCollector(_BadPage()).capture()
    assert scene.url == ""
    assert scene.screenshot is None
    assert scene.dom_snapshot is None  # 全部失败也不抛异常
