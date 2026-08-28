"""端到端测试：智能等待（wait_until_stable + T6 动作前置等待，需浏览器内核，-m e2e）。

演示页的 moving-btn 在加载后 ~30ms 移动一次再稳定；wait_until_stable 应等到
其移动完成并稳定后才返回，返回时元素已处于最终位置。
T6：开启 healing.action_wait 后，动作（click）执行前自动做短稳等待，
无需 POM 显式调用即可兜住加载抖动（默认关闭=保持既有行为）。
"""

import pytest

from selfheal.config import Settings
from selfheal.engine.healing_locator import HealingPage
from selfheal.knowledge.store import KnowledgeStore
from tests.e2e.pages.popup_page import PopupPage

pytestmark = pytest.mark.e2e


def test_wait_until_stable_on_moving_element(healing_page):
    demo = PopupPage(healing_page)
    demo.open()
    loc = healing_page.locator('[data-testid="moving-btn"]', description="移动按钮")
    loc.wait_until_stable(timeout_ms=5000, stable_ms=300, poll_ms=50)
    # 元素已移动并稳定：最终 y 应已下移（margin-top 60px + body margin）
    assert loc.bounding_box()["y"] >= 60


def _action_wait_settings() -> Settings:
    """开启动作前置智能等待的配置（短稳等待参数与显式调用一致）。"""
    s = Settings()
    s.healing.action_wait.enabled = True
    s.healing.action_wait.timeout_ms = 5000
    s.healing.action_wait.stable_ms = 300
    return s


@pytest.fixture
def action_wait_page(context):
    """注入开启 action_wait 的自愈页面（独立内存知识库，不污染会话共享库）。"""
    page = HealingPage(context.new_page(), _action_wait_settings(), knowledge=KnowledgeStore())
    yield page
    page.close()


def test_action_wait_completes_click_on_moving_element(action_wait_page):
    """开启后：直接 click（无显式 wait）也能兜住 30ms 加载抖动并成功点击。"""
    demo = PopupPage(action_wait_page)
    demo.open()
    loc = action_wait_page.locator('[data-testid="moving-btn"]', description="移动按钮")
    loc.click()
    # 点击发生在动作前置等待之后：元素已处于最终位置（未在抖动中误操作）
    assert loc.bounding_box()["y"] >= 60


def test_action_wait_does_not_break_healing_flow(action_wait_page):
    """开启后：既有自愈流程（失效定位器→自愈→重试）不受前置等待影响，照常跑通。"""
    from tests.e2e.pages.demo_page import DemoPage

    demo = DemoPage(action_wait_page)
    demo.open()
    demo.login()  # 内部 fill×2 + 失效定位器 click（走自愈）
    assert demo.result() == "logged-in"
