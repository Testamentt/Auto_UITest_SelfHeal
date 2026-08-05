"""POM 基类（基础框架 B1）。

统一"POM 如何拿 page、如何打开、如何定位"。POM 只依赖 page 接口
（原生 Page 或 HealingPage 均可），自愈开/关都能跑（决策 D3）。

约定：
- 子类声明 `url`（property），`open()` 默认跳转到它。
- 用 property 暴露关键元素，动作写成方法（如 `login()`）。
- `locator()` 透传 description/fallback（HealingPage 增强参数）；原生 Page 下
  若未提供这两者则等价于原生调用，保证基座与自愈插件正交、可兼容。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅类型检查时导入，避免运行期强依赖 playwright
    from playwright.sync_api import Page


class BasePage:
    """页面对象基类。"""

    def __init__(self, page: Page):
        self.page = page

    @property
    def url(self) -> str:
        raise NotImplementedError("子类需声明 url")

    def open(self, url: str | None = None) -> None:
        """打开页面；未传 url 时跳转到子类声明的 url。"""
        self.page.goto(url or self.url)

    def locator(
        self,
        selector: str,
        *,
        description: str | None = None,
        fallback: str | None = None,
        **kwargs: Any,
    ):
        """返回定位器。

        description/fallback 为 HealingPage 的自愈增强参数；原生 Page 下若未提供
        则按原生调用透传（保证 POM 在开/关自愈时同一份代码都能跑）。
        """
        if description is None and fallback is None:
            return self.page.locator(selector, **kwargs)
        return self.page.locator(selector, description=description, fallback=fallback, **kwargs)
