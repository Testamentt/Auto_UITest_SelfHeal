"""弹窗演示页的页面对象（POM，继承 BasePage）。

目标按钮定位器是**有效的**——点击失败不是因为定位错，而是被弹窗遮挡。
这验证 PopupGuard 处理"被遮挡"类失败（区别于启发式处理"定位失效"）。
"""

from __future__ import annotations

from pathlib import Path

from selfheal.engine.base_page import BasePage

POPUP_PAGE_URL = (Path(__file__).resolve().parent / "popup_page.html").as_uri()


class PopupPage(BasePage):
    """弹窗演示页对象。"""

    url = POPUP_PAGE_URL

    def click_target(self) -> None:
        self.locator('[data-testid="target-btn"]', description="目标按钮").click()

    def result(self) -> str | None:
        return self.locator("#result").text_content()
