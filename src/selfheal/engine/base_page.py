# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""POM 基类。

统一"POM 如何拿 page、如何打开、如何定位、如何断言"。POM 只依赖 page 接口
（原生 Page 或 HealingPage 均可），自愈开/关都能跑。

约定：
- 子类声明 `url`（property），`open()` 默认跳转到它。
- 用 property 暴露关键元素，动作写成方法（如 `login()`）。
- `locator()` 透传 description/fallback（HealingPage 增强参数）；原生 Page 下
  **自动丢弃**这两个参数并首次告警（原生 Locator 不接受它们，透传会 TypeError）。
- 断言用 `expect_text()`：Playwright 模块级 `expect()` 不接受 HealingLocator 代理
  （硬 `isinstance` 校验），本方法内部解包为原生 Locator。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅类型检查时导入，避免运行期强依赖 playwright
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)


class BasePage:
    """页面对象基类。"""

    def __init__(self, page: Page):
        self.page = page
        # 关闭自愈（原生 Page）时传入增强参数只告警一次，避免每个定位刷屏
        self._healing_kwargs_warned = False

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

        description/fallback 为 HealingPage 的自愈增强参数：

        - page 是 HealingPage（``supports_healing_kwargs = True``）→ 透传，进自愈闭环；
        - page 是原生 Page → **丢弃**这两个参数并首次记 warning（原生 Locator 不接受它们，
          原样透传会抛 ``TypeError``；丢弃后同一份 POM 在"关闭自愈"下仍能跑，
          只是没有语义增强——这正是插件化的语义）。
        """
        if getattr(self.page, "supports_healing_kwargs", False):
            return self.page.locator(selector, description=description, fallback=fallback, **kwargs)
        if description is not None or fallback is not None:
            self._warn_healing_kwargs_ignored(selector)
        return self.page.locator(selector, **kwargs)

    def _warn_healing_kwargs_ignored(self, selector: str) -> None:
        """关闭自愈时增强参数被忽略——只告警一次，说明原因与出路。"""
        if self._healing_kwargs_warned:
            return
        self._healing_kwargs_warned = True
        logger.warning(
            "当前 page 不支持自愈增强参数（原生 Page）：已忽略 description/fallback"
            "（首次出现在 selector=%r）。需要自愈请注入 healing_page；这是"
            "「关闭自愈=原生行为」的预期语义。",
            selector,
        )

    @staticmethod
    def _native(locator: Any) -> Any:
        """解包为原生 Locator：HealingLocator 有 ``native`` 属性，原生 Locator 原样返回。"""
        return getattr(locator, "native", locator)

    def expect_text(self, text: str, *, timeout_ms: float | None = None) -> None:
        """断言当前页面包含指定文案。

        **刻意不自愈**：断言失败即失败——把"文案变了"当定位问题修，属于改写预期
        （`docs/architecture.md` 的风险边界：禁止为通过而通过）。本方法只负责
        把 HealingLocator 解包成原生 Locator，绕开 Playwright ``expect()``
        对代理对象的 ``ValueError: Unsupported type``。
        """
        from playwright.sync_api import expect  # 惰性导入：本模块可在无浏览器环境被单测导入

        body = self.page.locator("body")
        expect(self._native(body)).to_contain_text(text, timeout=timeout_ms)
