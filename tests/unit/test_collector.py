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


def test_capture_network_logs_from_listeners():
    """C5：构造时挂载 on_request/on_response，capture() 带出近期网络日志。"""

    class _Req:
        url = "https://x/api"
        method = "GET"

    class _Resp:
        url = "https://x/api"
        status = 200

    class _Page:
        def __init__(self):
            self.handlers = {}

        def on(self, event, handler):
            self.handlers[event] = handler

        def emit(self, event, obj):
            self.handlers[event](obj)

        @property
        def url(self):
            return "file:///demo"

        def screenshot(self, **k):
            return b""

        def content(self):
            return "<html></html>"

    page = _Page()
    collector = SceneCollector(page)
    page.emit("request", _Req())
    page.emit("response", _Resp())
    scene = collector.capture()
    assert scene.network_logs == [
        {"type": "request", "url": "https://x/api", "method": "GET"},
        {"type": "response", "url": "https://x/api", "status": 200},
    ]


def test_network_listeners_absent_skipped():
    """页面无 on 方法 → 跳过监听，capture 仍正常（network_logs 空）。"""

    class _NoOnPage:
        @property
        def url(self):
            return "file:///demo"

        def screenshot(self, **k):
            return b""

        def content(self):
            return "<html></html>"

    scene = SceneCollector(_NoOnPage()).capture()
    assert scene.network_logs == []
