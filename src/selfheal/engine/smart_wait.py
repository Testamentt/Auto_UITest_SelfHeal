"""智能等待。

在固定超时之外，结合元素位置/尺寸的稳定性做自适应等待：先等元素可见，再要求其
bounding_box 连续 stable_ms 毫秒不变才视为稳定，避免加载抖动/重渲染期间误操作。
Playwright 仅作类型依赖（TYPE_CHECKING），纯函数逻辑可无浏览器单测。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅类型检查时导入，避免运行期强依赖 playwright
    from playwright.sync_api import Locator


def wait_until_stable(
    locator: Locator,
    timeout_ms: int = 10000,
    stable_ms: int = 300,
    poll_ms: int = 100,
) -> None:
    """等待目标元素可见且位置/尺寸稳定。

    Args:
        locator: 目标元素定位器。
        timeout_ms: 总超时（毫秒），超时抛 TimeoutError。
        stable_ms: 需要保持不变的持续时长（毫秒）。
        poll_ms: 轮询间隔（毫秒）。

    Raises:
        TimeoutError: timeout_ms 内元素未达稳定。
    """
    # 总超时从函数入口起算：wait_for 与稳定轮询共用同一预算，最坏耗时 ≈ timeout_ms
    deadline = time.monotonic() + timeout_ms / 1000
    locator.wait_for(state="visible", timeout=timeout_ms)
    last_box: Any = None
    stable_since: float | None = None
    while True:
        box = _safe_box(locator)
        now = time.monotonic()
        if box is None:  # 元素暂不可测（如重渲染瞬间），重置稳定计时
            last_box, stable_since = None, None
        elif box == last_box:
            if stable_since is not None and (now - stable_since) * 1000 >= stable_ms:
                return
        else:
            last_box, stable_since = box, now
        if now >= deadline:
            raise TimeoutError(f"元素在 {timeout_ms}ms 内未达到稳定状态")
        time.sleep(poll_ms / 1000)


def _safe_box(locator: Locator) -> Any:
    """读取 bounding_box，失败返回 None（视为暂不可测）。"""
    try:
        return locator.bounding_box()
    except Exception:  # noqa: BLE001 - 读取失败按暂不可测处理
        return None
