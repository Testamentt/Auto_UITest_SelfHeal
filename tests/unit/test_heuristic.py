"""单元测试：启发式策略的 DOM 解析与相似度打分（不依赖浏览器）。"""

import pytest

from selfheal.agent.strategies.heuristic import HeuristicStrategy
from selfheal.collect.collector import Scene

pytestmark = pytest.mark.unit

DOM = """
<html><body>
  <input data-testid="username" name="username" aria-label="用户名" />
  <button id="submit-btn-v2" data-testid="submit-btn" aria-label="登录按钮">登录</button>
  <button id="ghost-btn">Go</button>
</body></html>
"""


def test_heal_by_description_containment():
    """描述与候选 aria-label 互为子串 → 高置信度命中 data-testid。"""
    scene = Scene(url="file:///demo", dom_snapshot=DOM)
    cand = HeuristicStrategy().repair(scene, "#submit-btn-old", description="登录按钮")
    assert cand is not None
    assert cand.selector == '[data-testid="submit-btn"]'
    assert cand.confidence >= 0.6
    assert cand.strategy == "heuristic"


def test_heal_by_token_match_without_description():
    """无描述时，原选择器词元与 data-testid 完全重叠 → 命中。"""
    dom = '<html><body><button data-testid="submit-btn">Go</button></body></html>'
    cand = HeuristicStrategy().repair(Scene(url="x", dom_snapshot=dom), "#submit-btn")
    assert cand is not None
    assert cand.selector == '[data-testid="submit-btn"]'
    assert cand.confidence >= 0.6


def test_no_interactive_candidate_returns_none():
    scene = Scene(url="x", dom_snapshot="<html><body><div>纯文本</div></body></html>")
    assert HeuristicStrategy().repair(scene, "#nope", description="zzz") is None


def test_empty_dom_returns_none():
    assert HeuristicStrategy().repair(Scene(url="x", dom_snapshot=None), "#a") is None
