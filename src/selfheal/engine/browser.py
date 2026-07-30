"""Playwright 浏览器封装。

统一管理浏览器/上下文/页面的生命周期，并在步骤执行失败时触发采集与自愈。
TODO: 接入 config.BrowserConfig；在定位失败处调用 agent.orchestrator。
"""

from __future__ import annotations

from playwright.sync_api import Browser, Page, Playwright, sync_playwright

from selfheal.config import Settings


class BrowserManager:
    """以上下文管理器方式持有 Playwright 与浏览器实例。"""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._pw: Playwright | None = None
        self._browser: Browser | None = None

    def __enter__(self) -> "BrowserManager":
        self._pw = sync_playwright().start()
        cfg = self._settings.browser
        launcher = getattr(self._pw, cfg.channel, self._pw.chromium)
        self._browser = launcher.launch(headless=cfg.headless, slow_mo=cfg.slow_mo)
        return self

    def new_page(self) -> Page:
        assert self._browser is not None, "浏览器尚未启动，请先进入上下文"
        ctx = self._browser.new_context(viewport=self._settings.browser.viewport)
        return ctx.new_page()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
