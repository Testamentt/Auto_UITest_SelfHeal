"""Playwright 浏览器封装。

统一管理浏览器 / 上下文 / 页面的生命周期。BrowserConfig（headless/channel/slow_mo/viewport）已接入。

2026-10-08 审查修复（M7/A2）：
- `__enter__` 内 `sync_playwright().start()` 成功后若 `launch()` 抛错，此前 `__exit__` 永不执行
  → Playwright 的 node 驱动子进程与管道泄漏（CI 未装 Chrome 属高频场景）。现改为失败即清理再上抛。
- `__exit__` 幂等：清理后把内部引用置 None，重复退出不再重复 stop。
- 记录本管理器创建的 context，`__exit__` 统一关闭（此前 `new_page()` 建出的 context 只能靠
  `page.context.close()` 手动兜），并兼容调用方自行关闭（重复 close 是 no-op）。
- 前置校验用显式 `RuntimeError` 替代 `assert`（`python -O` 下 assert 会被剥离）。
"""

from __future__ import annotations

import contextlib
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from selfheal.config import Settings


class BrowserManager:
    """以上下文管理器方式持有 Playwright 与浏览器实例。"""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._contexts: list[BrowserContext] = []

    def __enter__(self) -> BrowserManager:
        self._pw = sync_playwright().start()
        try:
            cfg = self._settings.browser
            # channel 是 launch 参数而非 browser type（browser type 恒为 chromium）
            launch_kwargs: dict = {"headless": cfg.headless, "slow_mo": cfg.slow_mo}
            if cfg.channel not in ("chromium", "default"):
                launch_kwargs["channel"] = cfg.channel
            self._browser = self._pw.chromium.launch(**launch_kwargs)
        except BaseException:
            # launch 失败也要回收已启动的驱动子进程（否则 __exit__ 不会被调用）
            self._cleanup()
            raise
        return self

    def new_context(self, **kwargs: Any) -> BrowserContext:
        """创建浏览器上下文（可传 record_video/trace 等 Playwright context 参数）。

        本管理器记录创建的 context，`__exit__` 时统一关闭。
        """
        if self._browser is None:
            raise RuntimeError("浏览器尚未启动，请先在 with BrowserManager(...) 上下文中使用")
        opts: dict[str, Any] = {"viewport": self._settings.browser.viewport}
        opts.update(kwargs)
        context = self._browser.new_context(**opts)
        self._contexts.append(context)
        return context

    def new_page(self) -> Page:
        """为便捷创建新上下文 + 页面（每调用一个独立 context，由本管理器统一关闭）。"""
        return self.new_context().new_page()

    def _cleanup(self) -> None:
        """逆序释放：context → browser → driver；每步 best-effort，幂等（引用置 None）。"""
        for context in self._contexts:
            with contextlib.suppress(Exception):
                context.close()
        self._contexts.clear()
        if self._browser is not None:
            browser, self._browser = self._browser, None
            with contextlib.suppress(Exception):
                browser.close()
        if self._pw is not None:
            driver, self._pw = self._pw, None
            with contextlib.suppress(Exception):
                driver.stop()

    def __exit__(self, exc_type, exc, tb) -> None:
        self._cleanup()
