# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""票多星管理端登录页 POM。

被测页面事实（前端 `pages/auth/LoginPage.tsx`）：
- 表单 `form[data-testid="admin-login-form"]`，用户名 / 密码输入框与提交按钮分别带
  `data-testid="admin-username" / "admin-password" / "admin-login-submit"`；
- 提交按钮有可见文本「登录」，是自愈按 description 语义重定位的稳定文本锚点；
- 登录成功跳转管理端首页 `/`（dashboard 标题「数据概览」）。

**改版注入只改 `data-testid`，绝不动 `name`**：票多星前端是 React 受控表单，
`handleChange` 以 `e.target.name` 作为 state key，改 name 会让业务逻辑失效，而不再
是"定位器失效"。React 重渲染可能把 data-testid 写回，故注入用 MutationObserver
持续改名（改名后观察器再触发时已无 old 元素，不会死循环）。

**改版目标是提交按钮而非输入框**：输入框没有可见文本锚点（React 受控 input 的
`innerText` 为空，placeholder 与中文描述也不构成词元/子串关系），启发式只能靠
data-testid 的词元重叠拿到约 0.32 的弱置信度；按钮文本「登录」与 description
互为子串，强信号分支直接给出约 0.95，确定性可复现且零模型依赖。
"""

from __future__ import annotations

from selfheal.engine.base_page import BasePage

_ADMIN_FORM = 'form[data-testid="admin-login-form"]'

_RENAME_TESTID_JS = """
([oldId, newId]) => {
  const rename = () => {
    const el = document.querySelector(`[data-testid="${oldId}"]`);
    if (el) el.setAttribute('data-testid', newId);
  };
  rename();
  new MutationObserver(rename).observe(document.body, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['data-testid'],
  });
}
"""


class BookingAdminLoginPage(BasePage):
    """管理端登录页：凭管理员账号密码进入后台。"""

    def __init__(self, page, base_url: str):
        super().__init__(page)
        self._base_url = base_url.rstrip("/")

    @property
    def url(self) -> str:
        return f"{self._base_url}/login"

    def open_login(self) -> BookingAdminLoginPage:
        """打开登录页并等表单渲染完成（React 首屏为空白，不能只看 URL）。"""
        self.open()
        self.page.locator(_ADMIN_FORM).wait_for(state="visible", timeout=15_000)
        return self

    def fill_username(self, username: str) -> None:
        self.locator('[data-testid="admin-username"]', description="管理员用户名输入框").fill(
            username
        )

    def fill_password(self, password: str) -> None:
        self.locator('[data-testid="admin-password"]', description="管理员密码输入框").fill(
            password
        )

    def submit(self) -> None:
        self.locator('[data-testid="admin-login-submit"]', description="管理员登录按钮").click()

    def inject_submit_testid(
        self, old: str = "admin-login-submit", new: str = "admin-login-submit-v2"
    ) -> None:
        """改版注入：把登录按钮的 data-testid 由 old 改名为 new。

        与真实"前端升级改 testid"同构：旧定位器随即失效，按钮仍保留可见文本「登录」。
        MutationObserver 持续改名，抵消 React 重渲染把属性写回。
        """
        self.page.evaluate(_RENAME_TESTID_JS, [old, new])
