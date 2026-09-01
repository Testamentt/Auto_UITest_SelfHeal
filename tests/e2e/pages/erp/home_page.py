"""管伊佳 ERP 主框架页 POM（T23）：登录后的侧边菜单导航与页签断言。

页签/iframe 形态在 P2 场景实做时随用例勘测细化（当前仅导航与登录态断言所需最小集）。
"""

from __future__ import annotations

from selfheal.engine.base_page import BasePage


class ErpHomePage(BasePage):
    """ERP 主框架（侧边菜单 + 内容页签）。"""

    def __init__(self, page, base_url: str):
        super().__init__(page)
        self._base_url = base_url.rstrip("/")

    @property
    def url(self) -> str:
        return f"{self._base_url}/dashboard/analysis"

    def is_logged_in(self) -> bool:
        """登录态断言：URL 进入 dashboard 域（登录成功跳转目标）。"""
        return "/dashboard" in self.page.url
