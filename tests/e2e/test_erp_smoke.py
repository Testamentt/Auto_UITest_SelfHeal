"""端到端测试：管伊佳 ERP 被测系统（T23 迁移，marker erp；需本地 ERP 环境）。

P1 验收：登录流打通（HealingPage + UI 登录态 + 主框架断言）。
前置：.env 配 ERP_UI_USERNAME/ERP_UI_PASSWORD；ERP 前端 localhost:3001 可访问；
验证码已关闭（用户确认）。
"""

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.erp]


def test_erp_login_smoke(erp_page, settings):
    """登录冒烟：UI 登录进入 ERP 主框架（dashboard），自愈插件随 HealingPage 生效。"""
    assert erp_page.healing_enabled is True  # 自愈插件注入被测系统
    assert "/dashboard" in erp_page.url  # 登录成功跳转主框架
