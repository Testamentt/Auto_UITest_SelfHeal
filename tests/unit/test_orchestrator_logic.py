"""单元测试：编排器知识优先与配置默认值（不依赖浏览器）。"""

import pytest

from selfheal.agent.orchestrator import SelfHealOrchestrator
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
