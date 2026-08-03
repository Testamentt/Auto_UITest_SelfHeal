"""集成 / 端到端测试：自愈闭环三场景（需浏览器内核，-m e2e）。

三场景对应 RULE.md / roadmap.md 的验收点：
1. 自愈成功：失效定位器经 HealingPage 自动修复并完成操作。
2. 兜底触发：无法自愈时退回人工备用定位器（D6）。
3. 关闭=原生：禁用自愈后，失效定位器直接抛超时（验证插件可开关、不影响框架）。
"""

import pytest

from selfheal.engine.healing_locator import HealingPage
from tests.e2e.pages.demo_page import DemoPage

pytestmark = pytest.mark.e2e


def test_heal_success(healing_page):
    assert isinstance(healing_page, HealingPage)
    assert healing_page.healing_enabled is True

    demo = DemoPage(healing_page)
    demo.open()
    demo.login()  # 内部使用失效定位器 #submit-btn-old

    assert demo.result() == "logged-in"
    # 产生并记录了至少一次自愈
    assert len(healing_page.reporter.records) >= 1
    rec = healing_page.reporter.records[0]
    assert rec.success and rec.original_selector == "#submit-btn-old"


def test_fallback_when_unhealable(healing_page):
    demo = DemoPage(healing_page)
    demo.open()
    demo.click_secondary_via_fallback()  # 描述故意不匹配，无法自愈
    assert demo.result() == "secondary"  # 由人工备用定位器 #real-secondary 完成


def test_disabled_behaves_native(disabled_page):
    from playwright.sync_api import TimeoutError as PWTimeoutError

    assert disabled_page.healing_enabled is False
    disabled_page.set_default_timeout(800)  # 缩短等待，避免用例长时间阻塞

    demo = DemoPage(disabled_page)
    demo.open()
    with pytest.raises(PWTimeoutError):
        demo.login()  # 关闭自愈：失效定位器无人修复 → 超时
    assert demo.result() != "logged-in"
