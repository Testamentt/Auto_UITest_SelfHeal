"""集成测试：T5 按策略阈值端到端生效（需浏览器内核，-m e2e）。

验证配置 `healing.strategy_thresholds` 会真实改变端到端行为：
1. heuristic 阈值抬高（0.99 > 演示页 heuristic 实际命中的 ~0.95）→ 修复被拒 →
   D6 兜底（use_fallback）接管，操作经人工备用定位器完成（自愈拒绝不再悄悄采纳）。
2. 同配置且无 fallback → HealingFailedError（阈值拦截 = 硬性路由，非装饰性）。
"""

import pytest

from selfheal.config import Settings
from selfheal.engine.healing_locator import HealingFailedError, HealingPage
from selfheal.knowledge.store import KnowledgeStore
from tests.e2e.pages.demo_page import DemoPage

pytestmark = pytest.mark.e2e


def _strict_settings() -> Settings:
    """heuristic 采纳阈值抬到 0.99（高于演示页登录按钮的规则分数 ~0.95）→ 拒绝。"""
    s = Settings()
    s.healing.strategy_thresholds = {"heuristic": 0.99}
    return s


@pytest.fixture
def strict_page(context):
    """注入严格阈值的自愈页面（独立内存知识库，不污染会话共享库）。"""
    page = HealingPage(context.new_page(), _strict_settings(), knowledge=KnowledgeStore())
    yield page
    page.close()


def test_threshold_rejects_heuristic_and_falls_back(strict_page):
    """heuristic 0.95 < 策略阈值 0.99 → 拒绝采纳 → use_fallback 用 #submit-btn-v2 完成操作。"""
    demo = DemoPage(strict_page)
    demo.open()
    demo.login()
    assert demo.result() == "logged-in"  # 兜底路径完成（非自愈采纳）


def test_threshold_blocks_healing_without_fallback(strict_page):
    """同阈值且无备用定位器 → 自愈被策略阈值拦截，抛 HealingFailedError（硬性路由）。"""
    strict_page.set_default_timeout(800)  # 缩短等待，避免用例长时间阻塞
    demo = DemoPage(strict_page)
    demo.open()
    demo.locator('[data-testid="username"]', description="用户名输入框").fill("tester")
    demo.locator('[data-testid="password"]', description="密码输入框").fill("secret")
    with pytest.raises(HealingFailedError):
        demo.locator("#submit-btn-old", description="登录按钮").click()
