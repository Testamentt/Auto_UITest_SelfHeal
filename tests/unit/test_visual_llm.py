"""单元测试：视觉定位策略（候选护栏 + JSON 解析，不触网）。"""

import json

import pytest

from selfheal.agent.strategies.base import RepairCandidate
from selfheal.agent.strategies.visual import VisualStrategy
from selfheal.collect.collector import Scene
from tests.unit.fake_vision import FakeVisionClient

pytestmark = pytest.mark.unit

DOM = """
<html><body>
  <button id="submit-btn-v2" data-testid="submit-btn" aria-label="登录按钮">登录</button>
  <button id="ghost-btn">Go</button>
</body></html>
"""

SCENE = Scene(url="x", screenshot=b"fake-png-bytes", dom_snapshot=DOM)
_REAL = '[data-testid="submit-btn"]'  # 候选集中真实存在的稳定定位器


def _reply(selector: str, confidence: float) -> str:
    return json.dumps({"selector": selector, "confidence": confidence}, ensure_ascii=False)


def test_hit_returns_candidate():
    fake = FakeVisionClient(responses=[_reply(_REAL, 0.88)])
    cand = VisualStrategy(client=fake).repair(SCENE, "#old", description="登录按钮")
    assert isinstance(cand, RepairCandidate)
    assert cand.selector == _REAL
    assert cand.confidence == 0.88
    assert fake.calls and fake.calls[0][0] == b"fake-png-bytes"  # 截图已传给 VLM


def test_guard_rejects_fabricated_selector():
    fake = FakeVisionClient(responses=[_reply("#not-a-real-candidate", 0.9)])
    assert VisualStrategy(client=fake).repair(SCENE, "#old", description="登录按钮") is None


def test_confidence_out_of_range_rejected():
    fake = FakeVisionClient(responses=[_reply(_REAL, 1.5)])
    assert VisualStrategy(client=fake).repair(SCENE, "#old", description="x") is None


def test_no_client_returns_none():
    assert VisualStrategy(client=None).repair(SCENE, "#old", description="x") is None


def test_no_screenshot_returns_none():
    fake = FakeVisionClient(responses=[_reply(_REAL, 0.9)])
    scene = Scene(url="x", screenshot=None, dom_snapshot=DOM)
    assert VisualStrategy(client=fake).repair(scene, "#old", description="x") is None


def test_exception_returns_none():
    fake = FakeVisionClient(raise_on_call=RuntimeError("boom"))
    assert VisualStrategy(client=fake).repair(SCENE, "#old", description="x") is None


def test_garbage_reply_returns_none():
    fake = FakeVisionClient(responses=["不是 JSON"])
    assert VisualStrategy(client=fake).repair(SCENE, "#old", description="x") is None
