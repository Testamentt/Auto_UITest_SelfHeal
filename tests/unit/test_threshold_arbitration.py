"""单元测试：按策略独立阈值的**逐候选过滤**裁决（不触网 / 无浏览器）。

回归的缺陷（审计复现）：`best_candidate` 先按**原始置信度**取最大，随后 `generate`/`resolve`
再用**赢家自己的**阈值裁决，且不回退次优候选——heuristic 0.75（自身阈值 0.8）会遮蔽
visual 0.62（自身阈值 0.6），明明有可采纳候选却整体判失败。

修复口径：每个候选先按**自己策略的阈值**过滤，合格者才参与"取最优"；被拒候选不遮蔽后续候选，
但仍经 `FixProposal.rejected` 保留给失败路径的人审建议出口（过滤不能顺手丢掉审计信息）。
"""

from __future__ import annotations

import pytest

import selfheal.agent.orchestrator as orch_mod
from selfheal.agent.context import HealingContext
from selfheal.agent.diagnose import Diagnoser
from selfheal.agent.fix_generator import FixGenerator
from selfheal.agent.persistence import PersistenceHandler
from selfheal.agent.strategies.base import RepairCandidate
from selfheal.collect.collector import Scene
from selfheal.config import Settings
from selfheal.knowledge.store import KnowledgeStore
from selfheal.reporting.hooks import HealingReporter

pytestmark = pytest.mark.unit

_DOM = '<html><body><button id="go">Go</button></body></html>'


def _ctx() -> HealingContext:
    return HealingContext(
        scene=Scene(url="demo://page", dom_snapshot=_DOM),
        original_selector="#go-old",
        description="go 按钮",
        dom_fingerprint=None,
        page_fingerprint="",
        element_context=None,
    )


def _settings(
    order: tuple[str, ...],
    thresholds: dict[str, float],
    early: dict[str, float] | None = None,
) -> Settings:
    """按策略阈值配置（阈值只在裁决点生效——本文件不关心策略内部打分）。"""
    settings = Settings()
    settings.healing.strategy_order = list(order)
    settings.healing.strategy_thresholds = dict(thresholds)
    if early is not None:
        settings.healing.strategy_early_accept = dict(early)
    return settings


def _strategy_type(strategy_name: str, selector: str, confidence: float, calls: list | None = None):
    """造一个固定产出候选的策略类（`_build_strategy` 对未知策略类无参实例化）。"""

    class _FixedStrategy:
        name = strategy_name

        def repair(self, scene, original_selector, description=None):
            if calls is not None:
                calls.append(strategy_name)
            return RepairCandidate(selector=selector, confidence=confidence, strategy=strategy_name)

    return _FixedStrategy


def _registry(*strategy_types) -> dict:
    """只注册本用例用到的策略（隔离真实策略，保证裁决路径可控）。"""
    return {cls.name: cls for cls in strategy_types}


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


def _persister(settings: Settings, *, page=None) -> PersistenceHandler:
    return PersistenceHandler(
        settings, KnowledgeStore(), HealingReporter(), embedding=None, page=page
    )


# --- 核心回归：高置信但未达自身阈值的候选不得遮蔽达标候选 ---


def test_high_confidence_below_own_threshold_does_not_mask_acceptable_candidate():
    """heuristic 0.75（阈值 0.8）不得遮蔽 visual 0.62（阈值 0.6）。"""
    settings = _settings(("heuristic", "visual"), {"heuristic": 0.8, "visual": 0.6})
    gen = _fix_gen(
        settings,
        _registry(
            _strategy_type("heuristic", "#heuristic", 0.75),
            _strategy_type("visual", "#visual", 0.62),
        ),
    )

    best = gen.best_candidate(_ctx())

    assert best is not None, "0.62 已达 visual 自身阈值 0.6，必须被采纳"
    assert (best.selector, best.strategy) == ("#visual", "visual")
    assert best.confidence == pytest.approx(0.62)


def test_resolve_accepts_the_per_candidate_qualified_candidate():
    """裁决链路整体可用：generate → resolve 采纳的是达标候选（而非被遮蔽后的失败）。"""
    settings = _settings(("heuristic", "visual"), {"heuristic": 0.8, "visual": 0.6})
    gen = _fix_gen(
        settings,
        _registry(
            _strategy_type("heuristic", "#heuristic", 0.75),
            _strategy_type("visual", "#visual", 0.62),
        ),
    )
    context = _ctx()

    outcome = _persister(settings).resolve(context, gen.generate(context))

    assert outcome.success is True
    assert (outcome.new_selector, outcome.strategy) == ("#visual", "visual")
    assert outcome.attempt_id == "heal-1"  # 已暂存，等引擎重试验证


