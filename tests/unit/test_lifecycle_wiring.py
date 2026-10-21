"""单元测试：L3 / L4 接线的真实链路（M10 收口轮）。

覆盖**接线本身**（能力由队列第 10/13 条在 `collect/collector.py` 实现，行为细节由其
`test_collector.py` 的 L3/L4 用例覆盖；本模块只证明"调用方真的把它接上了"）：

- L4：`ContextAssembler(trace_active=…)` → `SceneCollector(trace_active=…)`；
  `SelfHealOrchestrator(trace_active=…)` 透传；`HealingPage(trace_active=…)` 透传；
  conftest 的 provider 取 CLI 覆盖优先、否则配置。False 时不触碰 tracing（且 provider
  确实被求值，不是"沉默忽略"）；True 时落盘现场 trace 并恢复外层录制。
- L3：`HealingPage.close()` → `SelfHealOrchestrator.close()` → `ContextAssembler.close()`
  → `SceneCollector.detach()`——页面上的 request/response 监听被真正摘除，且幂等。

纯逻辑用例：不依赖浏览器与网络（页面为最小 fake）。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from selfheal.agent import orchestrator as orch_module
from selfheal.agent.context import ContextAssembler
from selfheal.agent.orchestrator import SelfHealOrchestrator
from selfheal.collect.collector import TRACE_RECORDING_KWARGS
from selfheal.config import Settings
from selfheal.knowledge.store import KnowledgeStore
from selfheal.plugin import healing_page as hp_module
from selfheal.plugin.healing_page import HealingPage

pytestmark = pytest.mark.unit


def _settings() -> Settings:
    """离线设置（不建模型客户端、知识库走内存）：单元用例只验接线，不触外部依赖。"""
    settings = Settings()
    settings.llm.enabled = False
    settings.vision.enabled = False
    settings.embedding.enabled = False
    settings.knowledge.backend = "memory"
    return settings


class _Tracing:
    """tracing fake：记录 stop/start；stop 成功时真实落盘（便于断言"现场 trace 已存在"）。"""

    def __init__(self):
        self.calls: list[tuple] = []

    def stop(self, path=None):
        self.calls.append(("stop", path))
        Path(path).write_bytes(b"trace-zip")

    def start(self, **kwargs):
        self.calls.append(("start", kwargs))


class _FakePage:
    """最小页面 fake：网络监听（on / remove_listener）+ tracing + 采集所需读接口。"""

    def __init__(self):
        self.attached: dict[str, object] = {}
        self.handlers: dict[str, object] = {}
        self.removed: list[tuple[str, object]] = []
        self.tracing = _Tracing()

    @property
    def context(self):
        return SimpleNamespace(tracing=self.tracing)

    def on(self, event, handler):
        self.attached[event] = handler
        self.handlers[event] = handler

    def remove_listener(self, event, handler):
        self.removed.append((event, handler))
        self.handlers.pop(event, None)

    @property
    def url(self):
        return "file:///demo"

    def screenshot(self, **kwargs):
        return b""

    def content(self):
        return "<html><body><button id='go'>Go</button></body></html>"

    def evaluate(self, *args, **kwargs):
        raise RuntimeError("无活体页面")  # 元素上下文降级快照/静态提取（不炸采集）


# --- L4：trace 状态 provider 的接线 ---


def test_l4_assembler_skips_tracing_when_provider_false(tmp_path):
    """provider 判定 False → 完全不触碰 tracing、不建 trace_dir，且 provider 确实被调用。"""
    trace_dir = tmp_path / "traces"
    page = _FakePage()
    calls: list[int] = []
    settings = _settings()
    settings.browser.trace_dir = str(trace_dir)
    assembler = ContextAssembler(page, settings, trace_active=lambda: calls.append(1) or False)

    scene = assembler.assemble("#old", "登录按钮").scene

    assert calls == [1], "provider 必须被求值（否则等于沉默忽略注入）"
    assert scene.trace_path is None
    assert page.tracing.calls == [], "判定未录制时不得调用 tracing.stop/start"
    assert not trace_dir.exists(), "判定未录制时不得产生任何副作用（含建目录）"


def test_l4_assembler_saves_inline_trace_when_provider_true(tmp_path):
    """provider 判定 True → 现场 trace 落盘到 trace_dir，并立即恢复外层录制（同 conftest 参数）。"""
    trace_dir = tmp_path / "traces"
    page = _FakePage()
    settings = _settings()
    settings.browser.trace_dir = str(trace_dir)
    assembler = ContextAssembler(page, settings, trace_active=lambda: True)

    scene = assembler.assemble("#old", "登录按钮").scene

    assert scene.trace_path is not None
    assert Path(scene.trace_path).parent == trace_dir
    assert Path(scene.trace_path).exists()
    assert page.tracing.calls[0][0] == "stop"
    assert page.tracing.calls[1] == ("start", TRACE_RECORDING_KWARGS)


def test_l4_orchestrator_forwards_provider_and_closes_assembler(monkeypatch):
    """编排器把 provider 原样转交组装器，且 close() 级联收口组装器（L3 入口）。"""
    captured: dict = {}

    class _RecorderAssembler:
        def __init__(self, page, settings, trace_active=None):
            captured["page"] = page
            captured["trace_active"] = trace_active

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(orch_module, "ContextAssembler", _RecorderAssembler)
    provider = lambda: True  # noqa: E731 - 断言身份透传，用最小 lambda

    orch = SelfHealOrchestrator(
        None, _settings(), knowledge=KnowledgeStore(), trace_active=provider
    )

    assert captured["trace_active"] is provider
    orch.close()
    assert captured["closed"] is True


def test_l4_healing_page_forwards_provider_to_orchestrator(monkeypatch):
    """插件层把 provider 交给编排器（关键字传参），并由 close() 级联释放。"""
    captured: dict = {}

    class _RecorderOrchestrator:
        def __init__(self, page, settings, knowledge, reporter, *, trace_active=None):
            captured["trace_active"] = trace_active

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(hp_module, "SelfHealOrchestrator", _RecorderOrchestrator)
    monkeypatch.setattr(hp_module, "build_knowledge_store", lambda settings: KnowledgeStore())
    provider = lambda: False  # noqa: E731 - 同上

    page = HealingPage(object(), _settings(), trace_active=provider)

    assert captured["trace_active"] is provider
    page.close()
    assert captured["closed"] is True


def test_l4_conftest_provider_prefers_cli_over_settings():
    """conftest provider：CLI --trace-healing/--no-trace-healing 优先，未给则取 settings。"""
    from tests.conftest import _trace_active_provider

    class _Config:
        def __init__(self, value):
            self._value = value

        def getoption(self, name):
            assert name == "trace_healing"
            return self._value

    settings = _settings()
    settings.browser.trace = False
    assert _trace_active_provider(_Config(True), settings)() is True  # CLI 开覆盖配置关
    assert _trace_active_provider(_Config(False), settings)() is False
    settings.browser.trace = True
    assert _trace_active_provider(_Config(None), settings)() is True  # 无 CLI → 取配置


# --- L3：采集器监听的生命周期收口 ---


def test_l3_healing_page_close_detaches_network_listeners():
    """HealingPage.close() 后页面监听已摘除；重复 close 幂等（不重复摘除、不抛错）。"""
    page = _FakePage()
    healing = HealingPage(page, _settings(), knowledge=KnowledgeStore())
    assert set(page.handlers) == {"request", "response"}, "自愈开启时采集器应挂上两类监听"

    healing.close()

    assert page.handlers == {}, "close() 后监听必须已摘除（此前监听仍挂在页面上）"
    assert [event for event, _ in page.removed] == ["request", "response"]
    assert dict(page.removed) == page.attached, "摘除的必须是挂载时同一绑定方法（collector 契约）"

    healing.close()  # 幂等：二次调用不抛错、不重复摘除
    assert len(page.removed) == 2


def test_l3_orchestrator_close_detaches_and_is_idempotent():
    """编排器 close() 直接收口采集器监听（不依赖 HealingPage），且幂等。"""
    page = _FakePage()
    orch = SelfHealOrchestrator(page, _settings(), knowledge=KnowledgeStore())
    assert set(page.handlers) == {"request", "response"}

    orch.close()
    orch.close()  # 幂等

    assert page.handlers == {}
    assert [event for event, _ in page.removed] == ["request", "response"]


def test_l3_close_without_page_is_safe():
    """无 page（纯逻辑环境）时收口不得抛错：assemble/close 全链路 best-effort。"""
    assembler = ContextAssembler(None, _settings())
    assembler.close()
    assembler.close()

    orch = SelfHealOrchestrator(None, _settings(), knowledge=KnowledgeStore())
    orch.close()
