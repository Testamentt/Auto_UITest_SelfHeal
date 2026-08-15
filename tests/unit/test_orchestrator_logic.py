"""单元测试：编排器知识优先与配置默认值（不依赖浏览器）。"""

import logging

import pytest

from selfheal.agent.orchestrator import SelfHealOrchestrator
from selfheal.collect.collector import Scene
from selfheal.config import Settings
from selfheal.knowledge.schema import RepairCase
from selfheal.knowledge.store import KnowledgeStore

pytestmark = pytest.mark.unit


def test_healing_config_defaults():
    cfg = Settings().healing
    assert cfg.enabled is True
    assert cfg.on_uncertain == "use_fallback"
    assert cfg.confidence_threshold == 0.6
    assert cfg.strategy_order[0] == "heuristic"


def test_knowledge_first_hit():
    store = KnowledgeStore()
    store.add_repair(
        RepairCase(
            original_selector="#old",
            new_selector='[data-testid="x"]',
            strategy="heuristic",
            confidence=0.9,
            page_url="u",
        )
    )
    orch = SelfHealOrchestrator(page=None, settings=Settings(), knowledge=store)
    outcome = orch._lookup_knowledge(None, "#old")
    assert outcome is not None
    assert outcome.success
    assert outcome.new_selector == '[data-testid="x"]'
    assert outcome.strategy == "knowledge"


def test_knowledge_first_miss():
    orch = SelfHealOrchestrator(page=None, settings=Settings(), knowledge=KnowledgeStore())
    assert orch._lookup_knowledge(None, "#never-seen") is None


def test_unknown_strategy_logs_warning(caplog):
    """#8：strategy_order 含未知策略名 → 记 warning，不静默跳过也不崩溃。"""
    settings = Settings()
    settings.healing.strategy_order = ["heuristic", "nope"]
    orch = SelfHealOrchestrator(page=None, settings=settings, knowledge=KnowledgeStore())
    scene = Scene(url="x", dom_snapshot="<html><body></body></html>")
    with caplog.at_level(logging.WARNING, logger="selfheal.agent.orchestrator"):
        orch._best_candidate(scene, "#a", None)
    assert any("未知策略名" in r.message for r in caplog.records)


class _TrackedStore(KnowledgeStore):
    """记录是否被 close（验证 owns 标记，审查 M3）。"""

    def __init__(self):
        super().__init__()
        self.closed = False

    def close(self):
        self.closed = True


def test_close_owns_only_self_built_resources():
    """审查 M3：close 只关闭自建资源；注入的 knowledge / llm / vision 由注入方负责。"""
    injected = _TrackedStore()
    orch = SelfHealOrchestrator(page=None, settings=Settings(), knowledge=injected)
    orch.close()
    assert injected.closed is False  # 注入的 → 不被关闭
    orch.close()  # 幂等


def test_close_self_built_knowledge():
    """审查 M3：自建知识库（backend=memory）在 close 时关闭。"""
    from selfheal.knowledge.store import KnowledgeStore as _KS

    settings = Settings()
    settings.knowledge.backend = "memory"
    orch = SelfHealOrchestrator(page=None, settings=settings)
    assert isinstance(orch._knowledge, _KS)
    orch.close()  # 不抛异常即通过（内存后端无 close 属性，_safe_close 跳过）
