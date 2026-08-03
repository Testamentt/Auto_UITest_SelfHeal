"""单元测试：LLM 智能诊断（降级路径全覆盖，不触网）。"""

import pytest

from selfheal.agent.diagnose import Diagnoser, FailureContext
from selfheal.agent.diagnose_llm import LLMDiagnoser
from selfheal.collect.collector import Scene
from tests.unit.fake_llm import FakeLLMClient

pytestmark = pytest.mark.unit

DOM = """
<html><body>
  <button id="submit-btn-v2" data-testid="submit-btn" aria-label="登录按钮">登录</button>
</body></html>
"""

SCENE = Scene(url="https://example.com/login", dom_snapshot=DOM)


def test_valid_cause_accepted():
    diag = LLMDiagnoser(FakeLLMClient(responses=['{"root_cause": "covered", "reason": "被弹窗遮挡"}']))
    assert diag.diagnose(SCENE, "#submit-btn-old", FailureContext("TimeoutError", "timeout")) == "covered"


def test_out_of_whitelist_returns_unknown():
    diag = LLMDiagnoser(FakeLLMClient(responses=['{"root_cause": "banana", "reason": "x"}']))
    assert diag.diagnose(SCENE, "#a") == "unknown"


def test_garbage_reply_returns_unknown():
    diag = LLMDiagnoser(FakeLLMClient(responses=["完全不像是 JSON 的内容"]))
    assert diag.diagnose(SCENE, "#a") == "unknown"


def test_exception_falls_back_to_rule():
    diag = LLMDiagnoser(FakeLLMClient(raise_on_call=RuntimeError("boom")))
    # 规则式：主键在 DOM 中不存在 → not_found
    assert diag.diagnose(SCENE, "#ghost-no") == "not_found"


def test_rule_fallback_matches_phase1():
    """无 LLM 时 LLMDiagnoser 退化为与 Phase 1 规则式一致。"""
    llm = LLMDiagnoser(FakeLLMClient(raise_on_call=RuntimeError("x")))
    rule = Diagnoser()
    assert llm.diagnose(SCENE, "#submit-btn-old") == rule.diagnose(SCENE, "#submit-btn-old")
