# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""单元测试：自愈轮次与升级人工（round / escalate）。

契约：
1. `HealOutcome.round` 由引擎按实际尝试轮次盖章（1=修根，2=修叶）；
2. 两轮都修不回（且无 fallback）→ 抛 `HealingFailedError` 之前记一条
   `escalate=True, success=False, round=N` 的审计记录；
3. 首轮就修不回 → 只记 round=1，不会白跑第二轮；
4. `HealingRecord.round / escalate` 默认值保持既有构造点零改动；
5. 看板展示轮次与"需人工"类型。
"""

from __future__ import annotations

import pytest

from selfheal.agent.orchestrator import HealOutcome, SelfHealOrchestrator
from selfheal.agent.persistence import PersistenceHandler
from selfheal.config import HealingConfig, Settings
from selfheal.engine.healing_locator import HealingFailedError, HealingLocator
from selfheal.knowledge.store import KnowledgeStore
from selfheal.reporting.dashboard import render_dashboard
from selfheal.reporting.hooks import HealingRecord, HealingReporter

pytestmark = pytest.mark.unit


class _MyTimeout(Exception):
    """类名含 Timeout：模拟 Playwright 超时（无浏览器单测的既有约定）。"""


class _Loc:
    """最小 Locator 替身：动作按需抛超时。"""

    def __init__(self, *, result="ok", raises: BaseException | None = None):
        self.result = result
        self.raises = raises
        self.calls = 0

    def click(self, *args, **kwargs):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.result

    def count(self):
        return 1


class _Page:
    def __init__(self, mapping):
        self._mapping = mapping

    def locator(self, selector, **kwargs):
        return self._mapping[selector]


class _Orch:
    """按序返回 outcome，并记录 record_escalation 调用。"""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.runs: list[str] = []
        self.escalations: list[tuple] = []

    def run(self, sel, desc, failure=None, use_knowledge=True, action=None):
        self.runs.append(sel)
        return self._outcomes.pop(0)

    def commit_pending(self, attempt_id):
        return None

    def discard_pending(self, attempt_id):
        return None

    def record_escalation(self, selector, description=None, round_no=1):
        self.escalations.append((selector, round_no))


def _cfg(*, on_uncertain="use_fallback") -> HealingConfig:
    cfg = HealingConfig()
    cfg.on_uncertain = on_uncertain
    return cfg


# ---------------------------------------------------------------------------
# 1. 数据模型默认值
# ---------------------------------------------------------------------------


def test_record_defaults_keep_backward_compat():
    """既有构造点（位置参数 5~7 个）无需改动：round/escalate 有默认值。"""
    rec = HealingRecord("#old", "#new", "heuristic", 0.9, "not_found", True)
    assert rec.round == 1
    assert rec.escalate is False


def test_outcome_round_default_is_one():
    assert HealOutcome(success=True).round == 1


# ---------------------------------------------------------------------------
# 2. 引擎盖章轮次
# ---------------------------------------------------------------------------


def test_heal_and_resolve_stamps_round_two():
    """第二轮闭环的 outcome 被盖上 round=2。"""
    page = _Page({"#old": _Loc(), "#new": _Loc()})
    orch = _Orch([HealOutcome(success=True, new_selector="#new")])
    locator = HealingLocator(page.locator("#old"), page, "#old", _cfg(), orch)

    locator._heal_and_resolve(use_knowledge=False, leaf=True, round_no=2)
    assert locator._last_heal_outcome is not None
    assert locator._last_heal_outcome.round == 2


# ---------------------------------------------------------------------------
# 3. 轮次耗尽 → 升级人工
# ---------------------------------------------------------------------------


def test_two_rounds_exhausted_records_escalation_round_two():
    """修根成功但重放仍超时 → 修叶也失败 → 记录 escalate(round=2) 后抛错。"""
    broken_after_heal = _Loc(raises=_MyTimeout("Timeout 2500ms exceeded"))
    page = _Page({"#old": _Loc(raises=_MyTimeout("Timeout")), "#new": broken_after_heal})
    orch = _Orch(
        [
            HealOutcome(success=True, new_selector="#new", confidence=0.9),  # 第一轮：修根
            HealOutcome(success=False, root_cause="not_found"),  # 第二轮：修叶失败
        ]
    )
    locator = HealingLocator(page.locator("#old"), page, "#old", _cfg(), orch)

    with pytest.raises(HealingFailedError):
        locator.click()
    assert orch.escalations == [("#old", 2)]


def test_first_round_exhausted_records_escalation_round_one():
    """首轮就给不出可用定位 → 只记 round=1，不会再跑第二轮（避免浪费一次闭环）。"""
    page = _Page({"#old": _Loc(raises=_MyTimeout("Timeout"))})
    orch = _Orch([HealOutcome(success=False, root_cause="not_found")])
    locator = HealingLocator(page.locator("#old"), page, "#old", _cfg(), orch)

    with pytest.raises(HealingFailedError):
        locator.click()
    assert orch.escalations == [("#old", 1)]
    assert len(orch.runs) == 1  # 没有第二次闭环


def test_escalation_absent_when_fallback_rescues():
    """有 fallback 时自愈失败走兜底动作，不算"需人工"（不记 escalate）。"""
    fallback = _Loc(result="fallback-ok")
    page = _Page({"#old": _Loc(raises=_MyTimeout("Timeout")), "#fb": fallback})
    orch = _Orch([HealOutcome(success=False, root_cause="not_found")])
    locator = HealingLocator(page.locator("#old"), page, "#old", _cfg(), orch, fallback="#fb")

    assert locator.click() == "fallback-ok"
    assert orch.escalations == []


# ---------------------------------------------------------------------------
# 4. 编排器 / 持久化 / 看板
# ---------------------------------------------------------------------------


def _orchestrator() -> SelfHealOrchestrator:
    return SelfHealOrchestrator(
        page=None,
        settings=Settings(),
        knowledge=KnowledgeStore(),
        reporter=HealingReporter(),
    )


def test_orchestrator_record_escalation_flags_record():
    orch = _orchestrator()
    try:
        orch.record_escalation("#submit-btn-old", "登录按钮", round_no=2)
        recs = orch._reporter.records
        assert len(recs) == 1
        rec = recs[0]
        assert rec.original_selector == "#submit-btn-old"
        assert rec.success is False
        assert rec.verified is False
        assert rec.escalate is True
        assert rec.round == 2
        assert rec.root_cause == "rounds_exhausted"
    finally:
        orch.close()


def test_persistence_records_outcome_round():
    """成功记录继承 outcome.round（第 2 轮修好的会写明）。"""
    handler = PersistenceHandler(Settings(), KnowledgeStore(), HealingReporter(), None, None)
    handler._record("#old", HealOutcome(success=True, new_selector="#new", round=2))
    rec = handler._reporter.records[-1]
    assert rec.round == 2
    assert rec.escalate is False


def test_dashboard_renders_round_and_escalation():
    records = [
        HealingRecord("#old", "#new", "heuristic", 0.9, "not_found", True, round=2),
        HealingRecord("#x", None, None, 0.0, "rounds_exhausted", False, round=2, escalate=True),
    ]
    html = render_dashboard(records)
    assert "第 2 轮" in html
    assert "需人工" in html
    assert "第 1 轮" not in html
