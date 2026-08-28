"""单元测试：T5 置信度归一化 / 按策略阈值（不触网）。

覆盖四层：
1. agent/confidence.py 归一化层：默认恒等、自报收缩开关、注册表可扩展、越界钳制；
2. config 按策略阈值：缺省回退全局、覆盖生效、validator 防倒挂/越界；
3. 策略产出点接线：semantic_llm 收缩生效、heuristic/visual 恒等；
4. 路由接线：generate 采纳、resolve 成功/拒绝、best_candidate 短路、lookup_knowledge 按来源策略。
"""

import json

import pytest

import selfheal.agent.orchestrator as orch_mod
from selfheal.agent.confidence import (
    CALIBRATORS,
    KEY_HEURISTIC,
    KEY_SEMANTIC_L3,
    KEY_SEMANTIC_LLM,
    KEY_VISUAL,
    calibrate,
)
from selfheal.agent.context import HealingContext
from selfheal.agent.diagnose import Diagnoser
from selfheal.agent.fix_generator import FixGenerator
from selfheal.agent.persistence import PersistenceHandler
from selfheal.agent.strategies.heuristic import HeuristicStrategy
from selfheal.agent.strategies.semantic import SemanticStrategy
from selfheal.agent.strategies.visual import VisualStrategy
from selfheal.collect.collector import Scene
from selfheal.config import Settings
from selfheal.knowledge.schema import RepairCase
from selfheal.knowledge.store import KnowledgeStore
from selfheal.reporting.hooks import HealingReporter
from tests.unit.fake_llm import FakeLLMClient
from tests.unit.fake_vision import FakeVisionClient

pytestmark = pytest.mark.unit

# 子串强信号 + 词元重叠 → heuristic 0.95 的稳定 DOM（与 test_heuristic 同型）
_DOM_STRONG = '<html><body><button aria-label="登录按钮">登录</button></body></html>'
# 语义/视觉 LLM 回复的候选 selector 必须真实存在于 DOM（护栏）：
_DOM_CANDIDATE = (
    '<html><body><button data-testid="submit-btn" aria-label="登录按钮">登录</button></body></html>'
)


def _ctx(dom: str, description: str = "登录按钮") -> HealingContext:
    """构造一次闭环上下文（页面不可用：selector_exists 视为存在）。"""
    return HealingContext(
        scene=Scene(url="demo://page", dom_snapshot=dom),
        original_selector="#submit-btn-old",
        description=description,
        dom_fingerprint=None,
        page_fingerprint="",
        element_context=None,
    )


def _fix_gen(
    settings: Settings,
    knowledge: KnowledgeStore | None = None,
    llm=None,
    vision=None,
) -> FixGenerator:
    """构造 FixGenerator（无浏览器 / 无真模型，可注入 fake 客户端）。"""
    return FixGenerator(
        settings=settings,
        knowledge=knowledge if knowledge is not None else KnowledgeStore(),
        reporter=HealingReporter(),
        rule_diagnoser=Diagnoser(),
        llm_diagnoser=None,
        llm_client=llm,
        vision_client=vision,
        embedding=None,
        page=None,
        strategy_registry=dict(orch_mod._STRATEGY_REGISTRY),
    )


def _persister(settings: Settings) -> PersistenceHandler:
    return PersistenceHandler(
        settings, KnowledgeStore(), HealingReporter(), embedding=None, page=None
    )


# --- 1. 归一化层：默认恒等 ---


def test_calibrate_identity_by_default():
    """所有策略键默认恒等（含边界 0/1 与精度），保证 T4 后行为零变化。"""
    for key in (KEY_HEURISTIC, KEY_SEMANTIC_L3, KEY_SEMANTIC_LLM, KEY_VISUAL):
        assert calibrate(key, 0.935) == 0.935
        assert calibrate(key, 0.0) == 0.0
        assert calibrate(key, 1.0) == 1.0


def test_calibrate_unknown_key_identity():
    """未知策略键按恒等防御（新策略忘登记不改变行为）。"""
    assert calibrate("no_such_strategy", 0.7) == 0.7


# --- 2. 归一化层：自报收缩（仅 semantic_llm、需显式开关） ---


def test_shrink_applies_only_to_semantic_llm_when_enabled():
    """shrink 开启：semantic_llm 自报保守收缩（raw²）；其他标尺不受影响（防双重惩罚）。"""
    assert calibrate(KEY_SEMANTIC_LLM, 0.9, shrink_self_reported=True) == 0.81
    assert calibrate(KEY_SEMANTIC_LLM, 0.7, shrink_self_reported=True) == 0.49
    assert calibrate(KEY_SEMANTIC_LLM, 0.5, shrink_self_reported=True) == 0.25
    assert calibrate(KEY_SEMANTIC_LLM, 1.0, shrink_self_reported=True) == 1.0
    for key in (KEY_HEURISTIC, KEY_SEMANTIC_L3, KEY_VISUAL):
        assert calibrate(key, 0.9, shrink_self_reported=True) == 0.9


