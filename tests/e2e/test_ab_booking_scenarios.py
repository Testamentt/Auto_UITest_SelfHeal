# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""端到端测试：**真实被测系统（票多星）**上的自愈 A/B 对比场景（需本地 SUT，-m booking）。

与 `test_ab_scenarios.py`（自建 demo 页、确定性构造）互补：这里在真实 React 页面上做
"前端改版注入"（运行时改 `data-testid`，同真实前端升级形态），对比：

- `disabled` 变体：关闭自愈 → 旧定位器超时失败（xfail = 需人工修复）；
- `healing` 变体：开启自愈 → 按可见文本/描述重定位 → 操作成功。

**为什么只改 `data-testid` 不改 `name`**：票多星是 React 受控表单，`e.target.name` 是
state key；改 name 会让业务逻辑失效（那就不是定位问题了）。注入用 MutationObserver
持续压制重渲染写回，见 `tests/e2e/pages/booking/*.py` 的实现注释。

前置：前端 `:3001` 与后端 `:8088/api` 已启动（未启动时本文件用例 skip）。
运行：`python scripts/ab_compare.py --scenario-file tests/e2e/test_ab_booking_scenarios.py --out reports/ab-booking-compare.md`
"""

from __future__ import annotations

import urllib.request

import pytest

from tests.e2e.pages.booking.admin_login_page import BookingAdminLoginPage
from tests.e2e.pages.booking.shop_login_page import BookingShopLoginPage

pytestmark = [pytest.mark.e2e, pytest.mark.healing, pytest.mark.booking]

ab_variants = pytest.mark.parametrize(
    "ab_booking_page",
    [
        pytest.param(
            "disabled",
            marks=pytest.mark.xfail(
                strict=True, reason="无自愈：真实页面改版定位失效，需人工修复（disabled 变体）"
            ),
        ),
        "healing",
    ],
    indirect=True,
)


def _ensure_sut(base_url: str) -> None:
    """真实 SUT 不可达时 skip（不能把"环境没开"记成"自愈失败"）。"""
    try:
        urllib.request.urlopen(base_url, timeout=3)
    except Exception as exc:  # noqa: BLE001 - 环境问题一律 skip
        pytest.skip(f"票多星未启动（{base_url}）：{exc}")


@pytest.fixture
def ab_booking_page(request, settings, knowledge, context):
    """A/B 双变体页面（真实 SUT，匿名会话；不依赖登录凭证）。"""
    from selfheal.plugin.healing_page import HealingPage  # 惰性导入（装配层在 plugin/）
    from tests.conftest import _session_reporters

    _ensure_sut(settings.sut.base_url)
    if request.param == "disabled":
        page = HealingPage(
            context.new_page(), settings, knowledge=knowledge, enabled_override=False
        )
        page.set_default_timeout(800)  # 旧定位器快速失败，缩短 A 组耗时
    else:
        page = HealingPage(context.new_page(), settings, knowledge=knowledge, enabled_override=True)
    _session_reporters.append(page.reporter)
    yield page
    page.close()


@ab_variants
def test_ab_b1_admin_login_button(ab_booking_page, settings):
    """真实场景 1：管理端登录按钮 data-testid 改版 → 自愈凭可见文本「登录」重定位。"""
    login = BookingAdminLoginPage(ab_booking_page, settings.sut.base_url).open_login()
    login.fill_username("ab_probe")  # 让表单可提交（不依赖真实凭证）
    login.fill_password("ab_probe")
    login.inject_submit_testid()  # 运行时改版：admin-login-submit -> -v2
    login.submit()  # 旧 testid 已失效
    if ab_booking_page.healing_enabled:
        assert any(
            r.original_selector == '[data-testid="admin-login-submit"]'
            for r in ab_booking_page.reporter.records
        ), f"未产生管理端登录按钮的自愈记录：{ab_booking_page.reporter.records}"


@ab_variants
def test_ab_b2_shop_login_button(ab_booking_page, settings):
    """真实场景 2：商城登录按钮 data-testid 改版（容器 testid 同时移除）→ 自愈重定位。"""
    login = BookingShopLoginPage(ab_booking_page, settings.sut.base_url).open_login()
    login.fill_username("ab_probe")  # 商城表单两个输入框都填了按钮才 enabled
    login.fill_password("ab_probe")
    login.inject_submit_testid()
    login.submit()
    if ab_booking_page.healing_enabled:
        assert any(
            r.original_selector == '[data-testid="shop-login-submit"]'
            for r in ab_booking_page.reporter.records
        ), f"未产生商城登录按钮的自愈记录：{ab_booking_page.reporter.records}"
