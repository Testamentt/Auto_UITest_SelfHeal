# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""票多星管理端首页 POM（登录后的 dashboard）。

被测页面事实（前端 `pages/dashboard/DashboardPage.tsx`）：登录成功跳转 `/`，
页面标题为 `<h1>数据概览</h1>`（统计数据加载完成后渲染）；登录页表单
`form[data-testid="admin-login-form"]` 此时应已从 DOM 消失。
"""

from __future__ import annotations

from selfheal.engine.base_page import BasePage


class BookingAdminHomePage(BasePage):
    """管理端首页：以「数据概览」标题 + 登录表单消失判定登录态。"""

    def __init__(self, page, base_url: str):
        super().__init__(page)
        self._base_url = base_url.rstrip("/")

    @property
    def url(self) -> str:
        return f"{self._base_url}/"

    def is_logged_in(self, timeout: int = 15_000) -> bool:
        """登录态断言：dashboard 标题可见且登录表单已不存在；任何异常均视为未登录。"""
        try:
            self.page.get_by_role("heading", name="数据概览", exact=True).wait_for(
                state="visible", timeout=timeout
            )
            return self.page.locator('[data-testid="admin-login-form"]').count() == 0
        except Exception:  # noqa: BLE001 - 断言语义由调用方处理（等待超时=未登录）
            return False
