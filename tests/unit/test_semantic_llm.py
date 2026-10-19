"""单元测试：语义定位策略（防幻觉护栏 + R5 意图依据闸门，不触网）。

注意：本文件的描述一律取**与 DOM 夹具候选有词元/子串交集**者（`登录按钮`）。
描述与页面零交集会被 R5 意图闸门拦在模型调用之前（用例会变成"测错分支"），
该闸门自身的回归在 tests/unit/test_semantic_intent_guard.py。
"""

import json

import pytest

from selfheal.agent.strategies.base import RepairCandidate
from selfheal.agent.strategies.semantic import SemanticStrategy
from selfheal.collect.collector import Scene
from tests.unit.fake_llm import FakeLLMClient

pytestmark = pytest.mark.unit

DOM = """
<html><body>
  <button id="submit-btn-v2" data-testid="submit-btn" aria-label="登录按钮">登录</button>
  <button id="ghost-btn">Go</button>
</body></html>
"""

SCENE = Scene(url="x", dom_snapshot=DOM)
_REAL = (
    '[data-testid="submit-btn"]'  # 由 build_stable_selector 生成的稳定定位器（在 DOM 中真实存在）
)
_DESC = "登录按钮"  # 与夹具 aria-label 一致 → 通过 R5 意图闸门


def _reply(selector: str, confidence: float) -> str:
    """用 json.dumps 构造合法 JSON（selector 含双引号时需转义，模拟真实 LLM 输出）。"""
    return json.dumps({"selector": selector, "confidence": confidence}, ensure_ascii=False)


def test_hit_returns_candidate():
    fake = FakeLLMClient(responses=[_reply(_REAL, 0.9)])
    cand = SemanticStrategy(client=fake).repair(SCENE, "#old", description=_DESC)
    assert isinstance(cand, RepairCandidate)
    assert cand.selector == _REAL
    assert cand.confidence == 0.9


def test_guard_rejects_fabricated_selector():
    fake = FakeLLMClient(responses=[_reply("#fake-not-exist", 0.9)])
    assert SemanticStrategy(client=fake).repair(SCENE, "#old", description=_DESC) is None


def test_confidence_out_of_range_rejected():
    # 描述有依据（过闸门）→ 走到置信度护栏才被拒，保证本用例测的是值域护栏本身
    fake = FakeLLMClient(responses=[_reply(_REAL, 1.5)])
    assert SemanticStrategy(client=fake).repair(SCENE, "#old", description=_DESC) is None


def test_no_client_returns_none():
    assert SemanticStrategy(client=None).repair(SCENE, "#old", description=_DESC) is None


def test_no_description_returns_none():
    fake = FakeLLMClient(responses=[_reply(_REAL, 0.9)])
    assert SemanticStrategy(client=fake).repair(SCENE, "#old", description=None) is None


def test_exception_returns_none():
    fake = FakeLLMClient(raise_on_call=RuntimeError("boom"))
    assert SemanticStrategy(client=fake).repair(SCENE, "#old", description=_DESC) is None
    assert fake.calls  # 确实走到了模型调用（异常来自模型层，不是被闸门提前拦下）


def test_garbage_reply_returns_none():
    fake = FakeLLMClient(responses=["不是 JSON"])
    assert SemanticStrategy(client=fake).repair(SCENE, "#old", description=_DESC) is None
    assert fake.calls  # 同上：解析失败分支真的被执行
