# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""票多星客户端商城首页 POM。

被测页面事实（前端 `pages/shop/ShopPage.tsx` / `pages/shop/customerSession.ts`）：
- 商城首页 `/shop`，页头搜索框 `[data-testid="shop-search-input"]` 恒在；
- 客户登录态存 localStorage 的 `booking_customer_token`（另有 name / user_id /
  expires_at 三个配套键）；
- 页头右侧账号按钮文本为当前客户昵称（未登录时为「登录」），登录后作为
  "UI 已登录"的可视断言。
"""

from __future__ import annotations

from selfheal.engine.base_page import BasePage

_SEARCH_INPUT = '[data-testid="shop-search-input"]'
_CUSTOMER_TOKEN_JS = "() => localStorage.getItem('booking_customer_token') || ''"


class BookingShopPage(BasePage):
    """客户端商城首页：搜索框 + 客户会话 + 页头昵称三重登录态断言。"""

    def __init__(self, page, base_url: str):
        super().__init__(page)
        self._base_url = base_url.rstrip("/")

    @property
    def url(self) -> str:
        return f"{self._base_url}/shop"

    def open_shop(self) -> BookingShopPage:
        """打开商城首页并等搜索框渲染（React 首屏为空白，不能只看 URL）。"""
        self.open()
        self.page.locator(_SEARCH_INPUT).wait_for(state="visible", timeout=15_000)
        return self

    def has_customer_session(self) -> bool:
        """客户会话是否存在（localStorage 的 token 非空）。"""
        return bool(self.page.evaluate(_CUSTOMER_TOKEN_JS))

    def is_logged_in(self, customer_name: str | None = None, timeout: int = 15_000) -> bool:
        """登录态断言：搜索框可见 + 客户会话存在 +（可选）页头昵称按钮可见。"""
        try:
            self.page.locator(_SEARCH_INPUT).wait_for(state="visible", timeout=timeout)
            if not self.has_customer_session():
                return False
            if customer_name:
                self.page.get_by_role("button", name=customer_name, exact=True).first.wait_for(
                    state="visible", timeout=timeout
                )
            return True
        except Exception:  # noqa: BLE001 - 断言语义由调用方处理（等待超时=未登录）
            return False
