"""单元测试：D2 风险控制 —— T15 修复写回人审清单 / T16 verified flaky / T17 成本看板（不触网）。"""

import pytest

from selfheal.agent.orchestrator import HealOutcome, SelfHealOrchestrator
from selfheal.collect.collector import Scene
from selfheal.config import Settings
from selfheal.knowledge.store import KnowledgeStore
from selfheal.reporting.dashboard import render_dashboard
from selfheal.reporting.fix_proposals import estimate_cost
from selfheal.reporting.hooks import HealingRecord, HealingReporter
from selfheal.reporting.metrics import compute_metrics

pytestmark = pytest.mark.unit

LOGIN_DOM = '<html><body><button id="login">登录</button></body></html>'


class _FakeCollector:
    def __init__(self, url: str, dom: str | None = None):
        self._url = url
        self._dom = dom

    def capture(self) -> Scene:
        return Scene(url=self._url, dom_snapshot=self._dom)


class _FakePage:
    """可控页面：locator.count 按 present 集合返回，便于验证 verified 判定。"""

    def __init__(self, present: set[str]):
        self._present = present

    @property
    def url(self):
        return "file:///demo"

    def screenshot(self, **k):
        return b""

    def content(self):
        return LOGIN_DOM

    def evaluate(self, *a, **k):
        return None

    def locator(self, sel, **k):
        present = self._present

        class _Loc:
            def count(self):
                return 1 if sel in present else 0

        return _Loc()


# --- T16：verified / flaky 指标 ---


def test_metrics_verified_stats():
    records = [
        HealingRecord("#a", "#an", "heuristic", 0.9, "not_found", True, True),
        HealingRecord("#b", "#bn", "semantic", 0.8, "covered", True, False),
        HealingRecord("#c", None, "heuristic", 0.3, "not_found", False, False),
    ]
    m = compute_metrics(records)
    assert m["verified"] == 1
    assert m["flaky"] == 2
    assert m["verified_rate"] == 0.5  # 成功 2 条中 1 条真修复


def test_record_verified_true_when_original_still_broken():
    """原定位器修复后仍不存在 → 真自愈（verified=True）。"""
    page = _FakePage(present={'[data-testid="login"]'})
    orch = SelfHealOrchestrator(page, Settings(), knowledge=KnowledgeStore())
    outcome = HealOutcome(success=True, new_selector='[data-testid="login"]', confidence=0.9, strategy="heuristic")
    orch._record("#old", outcome)
    assert orch._reporter.records[0].verified is True


def test_record_verified_false_when_flaky():
    """原定位器修复后已恢复 → 失败是瞬时的（flaky，verified=False）。"""
    page = _FakePage(present={'[data-testid="login"]', "#old"})
    orch = SelfHealOrchestrator(page, Settings(), knowledge=KnowledgeStore())
    outcome = HealOutcome(success=True, new_selector='[data-testid="login"]', confidence=0.9, strategy="heuristic")
    orch._record("#old", outcome)
    assert orch._reporter.records[0].verified is False


def test_dashboard_shows_verified_and_cost():
    recs = [HealingRecord("#a", "#an", "h", 0.9, "nf", True, True)]
    cost = estimate_cost(2, 1)
    html = render_dashboard(recs, cost)
    assert "真自愈" in html
    assert "flaky 侥幸通过" in html
    assert "LLM 调用次数" in html
    assert "估算费用" in html
    assert "VLM 调用次数" in html


# --- T17：成本统计 ---


def test_estimate_cost_defaults():
    c = estimate_cost(10, 4)
    assert c["llm_calls"] == 10 and c["vlm_calls"] == 4
    assert c["total_cost"] == round(10 * 0.02 + 4 * 0.05, 4)


def test_estimate_cost_custom_unit_cost():
    c = estimate_cost(1, 1, unit_cost={"llm": 0.1, "vlm": 0.2})
    assert c["total_cost"] == 0.3


def test_reporter_cost_summary():
    reporter = HealingReporter()
    reporter.stats = {"llm_calls": 2, "vlm_calls": 3}
    c = reporter.cost_summary()
    assert c["llm_calls"] == 2 and c["vlm_calls"] == 3
    assert c["total_cost"] == round(2 * 0.02 + 3 * 0.05, 4)


def test_counting_client_increments_stats():
    from selfheal.llm.base import ChatMessage

    class _FakeLLM:
        def chat(self, messages, **kwargs):
            return '{"selector": "#login", "confidence": 0.9}'

    orch = SelfHealOrchestrator(page=None, settings=Settings(), knowledge=KnowledgeStore())
    proxy = orch._counting_client(_FakeLLM(), "llm_calls")
    proxy.chat([ChatMessage("user", "x")])
    assert orch._reporter.stats["llm_calls"] == 1


# --- T15：修复写回人审清单 ---


def test_fix_proposal_written_on_heal(monkeypatch, tmp_path):
    from selfheal.reporting import fix_proposals

    monkeypatch.setattr(fix_proposals, "FIX_PROPOSALS_DIR", tmp_path / "fp")
    monkeypatch.setattr(fix_proposals, "FIX_PROPOSALS_MD", tmp_path / "fix-proposals.md")
    settings = Settings()
    settings.healing.fix_proposals = True
    store = KnowledgeStore()
    orch = SelfHealOrchestrator(None, settings, knowledge=store)
    orch._collector = _FakeCollector("https://x/login", LOGIN_DOM)
    out = orch.run("#old", description="登录")
    assert out.success
    assert store.count_repairs() == 1
    assert (tmp_path / "fix-proposals.md").exists()
    assert len(list((tmp_path / "fp").glob("*.json"))) == 1
    # 建议默认未应用（applied=False），供人确认后合入
    payload = (tmp_path / "fp").glob("*.json").__next__().read_text(encoding="utf-8")
    assert '"applied": false' in payload


def test_fix_proposal_disabled_by_default():
    """默认不写建议文件（零副作用）。"""
    settings = Settings()
    store = KnowledgeStore()
    orch = SelfHealOrchestrator(None, settings, knowledge=store)
    orch._collector = _FakeCollector("https://x/login", LOGIN_DOM)
    out = orch.run("#old", description="登录")
    assert out.success
    assert store.count_repairs() == 1
