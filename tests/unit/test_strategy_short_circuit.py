"""单元测试：T1 策略短路（early_accept_threshold，不触网）。

启发式高置信命中时短路，不再调用 semantic(LLM)/visual(VLM)；
启发式置信不足时仍继续尝试后续策略。
"""

import json

import pytest

import selfheal.agent.orchestrator as orch_mod
from selfheal.agent.orchestrator import SelfHealOrchestrator
from selfheal.collect.collector import Scene
from selfheal.config import Settings
from selfheal.knowledge.store import KnowledgeStore
from tests.unit.fake_llm import FakeLLMClient
from tests.unit.fake_vision import FakeVisionClient

pytestmark = pytest.mark.unit


def _orch(llm=None, vision=None) -> SelfHealOrchestrator:
    return SelfHealOrchestrator(
        page=None,
        settings=Settings(),
        knowledge=KnowledgeStore(),
        llm_client=llm,
        vision_client=vision,
    )


def test_short_circuit_skips_costly_strategies():
    """启发式高置信（>=early_accept）命中 → 短路，不调 LLM/VLM。"""
    dom = '<html><body><button id="s" data-testid="submit-btn" aria-label="登录按钮">登录</button></body></html>'
    scene = Scene(url="x", dom_snapshot=dom)
    fake_llm = FakeLLMClient(responses=['{"selector":"x","confidence":0.9}'])
    fake_vision = FakeVisionClient(responses=['{"selector":"x","confidence":0.9}'])

    best = _orch(llm=fake_llm, vision=fake_vision)._best_candidate(scene, "#old-selector", "登录按钮")

    assert best is not None
    assert best.strategy == "heuristic"
    assert best.confidence >= 0.85
    assert fake_llm.calls == []  # semantic 被短路，未调 LLM
    assert fake_vision.calls == []  # visual 被短路，未调 VLM


def test_no_short_circuit_when_below_threshold():
    """启发式无强信号（<early_accept）→ 仍尝试 semantic（LLM 被调用）。"""
    dom = '<html><body><button data-testid="go-btn">Go</button></body></html>'
    scene = Scene(url="x", dom_snapshot=dom)
    reply = json.dumps({"selector": '[data-testid="go-btn"]', "confidence": 0.9})
    fake_llm = FakeLLMClient(responses=[reply])

    best = _orch(llm=fake_llm)._best_candidate(scene, "#totally-unrelated-xyz", "提交订单")

    assert fake_llm.calls  # semantic 被调用（未短路）
    assert best is not None
    assert best.strategy == "semantic"


class _BoomStrategy:
    """模拟内部异常的策略（如 embedding/数据崩溃），注册进策略注册表。"""

    name = "boom"

    def repair(self, scene, selector, description):
        raise RuntimeError("策略内部故障")


def test_strategy_exception_does_not_break_chain(monkeypatch):
    """审查 C4 回归：首个策略抛异常 → 不中断策略链，后续策略继续执行并可用。"""
    settings = Settings()
    settings.healing.strategy_order = ["boom", "heuristic"]
    monkeypatch.setitem(orch_mod._STRATEGY_REGISTRY, "boom", _BoomStrategy)
    dom = '<html><body><button id="s" data-testid="submit-btn" aria-label="登录按钮">登录</button></body></html>'
    scene = Scene(url="x", dom_snapshot=dom)

    best = _orch()._best_candidate(scene, "#old-selector", "登录按钮")

    # 故障策略被跳过，启发式正常产出候选（修复前整条链中断、best 为 None）
    assert best is not None
    assert best.strategy == "heuristic"
    assert best.confidence >= 0.85


def test_all_strategies_fail_returns_none(monkeypatch):
    """全部策略异常 → best 为 None（不崩溃，走失败/人审路径）。"""
    settings = Settings()
    settings.healing.strategy_order = ["boom"]
    monkeypatch.setitem(orch_mod._STRATEGY_REGISTRY, "boom", _BoomStrategy)
    scene = Scene(url="x", dom_snapshot="<html><body></body></html>")
    assert _orch(settings)._best_candidate(scene, "#x", "描述") is None
