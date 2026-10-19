"""R5 回归：语义 LLM 段的「意图依据闸门」（描述与页面候选的交叉校验）。

来源：docs/backlog/low-priority.md §三 R5 —— 视觉段有 C4 融合降权（`visual.py` 用
`score_selector` 的 L2 分做交叉校验），语义 LLM 段此前没有，导致真实模型对**与页面完全无关
的描述**（`#ghost-btn-old` + `zzz_不存在的描述`）也高置信采纳一个候选（点到 `#ghost-btn`），
使"故意不可自愈 → 走兜底"的场景失效。

本闸门（`semantic._description_has_basis`）**只做布尔下限**：描述与页面任一候选的词元/子串
都无交集 → 拒绝（返回 None，交下一策略/人审）；有交集则放行、**不改动**采纳置信度。
刻意不照搬视觉段的 `conf × (0.4 + 0.6 × l2)`：实测 `#submit-btn-old` + `登录按钮` 的 l2 仅约
0.2，融合后 0.9 → 0.468 会跌破 0.6 阈值，误伤合法改述场景（真实语义冒烟会失败）。

不触网：LLM 一律用 `FakeLLMClient`。
"""

from __future__ import annotations

import json

import pytest

from selfheal.agent.strategies.base import RepairCandidate
from selfheal.agent.strategies.semantic import (
    INTENT_BASIS_MIN_SPAN,
    SemanticStrategy,
    _description_has_basis,
)
from selfheal.collect.collector import Scene
from selfheal.dom import Element, parse_interactive_elements
from tests.unit.fake_llm import FakeLLMClient

pytestmark = pytest.mark.unit

# 与 tests/e2e/pages/demo_page.html 同型：登录按钮（aria-label 与描述一致）+ 属性稀少的 ghost 按钮
DEMO = """
<html><body>
  <form id="login-form">
    <input data-testid="username" name="username" aria-label="用户名" placeholder="用户名" />
    <button type="button" id="submit-btn-v2" data-testid="submit-btn" aria-label="登录按钮">登录</button>
    <button type="button" id="ghost-btn">Go</button>
    <button type="button" id="real-secondary" data-testid="secondary" aria-label="备用入口">备用入口</button>
  </form>
</body></html>
"""
SCENE = Scene(url="x", dom_snapshot=DEMO)
_SUBMIT = '[data-testid="submit-btn"]'
_GHOST = "#ghost-btn"


def _reply(selector: str, confidence: float) -> str:
    return json.dumps({"selector": selector, "confidence": confidence}, ensure_ascii=False)


def _run(selector: str, confidence: float, description: str | None):
    """跑一次语义策略，同时把 fake client 交回（供"是否真的调用过模型"断言）。"""
    fake = FakeLLMClient(responses=[_reply(selector, confidence)])
    cand = SemanticStrategy(client=fake).repair(SCENE, "#ghost-btn-old", description)
    return fake, cand


# --- ① R5 复现：无关描述 + 高自报置信 → 拒绝 ---


def test_unrelated_description_rejects_high_confidence_candidate():
    """复现 R5 的 ghost 场景：描述与页面无任何交集 → 即便模型自报 0.99 也不采纳。

    夹具里同时把"陷阱"显式化：原选择器 `#ghost-btn-old` 与候选 `#ghost-btn` 共享 ghost/btn
    字面重叠（这正是当初误采纳的来源），因此闸门必须**只看描述**才能拦住它。
    """
    from selfheal.agent.strategies.heuristic import _tokenize

    assert _tokenize("#ghost-btn-old") & _tokenize(_GHOST)  # 陷阱确实存在（原选择器有重叠）
    fake, cand = _run(_GHOST, 0.99, "zzz_不存在的描述")
    assert cand is None


def test_unrelated_description_is_rejected_before_calling_model(caplog):
    """闸门放在模型调用**之前**：既然"无依据"必然不采纳，就不该为它付一次模型调用。

    拒绝原因必须可见（R4：不允许静默失败）——记 debug 说明被拒理由。
    """
    import logging

    with caplog.at_level(logging.DEBUG, logger="selfheal.agent.strategies.semantic"):
        fake, cand = _run(_GHOST, 0.99, "zzz_不存在的描述")
    assert cand is None
    assert fake.calls == []  # 零模型调用（省成本/延迟）
    assert any("无词元/子串交集" in r.getMessage() for r in caplog.records)


