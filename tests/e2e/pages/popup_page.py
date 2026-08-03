"""弹窗演示页的页面对象（POM）。

目标按钮定位器是**有效的**——点击失败不是因为定位错，而是被弹窗遮挡。
这验证 PopupGuard 处理"被遮挡"类失败（区别于启发式处理"定位失效"）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfheal.engine.healing_locator import HealingPage

POPUP_PAGE_URL = (Path(__file__).resolve().parent / "popup_page.html").as_uri()


class PopupPage:
    """弹窗演示页对象。"""

    def __init__(self, page: HealingPage):
        self.page = page

    def open(self) -> None:
        self.page.goto(POPUP_PAGE_URL)

    def click_target(self) -> None:
        self.page.locator('[data-testid="target-btn"]', description="目标按钮").click()

    def result(self) -> str | None:
        return self.page.locator("#result").text_content()
