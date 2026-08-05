"""现场采集器。

把一次失败的多维现场打包成 Scene，供 diagnose 与 strategies 消费。
采集是**尽力而为**：单项失败不炸闭环（置默认值），visual 无截图自然返回 None 被跳过。
TODO: 实现 trace 录制、网络日志缓存（page.on('response')）。

注意：Playwright 仅作类型依赖（TYPE_CHECKING），本模块可在无浏览器环境下被单元测试导入。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型检查时导入，避免运行期强依赖 playwright
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)


@dataclass
class Scene:
    """一次失败的完整现场快照。"""

    url: str
    screenshot: bytes | None = None
    dom_snapshot: str | None = None
    network_logs: list[dict] = field(default_factory=list)
    trace_path: str | None = None


class SceneCollector:
    """失败现场采集。page 在构造时不做任何调用，capture() 时才采集。"""

    def __init__(self, page: Page | None):
        self._page = page

    def capture(self) -> Scene:
        """采集当前页面现场（尽力而为：单项失败置默认，不中断主流程）。"""
        if self._page is None:  # 允许无 page 构造（便于纯逻辑测试）
            return Scene(url="")
        scene = Scene(url=self._safe_url())
        scene.screenshot = self._safe_screenshot()
        scene.dom_snapshot = self._safe_dom()
        # TODO: 追加 network_logs 与 trace_path
        return scene

    def _safe_url(self) -> str:
        try:
            return self._page.url
        except Exception:  # noqa: BLE001 - 页面不可用时 URL 采集失败不炸闭环
            return ""

    def _safe_screenshot(self) -> bytes | None:
        try:
            return self._page.screenshot(full_page=True)
        except Exception:  # noqa: BLE001 - 截图失败不炸闭环（visual 无截图自然跳过）
            logger.warning("现场截图采集失败", exc_info=True)
            return None

    def _safe_dom(self) -> str | None:
        try:
            return self._page.content()
        except Exception:  # noqa: BLE001 - DOM 采集失败不炸闭环
            logger.warning("DOM 快照采集失败", exc_info=True)
            return None