def test_picks_highest_confidence_among_qualified_candidates():
    """多个达标候选仍取最优；不达标的高置信候选不参与比较。"""
    settings = _settings(("a", "b", "c"), {"a": 0.9, "b": 0.6, "c": 0.6})
    gen = _fix_gen(
        settings,
        _registry(
            # 0.88 < 自身阈值 0.9（但高于全局 early 0.85：过滤必须先于早接受）
            _strategy_type("a", "#a", 0.88),
            _strategy_type("b", "#b", 0.80),
            _strategy_type("c", "#c", 0.75),
        ),
    )

    best = gen.best_candidate(_ctx())

    assert best is not None and (best.selector, best.confidence) == ("#b", pytest.approx(0.80))


# --- 早接受（省钱短路）语义保持不变 ---


def test_early_accept_still_returns_immediately_and_skips_later_strategies():
    """达自身阈值 + 达早接受阈值 → 立即返回，不再执行后续（更贵的）策略。"""
    calls: list[str] = []
    settings = _settings(("heuristic", "visual"), {"heuristic": 0.8, "visual": 0.6})
    gen = _fix_gen(
        settings,
        _registry(
            _strategy_type("heuristic", "#heuristic", 0.95, calls),
            _strategy_type("visual", "#visual", 0.90, calls),
        ),
    )

    best = gen.best_candidate(_ctx())

    assert best is not None and best.strategy == "heuristic"
    assert calls == ["heuristic"], "早接受后不应继续调用 visual"


def test_below_own_threshold_is_not_early_accepted():
    """高于全局 early(0.85) 但未达自身阈值(0.9) 的候选不得借早接受绕过阈值过滤。"""
    settings = _settings(("heuristic", "visual"), {"heuristic": 0.9, "visual": 0.6})
    gen = _fix_gen(
        settings,
        _registry(
            _strategy_type(
                "heuristic", "#heuristic", 0.87
            ),  # 0.87 < 自身 0.9，但 >= 全局 early 0.85
            _strategy_type("visual", "#visual", 0.65),
        ),
    )

    best = gen.best_candidate(_ctx())

    assert best is not None and best.strategy == "visual"


# --- 过滤不丢审计信息：被拒候选仍进入人审建议出口 ---


def test_rejected_candidate_is_kept_for_review_suggestion(monkeypatch):
    """全被拒时 best=None，但 rejected 保留最优被拒候选，失败仍写人审建议。"""
    written: list[dict] = []
    monkeypatch.setattr(
        "selfheal.reporting.fix_proposals.write_fix_proposal",
        lambda **kwargs: written.append(kwargs),
    )
    settings = _settings(("heuristic",), {"heuristic": 0.8})
    gen = _fix_gen(settings, _registry(_strategy_type("heuristic", "#heuristic", 0.75)))
    context = _ctx()

    proposal = gen.generate(context)

    assert proposal.best is None
    assert proposal.rejected is not None
    assert proposal.rejected.selector == "#heuristic"

    outcome = _persister(settings).resolve(context, proposal)
    assert outcome.success is False
    assert len(written) == 1 and written[0]["new_selector"] == "#heuristic"


def test_all_candidates_rejected_keeps_highest_confidence_one():
    """全部不达标：rejected 取置信度最高者（仅供人审，不参与采纳）。"""
    settings = _settings(("heuristic", "visual"), {"heuristic": 0.9, "visual": 0.9})
    gen = _fix_gen(
        settings,
        _registry(
            _strategy_type("heuristic", "#heuristic", 0.80),
            _strategy_type("visual", "#visual", 0.70),
        ),
    )

    proposal = gen.generate(_ctx())

    assert proposal.best is None
    assert proposal.rejected is not None and proposal.rejected.selector == "#heuristic"


# --- 默认配置零回归 ---


def test_default_config_still_accepts_heuristic_candidate():
    """默认阈值（0.6 / 早接受 0.85）下行为不变：heuristic 强信号候选照常被采纳并短路。"""
    settings = Settings()
    gen = FixGenerator(
        settings=settings,
        knowledge=KnowledgeStore(),
        reporter=HealingReporter(),
        rule_diagnoser=Diagnoser(),
        llm_diagnoser=None,
        llm_client=None,
        vision_client=None,
        embedding=None,
        page=None,
        strategy_registry=dict(orch_mod._STRATEGY_REGISTRY),
    )
    context = HealingContext(
        scene=Scene(
            url="demo://page",
            dom_snapshot='<html><body><button aria-label="登录按钮">登录</button></body></html>',
        ),
        original_selector="#submit-btn-old",
        description="登录按钮",
        dom_fingerprint=None,
        page_fingerprint="",
        element_context=None,
    )

    best = gen.best_candidate(context)

    assert best is not None
    assert best.strategy == "heuristic"
    assert best.confidence >= settings.healing.early_accept_threshold  # 早接受路径
