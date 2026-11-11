"""单元测试：修复候选定位器的唯一性 / 可交互性校验（不触网 / 无浏览器）。

回归的缺陷（审计复现）：`selector_exists` 只判 `count() > 0`。两个同名按钮时启发式产出的
`text="提交"`（0.95）会被当成"可用的修复"采纳 → 引擎重试 `click()` 抛 Playwright
strict-mode violation（**不是 TimeoutError**，不触发自愈，直接冒泡）→ 且该脏案例会经
stage → commit 落库，供后续 L1/L3 复用（一次假修复变成长期污染源）。

修复口径：
- `selector_exists` = **唯一命中**（count()==1）——strict-mode 硬底线（候选采纳 / 知识复用 /
  verified 判定共用）；
- `selector_actionable` = 唯一命中 + 可见 + enabled——**择优信号**，不淘汰候选：它取决于动作
  （`set_input_files` 对隐藏 input 同样可用）且 Playwright 会自动等待，硬拒会误杀
  "暂不可见但稍后可见"与隐藏上传框（验证者用真 Chrome 复现过该回归）。
"""

from __future__ import annotations

import pytest

import selfheal.agent.orchestrator as orch_mod
from selfheal.agent.context import (
    HealingContext,
    action_requires_actionability,
    selector_actionable,
    selector_exists,
)
from selfheal.agent.diagnose import Diagnoser
from selfheal.agent.fix_generator import FixGenerator
from selfheal.agent.persistence import PersistenceHandler
from selfheal.agent.strategies.base import RepairCandidate
from selfheal.collect.collector import Scene
from selfheal.config import Settings
from selfheal.contracts import HealOutcome
from selfheal.knowledge.schema import RepairCase
from selfheal.knowledge.store import KnowledgeStore
from selfheal.reporting.hooks import HealingReporter

pytestmark = pytest.mark.unit

_AMBIGUOUS_SELECTOR = 'text="提交"'
_DUPLICATE_BUTTONS_DOM = "<html><body><button>提交</button><button>提交</button></body></html>"


class _Loc:
    """最小 locator 替身：可配命中数 / 可见性 / enabled；也支持"探测即异常"的页面故障。"""

    def __init__(
        self,
        count: int = 1,
        *,
        visible: bool = True,
        enabled: bool = True,
        count_raises: bool = False,
        interaction_raises: bool = False,
    ):
        self._count = count
        self._visible = visible
        self._enabled = enabled
        self._count_raises = count_raises
        self._interaction_raises = interaction_raises
        self.interaction_probed = False

    def count(self) -> int:
        if self._count_raises:
            raise RuntimeError("页面已关闭")
        return self._count

    @property
    def first(self):
        return self

    def is_visible(self) -> bool:
        self.interaction_probed = True
        if self._interaction_raises:
            raise AssertionError(
                "多命中时不得调用 is_visible（Playwright 会抛 strict-mode violation）"
            )
        return self._visible

    def is_enabled(self) -> bool:
        if self._interaction_raises:
            raise RuntimeError("元素已脱离文档")
        return self._enabled


class _CountOnlyLoc:
    """只实现 count() 的简易替身（无浏览器单测的既有形态：无 is_visible / is_enabled）。"""

    def __init__(self, count: int):
        self._count = count

    def count(self) -> int:
        return self._count


class _Page:
    """只读页面替身：按 selector 返回预置 locator，未预置的 selector 视为不存在。"""

    def __init__(self, locators: dict[str, object] | None = None):
        self._locators = locators or {}

    def locator(self, selector: str, **_kwargs):
        return self._locators.get(selector, _Loc(count=0))


def _ctx(
    *, description: str = "提交", original_selector: str = "#submit-old", dom: str | None = None
) -> HealingContext:
    return HealingContext(
        scene=Scene(url="demo://page", dom_snapshot=dom or _DUPLICATE_BUTTONS_DOM),
        original_selector=original_selector,
        description=description,
        dom_fingerprint=None,
        page_fingerprint="",
        element_context=None,
    )


