"""单元测试：P3 验证与沉淀闭环（B1）—— 重试成功才沉淀、失败不沉淀、commit 幂等（不依赖浏览器）。"""

import pytest

import selfheal.agent.orchestrator as orch_mod
from selfheal.agent.orchestrator import SelfHealOrchestrator
from selfheal.agent.strategies.base import RepairCandidate
from selfheal.collect.collector import Scene
from selfheal.config import Settings
from selfheal.engine.healing_locator import HealingLocator
from selfheal.knowledge.schema import RepairCase
from selfheal.knowledge.store import KnowledgeStore

pytestmark = pytest.mark.unit

LOGIN_DOM = '<html><body><button id="login">登录</button></body></html>'


class _MyTimeout(Exception):
    """类名含 Timeout，模拟 playwright 超时。"""


class _Page:
    """页面：locator 按 broken 判定 click 是否超时；count 按 broken 判定元素是否存在。"""

    def __init__(self, broken=(), handlers=None, url="https://x/login"):
        self._broken = set(broken)
        self._handlers = handlers or {}
        self._url = url

    @property
    def url(self):
        return self._url

    def locator(self, sel, **k):
        return _Loc(self, sel)

    def evaluate(self, js, sel):
        return None  # 模拟 live 提取失败 → 走快照/静态

    def content(self):
        return LOGIN_DOM

    def screenshot(self, **k):
        return b""


class _Loc:
    def __init__(self, page, sel):
        self._page = page
        self._sel = sel

    def click(self, *a, **k):
        if self._sel in self._page._broken:
            raise _MyTimeout(f"{self._sel} 不可定位")
        return self._page._handlers[self._sel]()

    def count(self):
        return 0 if self._sel in self._page._broken else 1


class _FakeCollector:
    """注入自定义 Scene（unit 无浏览器，绕开 SceneCollector 的空采集）。"""

    def __init__(self, url: str, dom: str | None = None):
        self._url = url
        self._dom = dom

    def capture(self) -> Scene:
        return Scene(url=self._url, dom_snapshot=self._dom)


class _FixedStrategy:
    """返回类属性 candidate 的假策略（注册进 _STRATEGY_REGISTRY，_build_strategy 无参实例化）。"""

    name = "fixed"
    candidate = None

    def repair(self, scene, selector, description):
        return type(self).candidate


def _use_fixed_strategy(monkeypatch, selector, confidence=0.9):
    monkeypatch.setattr(
        _FixedStrategy,
        "candidate",
        RepairCandidate(selector=selector, confidence=confidence, strategy="heuristic"),
    )
    monkeypatch.setitem(orch_mod._STRATEGY_REGISTRY, "fixed", _FixedStrategy)


def _orch(page=None, store=None, settings=None):
    settings = settings or Settings()
    orch = SelfHealOrchestrator(page=page, settings=settings, knowledge=store or KnowledgeStore())
    orch._assembler._collector = _FakeCollector("https://x/login", LOGIN_DOM)
    return orch


# --- run() 暂存 vs commit_pending（B1）---


def test_run_stages_then_commit_persists():
    """run() 成功仅暂存（不落库）；commit_pending 才真正沉淀；重复提交幂等。"""
    store = KnowledgeStore()
    orch = _orch(store=store)
    out = orch.run("#old", description="登录")
    assert out.success and out.attempt_id is not None
    assert store.count_repairs() == 0  # 未 commit → 不落库（验证未发生前不沉淀）
    orch.commit_pending(out.attempt_id)
    orch.commit_pending(out.attempt_id)  # 重复提交 → no-op
    assert store.count_repairs() == 1
    assert orch._persister._pending == {}
    assert len(orch._reporter.records) == 1  # 审计随沉淀落一次


def test_commit_unknown_id_noop():
    store = KnowledgeStore()
    orch = _orch(store=store)
    orch.commit_pending("heal-9999")  # 未知 id → no-op，不崩溃
    assert store.count_repairs() == 0


def test_l1_cached_path_not_staged():
    """L1/旧式知识复用路径：不暂存、不重复沉淀，仅记录审计。"""
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
    orch = _orch(store=store)
    out = orch.run("#old", description="登录")
    assert out.success and out.root_cause == "cached"
    assert out.attempt_id is None  # 知识复用不产生暂存
    assert orch._persister._pending == {}
    assert len(orch._reporter.records) == 1  # 复用仍记录审计（供指标统计）


# --- 引擎层：重试成功才提交 ---


def test_engine_retry_success_commits(monkeypatch):
    """自愈后重试成功 → 引擎触发 commit_pending，修复落入知识库。"""
    settings = Settings()
    settings.healing.strategy_order = ["fixed"]
    _use_fixed_strategy(monkeypatch, "#new")
    store = KnowledgeStore()
    page = _Page(broken={"#old"}, handlers={"#new": lambda: "ok"})
    orch = _orch(page=page, store=store, settings=settings)
    hl = HealingLocator(page.locator("#old"), page, "#old", settings.healing, orch)
    assert hl.click() == "ok"
    assert store.count_repairs() == 1  # 重试验证成功后沉淀
    assert len(orch._reporter.records) == 1
    assert orch._persister._pending == {}  # 已提交清空


def test_engine_all_retries_fail_no_commit(monkeypatch):
    """全部重试（含二次自愈）失败 → 不沉淀、无审计记录（未验证的修复不得入库）。"""
    settings = Settings()
    settings.healing.strategy_order = ["fixed"]
    _use_fixed_strategy(monkeypatch, "#new")
    store = KnowledgeStore()
    page = _Page(broken={"#old", "#new", "#new2"})
    orch = _orch(page=page, store=store, settings=settings)
    hl = HealingLocator(page.locator("#old"), page, "#old", settings.healing, orch)
    with pytest.raises(_MyTimeout):
        hl.click()
    assert store.count_repairs() == 0
    assert orch._reporter.records == []
