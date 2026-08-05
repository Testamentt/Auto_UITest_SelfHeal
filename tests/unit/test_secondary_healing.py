"""单元测试：T4 二次自愈 / 缓存验证（不依赖浏览器）。

覆盖：
- 缓存验证：知识缓存的新选择器若已失效则不用（转策略重修）；仍有效则复用。
- use_knowledge 门控：run(use_knowledge=False) 跳过知识库（二次自愈用）。
- _heal_and_resolve 透传 use_knowledge。
"""

import pytest

from selfheal.agent.orchestrator import HealOutcome, SelfHealOrchestrator
from selfheal.collect.collector import Scene
from selfheal.config import HealingConfig, Settings
from selfheal.engine.healing_locator import HealingLocator
from selfheal.knowledge.schema import RepairCase
from selfheal.knowledge.store import KnowledgeStore

pytestmark = pytest.mark.unit


class _FakeLoc:
    def __init__(self, exists: bool):
        self._exists = exists

    def count(self):
        return 1 if self._exists else 0


class _FakePage:
    """提供 locator/url/screenshot/content，供 orchestrator 采集与缓存验证。"""

    def __init__(self, present=()):
        self._present = set(present)

    def locator(self, sel, **k):
        return _FakeLoc(sel in self._present)

    @property
    def url(self):
        return "file:///fake"

    def screenshot(self, **k):
        return b""

    def content(self):
        return "<html><body></body></html>"


def _orch_with_case(new_selector: str, present=()) -> SelfHealOrchestrator:
    store = KnowledgeStore()
    store.add_repair(RepairCase("#old", new_selector, "heuristic", 0.9, "url"))
    return SelfHealOrchestrator(_FakePage(present), Settings(), knowledge=store)


def test_cache_validation_stale_not_used():
    """缓存的新选择器已不在页面 → 视为失效，不复用。"""
    orch = _orch_with_case("#gone", present=set())  # #gone 不存在
    assert orch._lookup_knowledge(Scene(url="x", dom_snapshot=""), "#old") is None


def test_cache_validation_valid_reused():
    """缓存的新选择器仍在页面 → 复用（strategy=knowledge）。"""
    orch = _orch_with_case("#present", present={"#present"})
    out = orch._lookup_knowledge(Scene(url="x", dom_snapshot=""), "#old")
    assert out is not None
    assert out.new_selector == "#present"
    assert out.strategy == "knowledge"


def test_run_uses_knowledge_by_default():
    orch = _orch_with_case("#present", present={"#present"})
    out = orch.run("#old")
    assert out.success and out.strategy == "knowledge"


def test_run_skips_knowledge_when_disabled():
    """use_knowledge=False（二次自愈）→ 不命中缓存，走策略（空 DOM 无候选 → 失败）。"""
    orch = _orch_with_case("#present", present={"#present"})
    out = orch.run("#old", use_knowledge=False)
    assert out.strategy != "knowledge"


def test_heal_and_resolve_passes_use_knowledge():
    class _RecOrch:
        def __init__(self):
            self.use_knowledge_calls = []

        def run(self, sel, desc, failure=None, use_knowledge=True):
            self.use_knowledge_calls.append(use_knowledge)
            return HealOutcome(success=True, new_selector="#new", confidence=0.9)

    class _Page:
        def locator(self, sel, **k):
            return ("LOC", sel)

    orch = _RecOrch()
    hl = HealingLocator(object(), _Page(), "#old", HealingConfig(), orch)
    result = hl._heal_and_resolve(use_knowledge=False)
    assert orch.use_knowledge_calls == [False]
    assert result == ("LOC", "#new")
