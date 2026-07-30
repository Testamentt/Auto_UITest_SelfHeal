"""自愈定位器 —— 框架对外的核心入口之一。

当常规定位失败时，由本模块把现场交给 agent.orchestrator 走自愈闭环，
拿到修复后的定位器并重试。成功修复会沉淀进知识库。
TODO: 串联 collect / agent / knowledge。
"""

from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Locator, Page


@dataclass
class HealResult:
    success: bool
    new_selector: str | None = None
    confidence: float = 0.0
    strategy: str | None = None  # heuristic / semantic / visual / knowledge


class HealingLocator:
    def __init__(self, page: Page):
        self._page = page

    def find(self, selector: str, description: str | None = None) -> Locator:
        """优先用原始 selector 定位；失败时触发自愈。

        Args:
            selector: 原始定位器（ID/XPath/CSS 等）。
            description: 元素的自然语言描述，供语义/视觉策略使用。
        """
        try:
            locator = self._page.locator(selector)
            locator.wait_for(state="visible")
            return locator
        except Exception:
            heal = self._heal(selector, description)
            if heal.success and heal.new_selector:
                return self._page.locator(heal.new_selector)
            raise

    def _heal(self, selector: str, description: str | None) -> HealResult:
        # TODO: 调用 agent.orchestrator.SelfHealOrchestrator.run(...)
        return HealResult(success=False)
