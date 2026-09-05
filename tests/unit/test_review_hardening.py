"""单元测试：2026-09-03 子代理复核（V4/V5）修复点回归覆盖。

覆盖：orchestrator 构造失败清理 / 豁免记录 verified=False / _safe_close 日志、
VLM close() 生命周期对齐、semantic._bump 非静默吞错。
"""

import logging
from types import SimpleNamespace

import pytest

from selfheal.agent import orchestrator as orch_module
from selfheal.agent.orchestrator import SelfHealOrchestrator, _safe_close
from selfheal.agent.strategies.semantic import SemanticStrategy
from selfheal.config import Settings
from selfheal.knowledge.store import KnowledgeStore
from selfheal.llm.openai_vision import OpenAICompatibleVLM

pytestmark = pytest.mark.unit


class _FakeStore:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _settings() -> Settings:
    settings = Settings()
    settings.knowledge.backend = "memory"
    return settings


def _boom(*args, **kwargs):
    raise RuntimeError("collaborator init failed")


# --- V4：orchestrator 构造失败清理 ---


def test_init_failure_closes_owned_resources(monkeypatch):
    """构造中途失败（协作者初始化抛错）必须清理已建成的自有资源（防泄漏）。"""
    store = _FakeStore()
    monkeypatch.setattr(orch_module, "build_knowledge_store", lambda settings: store)
    monkeypatch.setattr(orch_module, "ContextAssembler", _boom)
    with pytest.raises(RuntimeError):
        SelfHealOrchestrator(None, _settings())
    assert store.closed is True


def test_init_failure_keeps_injected_resources(monkeypatch):
    """注入的资源（owns=False）归注入方关闭，构造失败也不代关。"""
    store = _FakeStore()
    monkeypatch.setattr(orch_module, "ContextAssembler", _boom)
    with pytest.raises(RuntimeError):
        SelfHealOrchestrator(None, _settings(), knowledge=store)
    assert store.closed is False


# --- V4：豁免记录 verified 语义 ---


def test_exemption_record_not_counted_as_verified():
    """高风险页豁免不是修复——审计记录 verified=False，不计入"真自愈"指标。"""

    class _ExcludedContext:
        excluded = True

    class _StubAssembler:
        def assemble(self, original_selector, description=None):
            return _ExcludedContext()

    orch = SelfHealOrchestrator(None, _settings(), knowledge=KnowledgeStore())
    orch._assembler = _StubAssembler()
    outcome = orch.run("#old")
    assert outcome.success is False
    assert outcome.root_cause == "high_risk_page_excluded"
    rec = orch._reporter.records[-1]
    assert rec.verified is False


# --- V4：_safe_close 可观测性 ---


def test_safe_close_logs_warning(caplog):
    """_safe_close 关闭失败记 warning（不再静默），且不上抛阻断业务收口。"""

    class _Bad:
        def close(self):
            raise RuntimeError("close failed")

    with caplog.at_level(logging.WARNING, logger="selfheal.agent.orchestrator"):
        _safe_close(_Bad())
    assert any("best-effort 关闭" in rec.message for rec in caplog.records)


def test_safe_close_none_and_plain_objects():
    _safe_close(None)  # 无 close → no-op
    _safe_close(object())


# --- V5：VLM close() 生命周期对齐 ---


def test_vlm_close_releases_underlying_client():
    """VLM 补 close() 后，orchestrator.close() 的 _safe_close 不再是静默 no-op。"""
    vlm = OpenAICompatibleVLM("key", "model")
    closed = []

    class _FakeClient:
        def close(self):
            closed.append(1)

    vlm._client = _FakeClient()
    vlm.close()
    vlm.close()  # 幂等
    assert closed == [1]
    assert vlm._client is None


# --- V5：semantic._bump 非静默吞错 ---


def test_semantic_bump_logs_instead_of_silently_swallowing(caplog):
    """知识热度更新失败记 warning（R4 不静默吞错），且不阻塞采纳路径。"""

    class _RaisingKB:
        def bump_hit(self, repair_key):
            raise RuntimeError("database is locked")

    strategy = SemanticStrategy(knowledge=_RaisingKB())
    case = SimpleNamespace(repair_key="rk-1")
    with caplog.at_level(logging.WARNING, logger="selfheal.agent.strategies.semantic"):
        strategy._bump(case)  # 不上抛
    assert any("知识热度更新失败" in rec.message for rec in caplog.records)
