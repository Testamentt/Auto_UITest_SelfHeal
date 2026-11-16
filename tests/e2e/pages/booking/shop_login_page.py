# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""票多星客户端商城登录页 POM。

被测页面事实（前端 `pages/shop/ShopLoginPage.tsx`）：
- 表单 `form[data-testid="shop-login-form"]`；用户名 / 密码输入框与提交按钮分别带
  `data-testid="shop-username" / "shop-password" / "shop-login-submit"`；
- 输入框 placeholder 为「用户名」/「密码」（注册模式才出现「手机号」）；
- 提交按钮在默认 login 模式下文本为「登录」（register 模式为「注册并登录」），
  是自愈按 description 语义重定位的稳定文本锚点；
- 默认 `returnTo=/shop`，登录成功写入客户会话后跳转商城首页。

与管理端登录页同款约束：改版注入只改 `data-testid`，不改 `name`（商城表单虽为
受控 state，注入保持与真实"前端升级改 testid"同构且不影响业务字段语义）。

**改版目标是提交按钮而非输入框**：输入框没有可见文本锚点，启发式只能靠 data-testid
词元重叠得到弱置信度；按钮文本与 description 互为子串，强信号分支确定性通过。
"""

from __future__ import annotations

from selfheal.engine.base_page import BasePage

_SHOP_FORM = 'form[data-testid="shop-login-form"]'

# 商城登录表单容器连同提交按钮一起改版：容器 `data-testid` 被移除（组件重构常见形态）。
# 必要性：该表单容器的可交互文本恰好只有提交按钮的「登录」（输入框无 innerText），
# 启发式的强信号会把容器与按钮打成同分（0.95）并按 DOM 顺序先取容器——点击容器不会
# 提交表单。移除容器 testid 后它失去唯一稳定定位器（text="登录" 与模式切换按钮重名），
# 自愈只能落到真正的提交按钮上，结果确定。
_SHOP_INJECT_JS = """
([oldId, newId]) => {
  const normalize = () => {
    const submit = document.querySelector(`[data-testid="${oldId}"]`);
    if (submit) submit.setAttribute('data-testid', newId);
    const container = document.querySelector('form[data-testid="shop-login-form"]');
    if (container) container.removeAttribute('data-testid');
  };
  normalize();
  new MutationObserver(normalize).observe(document.body, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['data-testid'],
  });
}
"""


class BookingShopLoginPage(BasePage):
    """客户端商城登录页：凭客户账号密码进入商城。"""

    def __init__(self, page, base_url: str):
        super().__init__(page)
        self._base_url = base_url.rstrip("/")

    @property
    def url(self) -> str:
        return f"{self._base_url}/shop/login"

    def open_login(self) -> BookingShopLoginPage:
        """打开商城登录页并等表单渲染完成。"""
        self.open()
        self.page.locator(_SHOP_FORM).wait_for(state="visible", timeout=15_000)
        return self

    def fill_username(self, username: str) -> None:
        self.locator('[data-testid="shop-username"]', description="客户用户名输入框").fill(username)

    def fill_password(self, password: str) -> None:
        self.locator('[data-testid="shop-password"]', description="客户密码输入框").fill(password)

    def submit(self) -> None:
        self.locator('[data-testid="shop-login-submit"]', description="客户登录按钮").click()

    def inject_submit_testid(
        self, old: str = "shop-login-submit", new: str = "shop-login-submit-v2"
    ) -> None:
        """改版注入：登录按钮 data-testid 改名，同时移除表单容器 testid（见上方注释）。

        与真实"前端升级改 testid"同构：旧按钮定位器随即失效，按钮保留可见文本「登录」；
        MutationObserver 持续压制 React 重渲染把属性写回（容器 testid 也持续移除）。
        """
        self.page.evaluate(_SHOP_INJECT_JS, [old, new])
