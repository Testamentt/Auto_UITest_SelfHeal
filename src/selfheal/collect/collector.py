"""现场采集器。

把一次失败的多维现场打包成 Scene，供 diagnose 与 strategies 消费。
TODO: 实现 trace 录制、网络日志缓存（page.on('response')）。

注意：Playwright 仅作类型依赖（TYPE_CHECKING），本模块可在无浏览器环境下被单元测试导入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型检查时导入，避免运行期强依赖 playwright
    from playwright.sync_api import Page


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

    def __init__(self, page: "Page | None"):
        self._page = page

    def capture(self) -> Scene:
        """采集当前页面现场。当前实现截图 + DOM，网络/trace 待补。"""
        if self._page is None:  # 允许无 page 构造（便于纯逻辑测试）
            return Scene(url="")
        scene = Scene(url=self._page.url)
        scene.screenshot = self._page.screenshot(full_page=True)
        scene.dom_snapshot = self._page.content()
        # TODO: 追加 network_logs 与 trace_path
        return scene
