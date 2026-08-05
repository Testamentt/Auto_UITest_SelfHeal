"""单元测试：D1 风险控制 —— T13 高风险页豁免 / T14 dry-run 仅报告不执行（不触网）。"""

import pytest

from selfheal.agent.orchestrator import SelfHealOrchestrator
from selfheal.collect.collector import Scene
from selfheal.config import Settings
from selfheal.knowledge.schema import RepairCase
from selfheal.knowledge.store import KnowledgeStore

pytestmark = pytest.mark.unit

LOGIN_DOM = '<html><body><button id="login">登录</button></body></html>'


class _FakeCollector:
    """注入自定义 Scene（unit 无浏览器，绕开 SceneCollector 的空采集）。"""

    def __init__(self, url: str, dom: str | None = None):
        self._url = url
        self._dom = dom

    def capture(self) -> Scene:
        return Scene(url=self._url, dom_snapshot=self._dom)


def _orch(settings: Settings, store: KnowledgeStore | None = None, url: str = "", dom: str | None = None):
    orch = SelfHealOrchestrator(page=None, settings=settings, knowledge=store or KnowledgeStore())
    orch._assembler._collector = _FakeCollector(url, dom)
    return orch


# --- T13 高风险页豁免 ---


def test_excluded_url_skips_healing():
    settings = Settings()
    settings.healing.exclude_url_patterns = ["*://*/pay*", "*://*/admin/*"]
    orch = _orch(settings, url="https://x/pay/checkout")
    outcome = orch.run("#old")
    assert outcome.success is False
    assert outcome.root_cause == "high_risk_page_excluded"


def test_excluded_url_no_knowledge_mutation():
    settings = Settings()
    settings.healing.exclude_url_patterns = ["*://*/pay*"]
    store = KnowledgeStore()
    orch = _orch(settings, store, url="https://x/pay")
    orch.run("#old")
    assert store.count_repairs() == 0


def test_non_excluded_url_normal_flow():
    settings = Settings()
    settings.healing.exclude_url_patterns = ["*://*/pay*"]
    orch = _orch(settings, url="https://x/login", dom=LOGIN_DOM)
    outcome = orch.run("#old", description="登录")
    assert outcome.success is True
    assert outcome.new_selector == "#login"
    assert outcome.root_cause != "high_risk_page_excluded"


def test_exclude_pattern_matching():
    settings = Settings()
    settings.healing.exclude_url_patterns = ["*://*/pay*"]
    orch = _orch(settings, url="https://x/pay")
    assert orch._assembler._is_excluded("https://x/pay/step1") is True
    assert orch._assembler._is_excluded("https://x/login") is False
    assert orch._assembler._is_excluded("") is False


# --- T14 dry-run 仅报告不执行 ---


def test_dry_run_reports_proposal_not_applied(monkeypatch, tmp_path):
    from selfheal.reporting import fix_proposals

    monkeypatch.setattr(fix_proposals, "FIX_PROPOSALS_DIR", tmp_path / "fp")
    monkeypatch.setattr(fix_proposals, "FIX_PROPOSALS_MD", tmp_path / "fix-proposals.md")
    settings = Settings()
    settings.healing.dry_run = True
    store = KnowledgeStore()
    orch = _orch(settings, store, url="https://x/login", dom=LOGIN_DOM)
    outcome = orch.run("#old", description="登录")
    # 只报告、不执行：success=False + proposed_selector 给出建议
    assert outcome.success is False
    assert outcome.root_cause == "dry_run"
    assert outcome.proposed_selector == "#login"
    # 不持久化知识
    assert store.count_repairs() == 0
    # 建议已写出
    assert (tmp_path / "fp").exists()
    assert (tmp_path / "fix-proposals.md").exists()


def test_dry_run_skips_knowledge_early_return(monkeypatch, tmp_path):
    """dry_run 时不"应用"缓存修复（跳过知识早返回），仍产出建议供人审。"""
    from selfheal.reporting import fix_proposals

    monkeypatch.setattr(fix_proposals, "FIX_PROPOSALS_DIR", tmp_path / "fp")
    monkeypatch.setattr(fix_proposals, "FIX_PROPOSALS_MD", tmp_path / "fix-proposals.md")
    settings = Settings()
    settings.healing.dry_run = True
    store = KnowledgeStore()
    store.add_repair(
        RepairCase(
            original_selector="#old",
            new_selector="#cached",
            strategy="heuristic",
            confidence=0.9,
            page_url="https://x/login",
        )
    )
    orch = _orch(settings, store, url="https://x/login", dom=LOGIN_DOM)
    outcome = orch.run("#old", description="登录")
    assert outcome.success is False
    assert outcome.root_cause == "dry_run"
    assert outcome.proposed_selector != "#cached"  # 未直接应用缓存


def test_dry_run_failure_report_includes_proposal():
    from types import SimpleNamespace

    from selfheal.engine.healing_locator import HealingLocator

    settings = Settings()
    settings.healing.dry_run = True
    orch = _orch(settings, url="https://x/login", dom=LOGIN_DOM)
    outcome = orch.run("#old", description="登录")
    loc = HealingLocator(
        locator=None,
        page=None,
        selector="#old",
        cfg=SimpleNamespace(on_uncertain="use_fallback"),
        orchestrator=None,
        fallback=None,
        description="登录",
    )
    report = loc._failure_report(outcome)
    assert "dry_run" in report
    assert outcome.proposed_selector in report
