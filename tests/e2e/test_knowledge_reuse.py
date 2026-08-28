"""端到端测试：T7 知识二次命中（坐实"越用越聪明"，需浏览器内核，-m e2e）。

场景 A（知识复用）：第一次自愈成功 → 案例落库（B1 commit）→ 同场景第二次操作
直接命中知识缓存复用（strategy=="knowledge"、根因 cached/cached_l1），不再走策略链重修。

场景 B（缓存失效重修，T4 延伸）：修复沉淀后页面再次改版使缓存 selector 失效 →
缓存选择器验证拒绝（selector_exists）→ 转策略重修成功——知识库不是"永久正确答案"。

两个场景均使用**独立内存知识库**（fresh_knowledge_page），各自自证完整链路，
不受 session 级共享知识库（其他用例）的初始状态影响。
"""

import pytest

from selfheal.engine.healing_locator import HealingPage
from selfheal.knowledge.store import KnowledgeStore
from tests.e2e.pages.demo_page import DemoPage

pytestmark = pytest.mark.e2e


@pytest.fixture
def fresh_knowledge_page(context, settings):
    """独立内存知识库 + 自愈页面（场景内闭环，无跨用例知识串扰）。"""
    kb = KnowledgeStore()
    page = HealingPage(context.new_page(), settings, knowledge=kb)
    yield page, kb
    page.close()


def test_knowledge_reuse_second_hit(fresh_knowledge_page):
    """场景 A：第二次同场景操作直接复用知识缓存（strategy=="knowledge"）。"""
    healing_page, knowledge = fresh_knowledge_page
    demo = DemoPage(healing_page)
    # 第一次：启发式自愈成功，案例沉淀进知识库
    demo.open()
    demo.login()
    assert demo.result() == "logged-in"
    first = healing_page.reporter.records[-1]
    assert first.success and first.strategy == "heuristic"
    assert knowledge.count_repairs() >= 1  # B1：重试验证成功后已落库

    # 同场景第二次：页面回到相同改版状态 → 知识缓存直接命中复用（不再走策略链）
    demo.open()
    demo.login()
    assert demo.result() == "logged-in"
    second = healing_page.reporter.records[-1]
    assert second.success and second.strategy == "knowledge"
    assert second.root_cause in ("cached", "cached_l1")


def test_stale_cache_retries_strategies(fresh_knowledge_page):
    """场景 B：沉淀后页面再次改版使缓存 selector 失效 → 缓存验证拒绝 → 策略重修成功。"""
    healing_page, knowledge = fresh_knowledge_page
    demo = DemoPage(healing_page)
    demo.open()
    demo.login()
    assert demo.result() == "logged-in"
    assert healing_page.reporter.records[-1].strategy == "heuristic"
    assert knowledge.count_repairs() >= 1

    # 页面再次改版：缓存的选择器（data-testid="submit-btn"）作废；aria-label 保留供启发式重识别
    healing_page.evaluate(
        """() => {
            const btn = document.querySelector('#submit-btn-v2');
            btn.id = 'submit-btn-v3';
            btn.dataset.testid = 'submit-btn-v3';
        }"""
    )

    # 第二次操作：缓存命中但 selector 校验失败（T4 缓存验证）→ 转策略重修
    demo.login()
    assert demo.result() == "logged-in"
    second = healing_page.reporter.records[-1]
    assert second.success
    assert second.strategy != "knowledge"  # 缓存被拒绝，未复用
    assert second.strategy == "heuristic"  # 启发式按 aria-label 重新识别改版后的按钮
