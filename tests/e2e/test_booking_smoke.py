# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""端到端测试：票多星票务平台冒烟（marker booking；需本地票多星环境）。

覆盖：管理端登录流（`/login` → `/` dashboard「数据概览」）与客户端商城登录流
（`/shop/login` → `/shop`）。两条用例共用 conftest 提供的已登录 HealingPage，
断言自愈插件随页面注入生效 + UI 登录态成立。

前置：`.env` 配 `BOOKING_ADMIN_USERNAME` / `BOOKING_ADMIN_PASSWORD` /
`BOOKING_CUSTOMER_PASSWORD`；前端 3001、后端 8088 可访问（缺失/不可达时 fixture skip）。
"""

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.booking]


def test_booking_admin_login_smoke(booking_admin_page, settings):
    """管理端登录冒烟：HealingPage 注入的后台页面处于已登录 dashboard。"""
    from tests.e2e.pages.booking.admin_home_page import BookingAdminHomePage

    assert booking_admin_page.healing_enabled is True
    home = BookingAdminHomePage(booking_admin_page, settings.sut.base_url)
    assert home.is_logged_in(), f"管理端未进入 dashboard（url={booking_admin_page.url}）"


def test_booking_shop_login_smoke(booking_shop_page, settings):
    """商城登录冒烟：客户会话写入 localStorage，页头展示当前客户昵称。"""
    from tests.e2e.pages.booking.shop_page import BookingShopPage

    assert booking_shop_page.healing_enabled is True
    shop = BookingShopPage(booking_shop_page, settings.sut.base_url)
    assert shop.is_logged_in(settings.sut.customer_username), (
        f"商城未进入已登录态（url={booking_shop_page.url}）"
    )
