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


# --- L3：网络监听摘除（detach/close 幂等，摘除后不再计数） ---


class _ListenerPage:
    """可挂/可摘的页面 fake：on/remove_listener/emit 齐全，模拟 Playwright 的监听语义。

    remove_listener 用 `list.remove(handler)`（按 `==` 比较绑定方法）→ 与 Playwright
    （pyee + wrap_handler 实例级缓存）的"同一绑定方法可取回"语义一致。
    """

    def __init__(self, with_remove: bool = True):
        self.listeners: dict[str, list] = {"request": [], "response": []}
        self.removed: list[tuple] = []
        self._with_remove = with_remove

    def on(self, event, handler):
        self.listeners[event].append(handler)

    def remove_listener(self, event, handler):
        self.removed.append((event, handler))
        self.listeners[event].remove(handler)

    def emit(self, event, obj):
        for handler in list(self.listeners[event]):
            handler(obj)

    @property
    def url(self):
        return "file:///demo"

    def screenshot(self, **k):
        return b""

    def content(self):
        return "<html></html>"


class _Req:
    url = "https://x/api"
    method = "GET"


def test_l3_detach_removes_listeners_and_stops_counting():
    """L3：detach() 用 remove_listener 摘除两类监听，摘除后事件不再计入（已采集的保留）。"""
    page = _ListenerPage()
    collector = SceneCollector(page)
    page.emit("request", _Req())
    page.emit("response", _Req())
    assert len(collector.capture().network_logs) == 2

    collector.detach()
    assert [event for event, _ in page.removed] == ["request", "response"]  # 两类都摘掉
    assert page.listeners == {"request": [], "response": []}  # 页面上已无残留监听

    page.emit("request", _Req())
    page.emit("response", _Req())
    assert len(collector.capture().network_logs) == 2  # 摘除后不再新增，历史日志仍带出


def test_l3_detach_is_idempotent_and_close_aliases_it():
    """L3：detach() 幂等（重复调用不重复摘除、不抛异常）；close() 等价于 detach()。"""
    page = _ListenerPage()
    collector = SceneCollector(page)
    collector.detach()
    collector.detach()
    collector.close()
    assert len(page.removed) == 2  # 只有首次 detach 真正摘除两个事件

    # 无监听可摘时 detach/close 仍安全（含无 page 的纯逻辑实例）
    SceneCollector(None).close()


def test_l3_reattach_after_detach_resumes_counting():
    """L3：detach 后可重新挂载（同实例复用）→ 恢复计数，且不会重复挂同一事件。"""
    page = _ListenerPage()
    collector = SceneCollector(page)
    collector.detach()
    collector._attach_network_listeners(page)
    page.emit("request", _Req())
    assert len(collector.capture().network_logs) == 1
    assert len(page.listeners["request"]) == 1  # 去重：未重复挂载


def test_l3_repeated_construction_after_detach_no_double_count():
    """L3：同页"先 detach 旧实例、再构造新实例"→ 事件只被新实例计一次（不重复计数）。"""
    page = _ListenerPage()
    old = SceneCollector(page)
    old.detach()
    fresh = SceneCollector(page)
    page.emit("request", _Req())

    assert len(fresh.capture().network_logs) == 1  # 新实例计一次
    assert old.capture().network_logs == []  # 旧实例已摘除，不再计数
    assert len(page.listeners["request"]) == 1  # 页面上只有一份监听


def test_l3_detach_without_remove_listener_still_stops_counting(caplog):
    """L3：页面无 remove_listener（摘不掉）→ 记 warning 且守卫确保不再计数（R4 不静默）。"""

    class _NoRemovePage(_ListenerPage):
        remove_listener = None  # 显式无摘除手段

    page = _NoRemovePage()
    collector = SceneCollector(page)
    page.emit("request", _Req())
    assert len(collector.capture().network_logs) == 1

    with caplog.at_level("WARNING"):
        collector.detach()
    assert "无法真正摘除" in caplog.text
    page.emit("request", _Req())  # 监听仍挂在页面上，但守卫必须拦住
    assert len(collector.capture().network_logs) == 1


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


# --- L4：内联 trace 的录制状态由注入的 trace_active 判定（不再只靠异常探测） ---


def _raise_provider() -> bool:
    """故障的 trace 状态提供者（模拟 provider 内部异常）。"""
    raise RuntimeError("trace 状态不可用")


def test_l4_trace_provider_false_skips_stop(tmp_path):
    """L4：注入 trace_active=False → 完全不触碰 tracing（不调 stop、不建文件）。"""
    tracing = _RecordingTracing()
    asked: list[bool] = []

    def _provider() -> bool:
        asked.append(True)
        return False

    trace_dir = tmp_path / "traces"  # 用不存在的子目录，验证"零副作用"
    collector = SceneCollector(
        _SimplePage(type("Ctx", (), {"tracing": tracing})()),
        trace_dir=str(trace_dir),
        trace_active=_provider,
    )
    scene = collector.capture()
    assert scene.trace_path is None
    assert tracing.calls == []  # 显式 False → 不调 stop（旧实现必调一次以探测）
    assert asked == [True]  # provider 恰好被问一次
    assert not trace_dir.exists()  # 连 trace_dir 都不创建：判定为未录制时零副作用


def test_l4_trace_provider_true_saves_and_restores(tmp_path):
    """L4：注入 trace_active=True → 正常落盘现场 trace 并立即恢复外层录制。"""
    tracing = _RecordingTracing()
    collector = SceneCollector(
        _SimplePage(type("Ctx", (), {"tracing": tracing})()),
        trace_dir=str(tmp_path),
        trace_active=lambda: True,
    )
    scene = collector.capture()
    assert scene.trace_path is not None
    assert Path(scene.trace_path).parent == tmp_path
    assert Path(scene.trace_path).name.startswith("inline-trace-")
    assert tracing.calls[0][0] == "stop"
    assert tracing.calls[1] == ("start", {"screenshots": True, "snapshots": True})


def test_l4_trace_provider_error_treated_as_not_recording(tmp_path, caplog):
    """L4：provider 自身抛错 → 按未录制处理（不动 tracing）并记 warning（R4 不静默）。"""
    tracing = _RecordingTracing()
    collector = SceneCollector(
        _SimplePage(type("Ctx", (), {"tracing": tracing})()),
        trace_dir=str(tmp_path),
        trace_active=_raise_provider,
    )
    with caplog.at_level("WARNING"):
        scene = collector.capture()
    assert scene.trace_path is None
    assert tracing.calls == []  # 权威判定故障时不猜、不动 tracing
    assert "trace_active" in caplog.text


def test_l4_without_provider_keeps_exception_probe(tmp_path):
    """L4：未注入 provider（默认 None）→ 保留异常探测兜底路径（兼容旧行为）。"""
    tracing = _RecordingTracing(stop_error=RuntimeError("Must start tracing before stopping"))
    collector = SceneCollector(
        _SimplePage(type("Ctx", (), {"tracing": tracing})()), trace_dir=str(tmp_path)
    )
    assert collector._trace_active is None
    scene = collector.capture()
    assert scene.trace_path is None  # 探测到未录制
    assert len(tracing.calls) == 1 and tracing.calls[0][0] == "stop"
