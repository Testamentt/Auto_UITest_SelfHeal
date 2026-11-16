# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""端到端测试：票多星票务平台自愈场景（marker booking；需本地票多星环境）。

核心演示：**真实被测系统上的登录页改版 → 自愈重定位 → 登录成功**。

场景步骤（管理端 / 商城各一条）：
1. 打开登录页，填账号密码（**改版前基线**：旧 data-testid 正常工作，表单可用）；
2. **前端改版注入**：登录按钮的 `data-testid` 被改名（模拟前端升级），React 重渲染
   可能写回，故注入用 MutationObserver 持续改名；按钮仍保留可见文本「登录」；
3. 改版**后**仍用旧 data-testid 提交 → 定位器失效 → 自愈凭 description
   「登录按钮」在候选文本上强命中（containment 分支），重定位到改名后的按钮；
4. 点击成功 → 登录进入 dashboard / 商城，且自愈 reporter 产生成功记录。

**为什么改版目标是按钮而不是输入框**：React 受控 input 的可见文本为空，placeholder
与中文描述不构成词元/子串关系，启发式只能给出约 0.32 的弱置信度（低于默认阈值
0.6）；而模型语义/视觉兜底依赖外部模型可用，不能作为可复现用例的前提。按钮的
可见文本「登录」与 description 互为子串，启发式强信号约 0.95，零模型调用、确定性
可复现。用例同时断言本次成功记录的策略为 `heuristic`，把"零模型依赖"锁死。

商城用例的客户账号由 `booking_api.ensure_customer()` 先备好（首次注册，之后复用）。
"""

import os

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.booking]


def _heuristic_success_records(page) -> list:
    """本次自愈里「启发式策略且成功」的记录。"""
    return [r for r in page.reporter.records if r.success and r.strategy == "heuristic"]


def test_booking_admin_login_heal(booking_healing_page, settings):
    """管理端登录页：登录按钮 data-testid 改版 → 自愈重定位 → 登录进入 dashboard。"""
    from tests.e2e.pages.booking.admin_home_page import BookingAdminHomePage
    from tests.e2e.pages.booking.admin_login_page import BookingAdminLoginPage

    sut = settings.sut
    username = os.getenv(sut.admin_username_env, "")
    password = os.getenv(sut.admin_password_env, "")
    if not username or not password:
        pytest.skip(f"票多星管理员凭证未配置（{sut.admin_username_env}/{sut.admin_password_env}）")

    page = booking_healing_page
    login = BookingAdminLoginPage(page, sut.base_url).open_login()

    # 改版前：旧 data-testid 正常工作（基线，证明表单可用）
    login.fill_username(username)
    login.fill_password(password)

    # —— 前端改版注入：登录按钮 data-testid 由 admin-login-submit 改为 admin-login-submit-v2 ——
    login.inject_submit_testid()

    # 改版后：旧定位器失效 → 自愈按 description 语义重定位按钮 → 登录（演示核心）
    login.submit()

    assert BookingAdminHomePage(page, sut.base_url).is_logged_in(), (
        f"管理端自愈登录未成功（url={page.url}）"
    )
    successes = _heuristic_success_records(page)
    assert successes, f"未产生启发式自愈成功记录：{page.reporter.records}"


def test_booking_shop_login_heal(booking_healing_page, booking_api, settings):
    """商城登录页：登录按钮 data-testid 改版 → 自愈重定位 → 登录进入已登录商城。"""
    from tests.e2e.pages.booking.shop_login_page import BookingShopLoginPage
    from tests.e2e.pages.booking.shop_page import BookingShopPage

    sut = settings.sut
    customer_password = os.getenv(sut.customer_password_env, "")
    if not customer_password:
        pytest.skip(f"票多星客户密码未配置（{sut.customer_password_env}）")
    booking_api.ensure_customer(sut.customer_username, customer_password)

    page = booking_healing_page
    login = BookingShopLoginPage(page, sut.base_url).open_login()

    # 改版前：旧 data-testid 正常工作（基线，证明表单可用）
    login.fill_username(sut.customer_username)
    login.fill_password(customer_password)

    # —— 前端改版注入：登录按钮 data-testid 由 shop-login-submit 改为 shop-login-submit-v2 ——
    login.inject_submit_testid()

    # 改版后：旧定位器失效 → 自愈按 description 语义重定位按钮 → 登录（演示核心）
    login.submit()

    assert BookingShopPage(page, sut.base_url).is_logged_in(sut.customer_username), (
        f"商城自愈登录未成功（url={page.url}）"
    )
    successes = _heuristic_success_records(page)
    assert successes, f"未产生启发式自愈成功记录：{page.reporter.records}"
