"""端到端测试：T11 现场内联 trace（需浏览器内核，-m e2e）。

验证 SceneCollector.capture() 对 `Scene.trace_path` 的真实落盘语义：
1. context.tracing 录制中（模拟 --trace-healing / browser.trace）→ capture 导出现场
   trace（reports/traces/inline-trace-*.zip，可 `playwright show-trace` 回放），
   并恢复录制（后续 capture 再次产出新现场文件）；
2. 未录制 → 不擅自启动录制，trace_path 保持占位 None（零副作用）。
"""

from pathlib import Path

import pytest

from selfheal.collect.collector import SceneCollector
from tests.e2e.pages.demo_page import DemoPage

pytestmark = pytest.mark.e2e


def test_inline_trace_saved_when_recording(page, context):
    """录制中：capture() 产出真实落盘的现场 trace 文件。"""
    context.tracing.start(screenshots=True, snapshots=True)  # 模拟外层 --trace-healing
    demo = DemoPage(page)
    demo.open()
    scene = SceneCollector(page).capture()
    assert scene.trace_path is not None
    path = Path(scene.trace_path)
    assert path.exists()  # stop 同步完成，zip 已落盘
    assert path.name.startswith("inline-trace-")
    assert path.name.endswith(".zip")


def test_inline_trace_repeats_after_restore(page, context):
    """恢复录制后再次采集 → 产出新的现场 trace 文件（两段各自独立、互不覆盖）。"""
    context.tracing.start(screenshots=True, snapshots=True)
    demo = DemoPage(page)
    demo.open()
    first = SceneCollector(page).capture().trace_path
    second = SceneCollector(page).capture().trace_path  # stop→start 后再次导出
    assert first and second
    assert Path(first).exists() and Path(second).exists()
    assert first != second  # uuid 命名：不覆盖前一段


def test_inline_trace_absent_when_not_recording(page):
    """未录制：capture() 不擅自启动录制，trace_path 保持占位 None。"""
    demo = DemoPage(page)
    demo.open()
    scene = SceneCollector(page).capture()
    assert scene.trace_path is None