def test_shrink_off_is_identity_for_semantic_llm():
    """shrink 关闭时 semantic_llm 也恒等（默认行为不变）。"""
    assert calibrate(KEY_SEMANTIC_LLM, 0.9) == 0.9


# --- 3. 归一化层：注册表可扩展 + 越界钳制 ---


def test_calibrator_registry_extensible(monkeypatch):
    """注册表可插拔：登记自定义校准函数即时生效（未来数据标定替换点）。"""
    monkeypatch.setitem(CALIBRATORS, "custom", lambda raw, shrink: raw * 0.5)
    assert calibrate("custom", 0.8) == 0.4
    assert calibrate("custom", 0.8, shrink_self_reported=True) == 0.4


def test_calibrate_clamps_out_of_range(monkeypatch):
    """校准实现把有效置信度推出 [0,1] 时钳制（防御值域漂移，不静默吞掉）。"""
    monkeypatch.setitem(CALIBRATORS, "wild", lambda raw, shrink: 1.5 if raw > 0.5 else -0.2)
    assert calibrate("wild", 0.9) == 1.0
    assert calibrate("wild", 0.1) == 0.0


# --- 4. 配置：按策略阈值（缺省回退 / 覆盖 / validator） ---


def test_config_per_strategy_defaults_fallback():
    """未配置按策略阈值时回退全局（既有行为保持）。"""
    h = Settings().healing
    assert h.accept_threshold("heuristic") == 0.6
    assert h.accept_threshold("no_such") == 0.6
    assert h.early_accept_for("visual") == 0.85
    assert h.early_accept_for("no_such") == 0.85


def test_config_per_strategy_override():
    """配置的策略用独立阈值；未配置的照旧回退全局。"""
    s = Settings()
    s.healing.strategy_thresholds = {"heuristic": 0.65}
    s.healing.strategy_early_accept = {"visual": 0.9}
    assert s.healing.accept_threshold("heuristic") == 0.65
    assert s.healing.accept_threshold("visual") == 0.6  # 未配置 → 回退全局
    assert s.healing.early_accept_for("visual") == 0.9
    assert s.healing.early_accept_for("heuristic") == 0.85  # 未配置 → 回退全局


def test_config_rejects_out_of_range():
    """按策略阈值越出 [0,1] → 加载期报错（防倒挂/脏配置）。"""
    with pytest.raises(ValueError):
        Settings(healing={"strategy_thresholds": {"heuristic": 1.5}})
    with pytest.raises(ValueError):
        Settings(healing={"strategy_early_accept": {"visual": -0.1}})


def test_config_rejects_early_not_gt_accept():
    """同策略 early_accept 必须严格 > accept（与全局层级约束同构）。"""
    with pytest.raises(ValueError):
        Settings(
            healing={
                "strategy_thresholds": {"heuristic": 0.7},
                "strategy_early_accept": {"heuristic": 0.7},
            }
        )
    ok = Settings(
        healing={
            "strategy_thresholds": {"heuristic": 0.7},
            "strategy_early_accept": {"heuristic": 0.8},
        }
    )
    assert ok.healing.early_accept_for("heuristic") == 0.8


# --- 5. 策略产出点接线 ---


def test_semantic_llm_strategy_shrink_wired():
    """SemanticStrategy 的 LLM 自报段：shrink 开关贯通到产出置信度。"""
    scene = Scene(url="x", dom_snapshot=_DOM_CANDIDATE)
    reply = json.dumps({"selector": '[data-testid="submit-btn"]', "confidence": 0.9})
    shrunk = SemanticStrategy(client=FakeLLMClient([reply]), shrink_self_reported=True)
    raw = SemanticStrategy(client=FakeLLMClient([reply]), shrink_self_reported=False)
    assert shrunk.repair(scene, "#old", "提交订单").confidence == 0.81
    assert raw.repair(scene, "#old", "提交订单").confidence == 0.9


def test_heuristic_strategy_identity_wired():
    """heuristic 产出经统一标尺出口：恒等，规则分数原样保留（0.95 强信号）。"""
    cand = HeuristicStrategy().repair(
        Scene(url="x", dom_snapshot=_DOM_STRONG), "#submit-btn-old", "登录按钮"
    )
    assert cand is not None
    assert cand.confidence == 0.95


def test_visual_strategy_identity_wired():
    """visual 产出经统一标尺出口：C4 融合结果恒等（不二次收缩）。"""
    scene = Scene(url="x", screenshot=b"fake-png", dom_snapshot=_DOM_CANDIDATE)
    reply = json.dumps({"selector": '[data-testid="submit-btn"]', "confidence": 0.9})
    cand = VisualStrategy(client=FakeVisionClient([reply])).repair(
        scene, "#submit-btn-old", "登录按钮"
    )
    assert cand is not None
    assert cand.strategy == "visual"
    # l2 = score_selector(...) ≈ 0.95 → fusion = 0.9 * (0.4 + 0.6*0.95) = 0.873（恒等输出）
    assert cand.confidence == pytest.approx(0.873)