def _fix_gen(
    settings: Settings, *, page=None, knowledge: KnowledgeStore | None = None
) -> FixGenerator:
    return FixGenerator(
        settings=settings,
        knowledge=knowledge if knowledge is not None else KnowledgeStore(),
        reporter=HealingReporter(),
        rule_diagnoser=Diagnoser(),
        llm_diagnoser=None,
        llm_client=None,
        vision_client=None,
        embedding=None,
        page=page,
        strategy_registry=dict(orch_mod._STRATEGY_REGISTRY),
    )


# --- selector_exists：唯一命中（硬底线） ---


def test_selector_not_unique_when_missing():
    assert selector_exists(_Page(), "#nope") is False


def test_selector_not_unique_when_ambiguous():
    """核心回归：两个同名按钮 → `text="提交"` 命中 2 个 → 不可用于动作。"""
    page = _Page({_AMBIGUOUS_SELECTOR: _Loc(count=2)})
    assert selector_exists(page, _AMBIGUOUS_SELECTOR) is False


def test_selector_unique_visible_and_enabled():
    assert selector_exists(_Page({"#submit": _Loc(count=1)}), "#submit") is True


def test_selector_uniqueness_does_not_depend_on_visibility():
    """唯一性只管 strict mode：隐藏 / disabled 的元素仍是"唯一"，是否可交互另行判定。"""
    assert selector_exists(_Page({"#file": _Loc(count=1, visible=False)}), "#file") is True
    assert selector_exists(_Page({"#file": _Loc(count=1, enabled=False)}), "#file") is True


def test_selector_not_unique_when_page_probe_fails():
    """页面已关闭 / 定位器解析失败：失败安全地判为不可用（不阻断自愈主流程）。"""
    assert selector_exists(_Page({"#submit": _Loc(count_raises=True)}), "#submit") is False


def test_selector_unique_without_page():
    """无页面（纯逻辑单测 / 知识查询）→ 视为唯一可用（既有契约）。"""
    assert selector_exists(None, "#anything") is True


def test_count_only_stub_checks_uniqueness_only():
    """简易替身无 is_visible/is_enabled 不影响唯一性判断。"""
    assert selector_exists(_Page({"#submit": _CountOnlyLoc(1)}), "#submit") is True
    assert selector_exists(_Page({"#submit": _CountOnlyLoc(2)}), "#submit") is False


# --- selector_actionable：可交互（择优信号，不淘汰） ---


def test_actionable_requires_visible_and_enabled():
    assert selector_actionable(_Page({"#submit": _Loc(count=1)}), "#submit") is True
    assert selector_actionable(_Page({"#submit": _Loc(count=1, visible=False)}), "#submit") is False
    assert selector_actionable(_Page({"#submit": _Loc(count=1, enabled=False)}), "#submit") is False


def test_actionable_false_when_ambiguous_and_skips_interaction_probe():
    """多命中时不得调用 is_visible（Playwright 对多命中同样抛 strict-mode violation）。"""
    locator = _Loc(count=2, interaction_raises=True)
    page = _Page({_AMBIGUOUS_SELECTOR: locator})
    assert selector_actionable(page, _AMBIGUOUS_SELECTOR) is False
    assert locator.interaction_probed is False


def test_actionable_probe_failure_is_false():
    assert (
        selector_actionable(_Page({"#submit": _Loc(interaction_raises=True)}), "#submit") is False
    )


def test_actionable_without_page_or_probes_defaults_true():
    assert selector_actionable(None, "#anything") is True
    assert selector_actionable(_Page({"#submit": _CountOnlyLoc(1)}), "#submit") is True


def test_action_requires_actionability_is_action_aware():
    """`set_input_files` 对隐藏 input 可用（标准上传写法）→ 不要求可交互；其余动作要求。"""
    assert action_requires_actionability("set_input_files") is False
    for action in ("click", "fill", "check", "press", "hover", "set_input_files_v2"):
        assert action_requires_actionability(action) is True
    assert action_requires_actionability(None) is False  # 未知来源按既有行为处理


# --- 候选采纳：非唯一不得进入修复链路（否则重试 strict-mode 崩溃） ---