# --- ② 合法场景不得误伤（登录按钮：描述与 aria-label 一致） ---


def test_legit_description_is_accepted_unchanged():
    """`#submit-btn-old` + `登录按钮`：闸门放行，且置信度**不被融合降权**（保持 0.9）。"""
    fake, cand = _run(_SUBMIT, 0.9, "登录按钮")
    assert isinstance(cand, RepairCandidate)
    assert cand.selector == _SUBMIT
    assert cand.confidence == 0.9
    assert fake.calls  # 合法路径照常调用模型


def test_containment_basis_covers_paraphrase_without_token_overlap():
    """合法改述：描述 `登录` 与候选文本/aria 互为子串（词元不同）→ 有依据、放行。"""
    _, cand = _run(_SUBMIT, 0.8, "登录")
    assert cand is not None and cand.selector == _SUBMIT


def test_high_confidence_is_not_fused_with_l2():
    """闸门是布尔下限，不是视觉段的融合降权：低自报值原样保留，不被 L2 二次折算。"""
    _, cand = _run(_SUBMIT, 0.4, "登录按钮")
    assert cand is not None and cand.confidence == 0.4


# --- ③ description 缺失：行为与改动前逐字节一致（基线对照） ---


@pytest.mark.parametrize(
    ("description", "baseline"),
    [
        (None, None),  # 基线（commit 2aa26d0 实测）：入口护栏直接返回 None，模型零调用
        ("", None),
    ],
)
def test_missing_description_matches_pre_change_baseline(description, baseline, caplog):
    """description 为空 → 闸门**不参与**（走既有入口护栏），结果与改动前一致。

    基线值取自改动前实测（`_llm_semantic` 入口 `not description` → None，fake.calls == []）。
    """
    import logging

    with caplog.at_level(logging.DEBUG, logger="selfheal.agent.strategies.semantic"):
        fake, cand = _run(_SUBMIT, 0.9, description)
    assert cand is baseline
    assert fake.calls == []
    assert not any("无词元/子串交集" in r.getMessage() for r in caplog.records)  # 闸门未触发


# --- ④ 判定口径：任一候选有交集即放行（不判"LLM 选得对不对"） ---


def test_description_matching_only_a_non_selected_candidate_is_accepted():
    """描述只与**非选中**候选（备用入口）有交集 → 不因闸门误拒。

    判定口径：闸门回答的是"描述是否指向本页某处"（页面级依据），不是"LLM 是否选对了元素"——
    后者需要真实数据标定（见模块 docstring 指向 agent/confidence.py 的标定点）。
    """
    _, cand = _run(_SUBMIT, 0.9, "备用入口")
    assert cand is not None and cand.selector == _SUBMIT


# --- 边界：单字符子串不算依据 ---


def test_single_char_substring_is_not_basis():
    """`登` 是候选文案 `登录按钮` 的单字符子串：属噪声，不构成意图依据（见 INTENT_BASIS_MIN_SPAN）。"""
    assert INTENT_BASIS_MIN_SPAN == 2
    _, cand = _run(_GHOST, 0.99, "登")
    assert cand is None


# --- 闸门判据直测（字段口径与 L2 打分同源） ---


def _elements() -> list[Element]:
    return parse_interactive_elements(DEMO)


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("登录按钮", True),  # aria-label 全等
        ("登录", True),  # 子串（候选文本）
        ("submit", True),  # data-testid 词元
        ("secondary", True),  # 非选中候选的 data-testid（页面级依据）
        ("备用入口", True),  # 另一候选的 aria-label
        ("ghost", True),  # id 词元命中的是 ghost-btn，但闸门只看"本页有没有落点"
        ("zzz_不存在的描述", False),  # R5 缺陷描述
        ("用户名", True),  # input 的 name/aria-label/placeholder
        ("完全无关的一段话", False),
        ("登", False),  # 单字符子串 → 噪声
        ("   ", False),  # 纯空白 → 无依据（函数自洽）
    ],
)
def test_description_has_basis_matrix(description, expected):
    assert _description_has_basis(description, _elements()) is expected


def test_description_has_basis_ignores_original_selector():
    """函数签名里没有原选择器——结构性保证依据只看描述（防 R5 缺陷复活）。"""
    import inspect

    params = list(inspect.signature(_description_has_basis).parameters)
    assert params == ["description", "elements"]
