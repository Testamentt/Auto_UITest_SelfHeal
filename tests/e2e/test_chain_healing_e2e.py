"""端到端测试：链式定位自愈（M15-3，需浏览器内核，-m e2e）。

补 `tests/unit/test_chain_healing.py` 的缺口：单测用自写 fake 验证链记录 / 重放 / 二次修叶子的
**逻辑**；本文件在真实 Playwright 页面上验证同一语义端到端成立（RULE.md R2 要求单测 + 集成双覆盖）。

演示页构造（见 `pages/demo_page.html`）：外层 `#panel` 是稳定"根"（容器，页面里真实存在），
内层 `#inner-btn-old` 是**已失效**的旧 id（页面里不存在），真实按钮为 `#inner-btn-v2`（文本"面板按钮"）。
`HealingLocator` 的链语义（`engine/healing_locator.py`）：
1. 第一次自愈修**根**（`#panel`）并重放整条链 —— 根未坏时必然回原根、重放仍在叶子处超时；
2. 重放仍超时 → 第二次自愈修**叶子**（`#inner-btn-old`，跳过知识缓存），命中 `#inner-btn-v2`。
因此"链式点击最终成功"这件事本身，必须经由第二次（叶子）自愈才能发生。

description 只能挂在 `HealingPage.locator()`（根）上：链节点 `HealingLocator.locator()` 不解析该
自愈增强参数，会把 `description=` 原样透传给原生 `Locator.locator()` 并抛 `TypeError`；
根描述会随链自动传播到每个节点（`_chain` 复制 `self._description`），叶子自愈因此也拿得到意图。

模型调用说明（R4 不静默）：两个场景都在**根**上挂描述"面板按钮"，使两跳都由启发式收官——
修根：描述与容器文本"面板"互为子串 → 置信度 0.95 ≥ 早接受阈值 0.85 → 早短路，不走语义/视觉；
修叶：描述与按钮文本完全相等 → 置信度 1.0。故本文件**不外呼真实模型**，用例带护栏断言
`llm_calls`/`vlm_calls` 为 0（一旦退化为模型驱动即失败，避免变成"模型心情"抖动）。

反面记录（实测）：若不给根描述（走 auto_description），修根那一步启发式分数不足会继续落到
语义/视觉策略，结果依模型与知识库状态而变——同一天两次运行分别出现「语义策略修根成功」与
「视觉策略给出 0.27 < 0.6 被拒 → 整个根修复失败」，后者让链式点击直接抛 HealingFailedError。
故断言锚定"最终结果正确 + 记录产生"，且以显式描述保证确定性，不把用例交给模型心情。
"""

import pytest
from playwright.sync_api import TimeoutError as PWTimeoutError

from tests.e2e.pages.demo_page import DemoPage

pytestmark = [pytest.mark.e2e, pytest.mark.healing]

# 演示页链式夹具约定的 id（改 HTML 请同步此处）
CHAIN_ROOT = "#panel"
CHAIN_LEAF_STALE = "#inner-btn-old"  # 已失效的旧 id：页面里不存在
CHAIN_LEAF_REAL = "#inner-btn-v2"  # 改版后的真实按钮
CHAIN_LEAF_TEXT = "面板按钮"  # 稳定文本：启发式重识别的强信号


def _chain(healing_page):
    """构造"根存在、叶子失效"的链（description 只能挂根：链节点不接受该参数）。

    与 POM 内 `locator(...).locator(...)` 同构；根描述随链传播到叶子（`_chain` 复制 `self._description`），
    供两次自愈都拿到"面板按钮"这一意图（描述的是**最终目标元素**，不是容器）。
    """
    return healing_page.locator(CHAIN_ROOT, description=CHAIN_LEAF_TEXT).locator(CHAIN_LEAF_STALE)


def _records_with(page, selector: str) -> list:
    """自愈记录中 original_selector 命中的条目（记录只在修复成功时产生，见 PersistenceHandler）。"""
    return [r for r in page.reporter.records if r.original_selector == selector]


def _assert_no_model_calls(page) -> None:
    """确定性护栏：本文件两跳均由启发式收官，不应外呼真实模型（外呼即前提被破坏）。"""
    stats = page.reporter.stats
    assert stats.get("llm_calls", 0) == 0, f"意外外呼 LLM：stats={stats}"
    assert stats.get("vlm_calls", 0) == 0, f"意外外呼 VLM：stats={stats}"


def test_chain_replay_click_heals_leaf(healing_page):
    """场景 A（链式重放）：稳定根 + 失效叶子组成链，点击后 `#result` 被置为 panel-clicked。

    演示页对该按钮的监听把 `#result` 设为 "panel-clicked"，故结果值即"真实点击生效"的证据。
    """
    demo = DemoPage(healing_page)
    demo.open()

    # 根(#panel，存在) → 叶子(#inner-btn-old，不存在) → 动作（first 是纯定位链环，只进重放）
    _chain(healing_page).first.click()

    assert demo.result() == "panel-clicked"
    # 自愈记录：以**失效叶子**为原定位器（若第一次修根就被采纳，记录里会是 #panel）
    leaf_records = _records_with(healing_page, CHAIN_LEAF_STALE)
    assert leaf_records, f"未产生以 {CHAIN_LEAF_STALE} 为原定位器的自愈记录"
    assert all(r.success is True for r in leaf_records)
    _assert_no_model_calls(healing_page)


def test_second_heal_fixes_broken_leaf(healing_page):
    """场景 B（二次自愈修叶子，M15 核心）：根 ok、叶子失效 → 第一次修根重放必失败。

    断言链在最终结果上成立，并证明**赢下这次动作的是叶子修复**：
    记录里的 original_selector 是叶子 selector（而非根 `#panel`），且带生效策略名。
    """
    demo = DemoPage(healing_page)
    demo.open()

    _chain(healing_page).click()

    assert demo.result() == "panel-clicked"
    leaf_records = _records_with(healing_page, CHAIN_LEAF_STALE)
    assert leaf_records, "未产生叶子自愈记录（说明没有走第二次自愈修叶子）"
    winner = leaf_records[-1]
    assert winner.success is True
    assert winner.strategy  # 生效策略名非空（实测为 heuristic；不锁死具体值，留策略演进空间）
    # 修复后的定位器确实落在改版后的真实按钮上，且页面里只有这一个"面板按钮"
    assert winner.new_selector == CHAIN_LEAF_REAL
    assert healing_page.locator(winner.new_selector).count() == 1
    _assert_no_model_calls(healing_page)


def test_disabled_chain_stays_native(disabled_page):
    """场景 C（关闭自愈）：链式调用完全原生——失效叶子直接抛 Playwright 超时，零修复。"""
    disabled_page.set_default_timeout(800)  # 缩短等待，避免用例长时间阻塞
    demo = DemoPage(disabled_page)
    demo.open()

    with pytest.raises(PWTimeoutError):
        _chain(disabled_page).click()
    assert demo.result() != "panel-clicked"