def test_ambiguous_heuristic_candidate_is_rejected_before_adoption(monkeypatch):
    """回归：同名按钮场景不再采纳 `text="提交"`，避免 strict-mode 崩溃与脏案例落库。"""
    monkeypatch.setattr("selfheal.reporting.fix_proposals.write_fix_proposal", lambda **_: None)
    settings = Settings()
    page = _Page({_AMBIGUOUS_SELECTOR: _Loc(count=2)})
    gen = _fix_gen(settings, page=page)
    context = _ctx(description="提交")

    proposal = gen.generate(context)

    assert proposal.best is None, "非唯一定位器不得被采纳"
    assert proposal.rejected is not None
    assert proposal.rejected.selector == _AMBIGUOUS_SELECTOR

    outcome = PersistenceHandler(
        settings, KnowledgeStore(), HealingReporter(), embedding=None, page=page
    ).resolve(context, proposal)
    assert outcome.success is False
    assert outcome.new_selector is None  # 没有"新定位器"交给引擎重试 → 不会 strict-mode 崩溃


def test_unique_heuristic_candidate_is_still_adopted():
    """对照：唯一命中时启发式候选照常采纳（修复不误伤正常路径）。"""
    page = _Page({_AMBIGUOUS_SELECTOR: _Loc(count=1)})
    gen = _fix_gen(Settings(), page=page)

    best = gen.best_candidate(_ctx(description="提交"))

    assert best is not None
    assert (best.selector, best.strategy) == (_AMBIGUOUS_SELECTOR, "heuristic")


def test_hidden_file_input_candidate_is_adopted_for_set_input_files():
    """回归（验证者用真 Chrome 复现）：隐藏 `<input type=file>` 是标准上传写法，不得被拒。"""
    page = _Page({"#file": _Loc(count=1, visible=False)})
    gen = _fix_gen(Settings(), page=page)
    context = _ctx(description="上传附件", original_selector="#file-old", dom="<html></html>")

    # 直接注入一个固定候选策略，聚焦"可交互性按动作取舍"而非启发式打分
    gen._strategy_registry = {"heuristic": _fixed_strategy("#file", 0.95, "heuristic")}

    best = gen.best_candidate(context, action="set_input_files")

    assert best is not None and best.selector == "#file"


def test_hidden_candidate_is_still_adopted_for_click_when_nothing_better():
    """隐藏候选对 click 不淘汰：Playwright 会等待 actionable，放弃候选会白白丢掉修复机会。"""
    page = _Page({"#late": _Loc(count=1, visible=False)})
    gen = _fix_gen(Settings(), page=page)
    gen._strategy_registry = {"heuristic": _fixed_strategy("#late", 0.95, "heuristic")}

    best = gen.best_candidate(_ctx(dom="<html></html>"), action="click")

    assert best is not None and best.selector == "#late"


def test_confidence_wins_over_actionability_for_click():
    """置信度优先：不拿"当前可交互"压过高置信候选（可交互诱饵点错代价更高，D6 宁可失败）。"""
    page = _Page({"#hidden": _Loc(count=1, visible=False), "#visible": _Loc(count=1)})
    settings = Settings()
    settings.healing.strategy_order = ["heuristic", "visual"]
    gen = _fix_gen(settings, page=page)
    gen._strategy_registry = {
        "heuristic": _fixed_strategy("#hidden", 0.95, "heuristic"),
        "visual": _fixed_strategy("#visible", 0.70, "visual"),
    }

    best = gen.best_candidate(_ctx(dom="<html></html>"), action="click")

    assert best is not None and best.selector == "#hidden"


def test_actionable_wins_tie_break_for_click():
    """同分时才让可交互者胜出（确定性 tie-break）。"""
    page = _Page({"#hidden": _Loc(count=1, visible=False), "#visible": _Loc(count=1)})
    settings = Settings()
    settings.healing.strategy_order = ["heuristic", "visual"]
    gen = _fix_gen(settings, page=page)
    gen._strategy_registry = {
        "heuristic": _fixed_strategy("#hidden", 0.70, "heuristic"),
        "visual": _fixed_strategy("#visible", 0.70, "visual"),
    }

    best = gen.best_candidate(_ctx(dom="<html></html>"), action="click")

    assert best is not None and best.selector == "#visible"


def test_unknown_action_behaves_like_legacy():
    """未知动作（脚本直调 run() 未传 action）→ 不探测可交互性，按既有行为取值。"""
    page = _Page({"#hidden": _Loc(count=1, visible=False)})
    gen = _fix_gen(Settings(), page=page)
    gen._strategy_registry = {"heuristic": _fixed_strategy("#hidden", 0.95, "heuristic")}

    best = gen.best_candidate(_ctx(dom="<html></html>"), action=None)

    assert best is not None and best.selector == "#hidden"


