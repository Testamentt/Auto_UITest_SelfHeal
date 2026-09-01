"""管伊佳 ERP 登录页 POM（T23）。

勘测确认（2026-08-31）：
- 前端为 **Ant Design Vue**（非 Element-UI）；登录输入框有稳定 id（`#loginName` / `#password`）；
- 表单支持 Enter 提交（实测验证）；登录成功跳转 `/dashboard/analysis`；
- 验证码已在被测环境关闭（用户确认），UI 登录可直接自动化。

url 来自 settings.sut.base_url（被测环境可换），故用 property 覆盖 BasePage.url。
"""

from __future__ import annotations

from selfheal.engine.base_page import BasePage


class ErpLoginPage(BasePage):
    """登录页：凭账号密码进入 ERP 主框架。"""

    def __init__(self, page, base_url: str):
        super().__init__(page)
        self._base_url = base_url.rstrip("/")

    @property
    def url(self) -> str:
        return f"{self._base_url}/user/login"

    def open_login(self) -> ErpLoginPage:
        self.open()
        return self

    def login(self, username: str, password: str) -> None:
        """填账号密码并点击登录按钮，**等待登录跳转完成**（vue-router 异步 push，
        不等待则调用方立即断言时 URL 仍是登录页——实测踩坑）。"""
        self.locator("#loginName", description="登录用户名输入框").fill(username)
        self.locator("#password", description="登录密码输入框").fill(password)
        self.locator("button.ant-btn-primary", description="登录按钮").click()
        self.page.wait_for_url("**/dashboard/**", timeout=15_000)
