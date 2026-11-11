"""两条审计缺陷的最小回归用例（不触网 / 无浏览器）。

对应审计报告复现的两个缺陷，按「一个缺陷一条用例」固定下来，防止回归：

1. **阈值遮蔽**：`best_candidate` 曾按原始置信度取最大，再用赢家自己的阈值裁决 →
   heuristic 0.75（自身阈值 0.8）会遮蔽 visual 0.62（自身阈值 0.6），本可修复却整体判失败。
2. **候选非唯一**：`selector_exists` 曾只判 `count() > 0` → 两个同名按钮时 `text="提交"`
   被当成可用修复采纳，重试 `click()` 抛 Playwright strict-mode violation（非 TimeoutError，
   不触发自愈），脏案例还会写进知识库。

更完整的边界覆盖见 `test_threshold_arbitration.py` 与 `test_selector_usability.py`。
"""

from __future__ import annotations

import pytest

import selfheal.agent.orchestrator as orch_mod
from selfheal.agent.context import HealingContext, selector_exists
from selfheal.agent.diagnose import Diagnoser
from selfheal.agent.fix_generator import FixGenerator
from selfheal.agent.persistence import PersistenceHandler
from selfheal.agent.strategies.base import RepairCandidate
from selfheal.collect.collector import Scene
from selfheal.config import Settings
from selfheal.knowledge.store import KnowledgeStore
from selfheal.reporting.hooks import HealingReporter

pytestmark = pytest.mark.unit

_AMBIGUOUS_SELECTOR = 'text="提交"'
_DUPLICATE_BUTTONS_DOM = "<html><body><button>提交</button><button>提交</button></body></html>"


def _ctx(dom: str) -> HealingContext:
    return HealingContext(
        scene=Scene(url="demo://page", dom_snapshot=dom),
        original_selector="#submit-old",
        description="提交",
        dom_fingerprint=None,
        page_fingerprint="",
        element_context=None,
    )


def _fix_gen(settings: Settings, strategies: dict, *, page=None) -> FixGenerator:
    return FixGenerator(
        settings=settings,
        knowledge=KnowledgeStore(),
        reporter=HealingReporter(),
        rule_diagnoser=Diagnoser(),
        llm_diagnoser=None,
        llm_client=None,
        vision_client=None,
        embedding=None,
        page=page,
        strategy_registry=strategies,
    )


def _strategy_type(name: str, selector: str, confidence: float):
    """固定产出候选的假策略（`_build_strategy` 对未知策略类无参实例化）。"""

    class _FixedStrategy:
        def repair(self, scene, original_selector, description=None):
            return RepairCandidate(selector=selector, confidence=confidence, strategy=name)

    _FixedStrategy.name = name  # 策略名以调度键为准（_select_candidates 会用它查阈值）
    return _FixedStrategy


def test_threshold_masking_reported_case():
    """回归①：heuristic 0.75（阈值 0.8）不得遮蔽 visual 0.62（阈值 0.6）。"""
    settings = Settings()
    settings.healing.strategy_order = ["heuristic", "visual"]
    settings.healing.strategy_thresholds = {"heuristic": 0.8, "visual": 0.6}
    gen = _fix_gen(
        settings,
        {
            "heuristic": _strategy_type("heuristic", "#heuristic", 0.75),
            "visual": _strategy_type("visual", "#visual", 0.62),
        },
    )
    context = _ctx("<html><body><button id='go'>Go</button></body></html>")

    best = gen.best_candidate(context)
    outcome = PersistenceHandler(
        settings, KnowledgeStore(), HealingReporter(), embedding=None, page=None
    ).resolve(context, gen.generate(context))

    # 0.75 未达自身阈值 0.8 → 不参与择优；0.62 已达自身阈值 0.6 → 被采纳
    assert best is not None
    assert (best.selector, best.strategy, best.confidence) == (
        "#visual",
        "visual",
        pytest.approx(0.62),
    )
    assert outcome.success is True
    assert (outcome.new_selector, outcome.strategy) == ("#visual", "visual")


class _Loc:
    def __init__(self, count: int):
        self._count = count

    def count(self) -> int:
        return self._count


class _Page:
    def __init__(self, counts: dict[str, int]):
        self._counts = counts

    def locator(self, selector: str, **_kwargs):
        return _Loc(self._counts.get(selector, 0))


def test_non_unique_candidate_reported_case(monkeypatch):
    """回归②：`text="提交"` 命中两个按钮（count()!=1）时必须被拒，不得交给引擎重试。"""
    monkeypatch.setattr("selfheal.reporting.fix_proposals.write_fix_proposal", lambda **_: None)
    page = _Page({_AMBIGUOUS_SELECTOR: 2})  # 两个同名按钮 → 非唯一
    settings = Settings()
    gen = _fix_gen(settings, dict(orch_mod._STRATEGY_REGISTRY), page=page)
    context = _ctx(_DUPLICATE_BUTTONS_DOM)

    # 唯一性硬校验：存在（count>0）但非唯一 → 不可用
    assert selector_exists(page, _AMBIGUOUS_SELECTOR) is False

    proposal = gen.generate(context)
    outcome = PersistenceHandler(
        settings, KnowledgeStore(), HealingReporter(), embedding=None, page=page
    ).resolve(context, proposal)

    # 启发式确实产出了 text="提交"（0.95），但被唯一性校验拦下：不采纳、不生成新定位器、不落库
    assert proposal.best is None
    assert proposal.rejected is not None and proposal.rejected.selector == _AMBIGUOUS_SELECTOR
    assert outcome.success is False
    assert outcome.new_selector is None
