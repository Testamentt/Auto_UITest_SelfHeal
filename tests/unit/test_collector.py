"""单元测试：现场采集器尽力而为（H1）与 T11 现场内联 trace（不依赖浏览器）。"""

from pathlib import Path

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


# --- T11 现场内联 trace（fake tracing，不依赖浏览器） ---


class _SimplePage:
    """带可注入 context 的最小页面 fake（供 T11 用例复用）。"""

    def __init__(self, context=None):
        self._context = context

    @property
    def url(self):
        return "file:///demo"

    @property
    def context(self):
        return self._context

    def screenshot(self, **k):
        return b""

    def content(self):
        return "<html></html>"


class _RecordingTracing:
    """模拟"正在录制"的 tracing：记录 stop/start 调用。"""

    def __init__(self, stop_error: Exception | None = None, start_error: Exception | None = None):
        self.stop_error = stop_error
        self.start_error = start_error
        self.calls: list[tuple] = []

    def stop(self, path=None):
        self.calls.append(("stop", path))
        if self.stop_error is not None:
            raise self.stop_error

    def start(self, **kwargs):
        self.calls.append(("start", kwargs))
        if self.start_error is not None:
            raise self.start_error


def test_inline_trace_absent_without_page():
    """无 page（纯逻辑环境）→ 现场 trace 保持占位 None。"""
    scene = SceneCollector(None).capture()
    assert scene.trace_path is None


def test_inline_trace_none_when_not_recording(tmp_path):
    """未录制（stop 抛 "Must start tracing before stopping"）→ 不擅自启动录制，返回 None。"""
    tracing = _RecordingTracing(stop_error=RuntimeError("Must start tracing before stopping"))
    collector = SceneCollector(
        _SimplePage(type("Ctx", (), {"tracing": tracing})()), trace_dir=str(tmp_path)
    )
    scene = collector.capture()
    assert scene.trace_path is None
    # 只有探测 stop（带路径），未 start（未录制则绝不擅自启动录制）
    assert len(tracing.calls) == 1
    assert tracing.calls[0][0] == "stop" and tracing.calls[0][1] is not None


def test_inline_trace_saved_and_recording_restored(tmp_path):
    """录制中（stop 成功）→ 现场 trace 落盘 trace_dir，并立即恢复录制（同 conftest 参数）。"""
    tracing = _RecordingTracing()
    collector = SceneCollector(
        _SimplePage(type("Ctx", (), {"tracing": tracing})()), trace_dir=str(tmp_path)
    )
    scene = collector.capture()
    assert scene.trace_path is not None
    assert Path(scene.trace_path).parent == tmp_path
    assert Path(scene.trace_path).name.startswith("inline-trace-")
    assert tracing.calls[0][0] == "stop"
    assert tracing.calls[1] == ("start", {"screenshots": True, "snapshots": True})


def test_inline_trace_restore_failure_keeps_path(tmp_path):
    """stop 成功后恢复录制失败 → 现场 trace 已保存，路径仍返回（不丢证据）。"""
    tracing = _RecordingTracing(start_error=RuntimeError("driver restart failed"))
    collector = SceneCollector(
        _SimplePage(type("Ctx", (), {"tracing": tracing})()), trace_dir=str(tmp_path)
    )
    scene = collector.capture()
    assert scene.trace_path is not None  # 现场 trace 有效
    assert Path(scene.trace_path).parent == tmp_path