def _fixed_strategy(selector: str, confidence: float, strategy: str):
    """固定产出候选的策略类（`_build_strategy` 对未知策略类无参实例化）。"""

    class _FixedStrategy:
        name = strategy

        def repair(self, scene, original_selector, description=None):
            return RepairCandidate(selector=selector, confidence=confidence, strategy=strategy)

    return _FixedStrategy


# --- 知识复用：只接受仍唯一命中的缓存定位器 ---


def _store_with_cached_case(new_selector: str, confidence: float = 0.95) -> KnowledgeStore:
    store = KnowledgeStore()
    store.add_repair(
        RepairCase(
            original_selector="#submit-old",
            new_selector=new_selector,
            strategy="heuristic",
            confidence=confidence,
            page_url="demo://page",
        )
    )
    return store


def test_knowledge_reuse_rejects_ambiguous_cached_selector():
    """缓存的新定位器变成多命中 → 不复用（否则把历史脏案例继续传播）。"""
    store = _store_with_cached_case(_AMBIGUOUS_SELECTOR)
    gen = _fix_gen(Settings(), page=_Page({_AMBIGUOUS_SELECTOR: _Loc(count=2)}), knowledge=store)

    assert gen.lookup_knowledge(_ctx()) is None


def test_knowledge_reuse_accepts_unique_cached_selector():
    store = _store_with_cached_case(_AMBIGUOUS_SELECTOR)
    gen = _fix_gen(Settings(), page=_Page({_AMBIGUOUS_SELECTOR: _Loc(count=1)}), knowledge=store)

    outcome = gen.lookup_knowledge(_ctx())

    assert outcome is not None
    assert (outcome.strategy, outcome.new_selector) == ("knowledge", _AMBIGUOUS_SELECTOR)


def test_knowledge_reuse_accepts_hidden_file_input_selector():
    """隐藏上传框的缓存定位器仍可复用（唯一即可；可见性由动作与自动等待决定）。"""
    store = _store_with_cached_case("#file")
    gen = _fix_gen(Settings(), page=_Page({"#file": _Loc(count=1, visible=False)}), knowledge=store)

    outcome = gen.lookup_knowledge(_ctx())

    assert outcome is not None and outcome.new_selector == "#file"


def test_knowledge_reuse_accepts_disabled_unique_selector():
    """disabled 是瞬态，唯一命中的缓存定位器仍可复用（重试前 Playwright 会自动等待）。"""
    store = _store_with_cached_case("#btn")
    gen = _fix_gen(Settings(), page=_Page({"#btn": _Loc(count=1, enabled=False)}), knowledge=store)

    outcome = gen.lookup_knowledge(_ctx())

    assert outcome is not None and outcome.new_selector == "#btn"


# --- verified 判定：原定位器"结构性不可用"才算真修复（flaky 口径不注水） ---


def _outcome() -> HealOutcome:
    return HealOutcome(
        success=True, new_selector="#new-submit", confidence=0.9, strategy="heuristic"
    )


def _verified(page) -> bool:
    reporter = HealingReporter()
    persister = PersistenceHandler(
        Settings(), KnowledgeStore(), reporter, embedding=None, page=page
    )
    persister._record("#submit-old", _outcome())
    return reporter.records[-1].verified


def test_verified_true_when_original_selector_never_recovered():
    assert _verified(_Page()) is True


def test_verified_false_when_original_selector_recovered():
    """原定位器已恢复 → 失败只是瞬时（flaky），不算真自愈。"""
    assert _verified(_Page({"#submit-old": _Loc(count=1)})) is False


def test_verified_false_when_original_selector_only_transiently_uninteractable():
    """隐藏 / disabled 是瞬态（如提交后按钮置灰），不构成"真修复"证据。"""
    assert _verified(_Page({"#submit-old": _Loc(count=1, visible=False)})) is False
    assert _verified(_Page({"#submit-old": _Loc(count=1, enabled=False)})) is False


def test_verified_true_when_original_selector_still_ambiguous():
    """原定位器多命中同样不可用（重试会 strict-mode 失败）→ 计为真修复。"""
    assert _verified(_Page({"#submit-old": _Loc(count=2)})) is True
