"""单元测试：P4 视觉置信度一致性（C4）—— L2 交叉验证降权，防静默误修（不触网）。"""

import json

import pytest

from selfheal.agent.strategies.heuristic import score_selector
from selfheal.agent.strategies.visual import VisualStrategy
from selfheal.collect.collector import Scene

pytestmark = pytest.mark.unit

DOM = """
<html><body>
  <button data-testid="login" aria-label="登录按钮">登录</button>
  <button data-testid="cancel" aria-label="取消">取消</button>
</body></html>
"""
SCENE = Scene(url="x", screenshot=b"fake-png-bytes", dom_snapshot=DOM)
_LOGIN = '[data-testid="login"]'
_CANCEL = '[data-testid="cancel"]'


class _FakeVLM:
    def __init__(self, reply):
        self._reply = reply

    def analyze_image(self, image, prompt, **kwargs):
        return self._reply


def _reply(selector: str, confidence: float) -> str:
    return json.dumps({"selector": selector, "confidence": confidence}, ensure_ascii=False)


# --- score_selector（跨策略一致性校验工具）---


def test_score_selector_matches_intent():
    """描述与元素文本/aria 强匹配 → L2 高分（元素"移动但属性不变"时同样高分，不误伤）。"""
    s = score_selector(DOM, _LOGIN, "#login-old", "登录按钮")
    assert s >= 0.85  # 描述互为子串 → 基础 0.85+
    assert s == pytest.approx(0.95, abs=0.001)


def test_score_selector_mismatch_low():
    """描述与元素意图偏离 → L2 低分（VLM 挑错区域的信号）。"""
    s = score_selector(DOM, _CANCEL, "#login-old", "登录按钮")
    assert s < 0.3


def test_score_selector_missing_element_zero():
    """selector 不在 DOM → 0.0（保守拒绝）。"""
    assert score_selector(DOM, "#nonexistent", "#login-old", "登录按钮") == 0.0
    assert score_selector(None, _LOGIN, "#login-old", "登录按钮") == 0.0


# --- VisualStrategy 融合 ---


def test_visual_picks_wrong_element_gets_penalized():
    """VLM 高置信（0.9）但挑中与描述无关的元素 → L2≈0 → final=0.36 < 接受阈值 → 被拒。"""
    fake = _FakeVLM(_reply(_CANCEL, 0.9))
    cand = VisualStrategy(client=fake).repair(SCENE, "#login-old", description="登录按钮")
    assert cand is not None
    assert cand.selector == _CANCEL
    assert cand.confidence == pytest.approx(0.36, abs=0.001)  # 0.9 × (0.4 + 0.6×0)


def test_visual_picks_right_element_not_penalized():
    """VLM 选中正确元素（元素移动但文本/属性不变）→ L2 高 → 融合后仍高置信（不误伤）。"""
    fake = _FakeVLM(_reply(_LOGIN, 0.9))
    cand = VisualStrategy(client=fake).repair(SCENE, "#login-old", description="登录按钮")
    assert cand is not None
    assert cand.confidence == pytest.approx(0.873, abs=0.001)  # 0.9 × (0.4 + 0.6×0.95)
    assert cand.confidence >= 0.6  # 仍可被编排器接受


def test_visual_low_vlm_confidence_still_rejected():
    """VLM 自身置信度就低 → 融合后更低。"""
    fake = _FakeVLM(_reply(_CANCEL, 0.5))
    cand = VisualStrategy(client=fake).repair(SCENE, "#login-old", description="登录按钮")
    assert cand.confidence == pytest.approx(0.2, abs=0.001)  # 0.5 × 0.4
