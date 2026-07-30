"""现场采集器。

把一次失败的多维现场打包成 Scene，供 diagnose 与 strategies 消费。
TODO: 实现 trace 录制、网络日志缓存（page.on('response')）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    def __init__(self, page: Page):
        self._page = page

    def capture(self) -> Scene:
        """采集当前页面现场。当前实现截图 + DOM，网络/trace 待补。"""
        scene = Scene(url=self._page.url)
        scene.screenshot = self._page.screenshot(full_page=True)
        scene.dom_snapshot = self._page.content()
        # TODO: 追加 network_logs 与 trace_path
        return scene