# --- 6. 路由接线：generate 采纳 / resolve 裁决 / 短路 / 知识复用 ---


def test_generate_accepts_by_per_strategy_threshold():
    """heuristic 0.95 >= 按策略阈值 0.9 → 采纳（传统场景行为不变）。"""
    s = Settings()
    s.healing.strategy_thresholds = {"heuristic": 0.9}
    proposal = _fix_gen(s).generate(_ctx(_DOM_STRONG))
    assert proposal.best is not None
    assert proposal.best.strategy == "heuristic"
    assert proposal.best.confidence == 0.95


def test_resolve_uses_per_strategy_threshold(monkeypatch):
    """按策略阈值裁决：未配置策略回退全局（0.95 >= 全局 0.5 → 采纳）。"""
    monkeypatch.setattr("selfheal.reporting.fix_proposals.write_fix_proposal", lambda **kw: None)
    s = Settings()
    s.healing.confidence_threshold = 0.5
    s.healing.strategy_thresholds = {"visual": 0.99}  # 与 heuristic 无关 → heuristic 回退全局
    proposal = _fix_gen(s).generate(_ctx(_DOM_STRONG))
    out = _persister(s).resolve(_ctx(_DOM_STRONG), proposal)
    assert out.success is True


def test_resolve_rejects_when_strategy_threshold_high(monkeypatch):
    """同一 0.95：全局 0.5 会采纳，但按策略 heuristic=0.99 拒绝（证明按策略覆盖全局）。"""
    monkeypatch.setattr("selfheal.reporting.fix_proposals.write_fix_proposal", lambda **kw: None)
    s = Settings()
    s.healing.confidence_threshold = 0.5
    s.healing.strategy_thresholds = {"heuristic": 0.99}
    proposal = _fix_gen(s).generate(_ctx(_DOM_STRONG))
    # 拒绝路径：候选不进入 outcome（写入人审清单 fix-proposals），outcome 只带失败根因
    out = _persister(s).resolve(_ctx(_DOM_STRONG), proposal)
    assert out.success is False
    assert out.root_cause  # 带可追溯根因（供报告/审计）


def test_best_candidate_short_circuit_default():
    """默认 early_accept 0.85：heuristic 0.95 达阈值 → 短路，不调 semantic（回归 T1）。"""
    scene = Scene(url="x", dom_snapshot=_DOM_STRONG)
    fake_llm = FakeLLMClient(['{"selector":"x","confidence":0.9}'])
    ctx = HealingContext(
        scene=scene,
        original_selector="#submit-btn-old",
        description="登录按钮",
        dom_fingerprint=None,
        page_fingerprint="",
    )
    best = _fix_gen(Settings(), llm=fake_llm).best_candidate(ctx)
    assert best is not None and best.strategy == "heuristic"
    assert fake_llm.calls == []  # 短路生效


def test_best_candidate_short_circuit_respects_per_strategy():
    """按策略 early_accept 抬高到 0.99：0.95 未达 → 不短路，继续 semantic（策略阈值生效）。"""
    scene = Scene(url="x", dom_snapshot=_DOM_STRONG)
    fake_llm = FakeLLMClient(['{"selector":"x","confidence":0.9}'])
    ctx = HealingContext(
        scene=scene,
        original_selector="#submit-btn-old",
        description="登录按钮",
        dom_fingerprint=None,
        page_fingerprint="",
    )
    s = Settings()
    s.healing.strategy_early_accept = {"heuristic": 0.99}
    best = _fix_gen(s, llm=fake_llm).best_candidate(ctx)
    assert best is not None and best.strategy == "heuristic"
    assert fake_llm.calls  # 未短路：semantic 被调用


def _knowledge_ctx() -> HealingContext:
    return HealingContext(
        scene=Scene(url="demo://page", dom_snapshot="<html><body></body></html>"),
        original_selector="#submit-btn-old",
        description=None,
        dom_fingerprint=None,
        page_fingerprint="",
        element_context=None,
    )


def test_lookup_knowledge_uses_source_strategy_threshold():
    """知识复用门槛与案例来源策略的独立阈值对齐（0.65 对 visual=0.7 拒绝 / 回退全局 0.6 命中）。"""
    store = KnowledgeStore()
    store.add_repair(
        RepairCase(
            original_selector="#submit-btn-old",
            new_selector='[data-testid="submit-btn"]',
            strategy="visual",
            confidence=0.65,
            page_url="demo://page",
        )
    )
    strict = Settings()
    strict.healing.strategy_thresholds = {"visual": 0.7}  # 0.65 < 0.7 → 不命中
    assert _fix_gen(strict, knowledge=store).lookup_knowledge(_knowledge_ctx()) is None

    relaxed = Settings()  # 回退全局 0.6 → 0.65 达标 → 命中（page=None 视为选择器存在）
    out = _fix_gen(relaxed, knowledge=store).lookup_knowledge(_knowledge_ctx())
    assert out is not None
    assert out.strategy == "knowledge"
    assert out.confidence == 0.65
