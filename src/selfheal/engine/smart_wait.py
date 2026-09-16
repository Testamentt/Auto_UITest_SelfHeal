"""智能等待。

在固定超时之外，结合元素位置/尺寸的稳定性做自适应等待：先等元素可见，再要求其
bounding_box 连续 stable_ms 毫秒不变才视为稳定，避免加载抖动/重渲染期间误操作。
Playwright 仅作类型依赖（TYPE_CHECKING），纯函数逻辑可无浏览器单测。

2026-10-08 审查修复（M6）：
- `timeout_ms<=0` 显式报错：Playwright 把 `timeout=0` 解释为"**不超时**"（等待失去上界），
  而非法参数此前会静默变成无界等待。
- **稳定窗口与可见性预算分离**：可见性等待最多用 `timeout_ms - stable_ms`，给稳定判定留下
  独立窗口。此前两者共用同一 deadline，元素恰在 deadline 前变可见时会立刻抛"未达到稳定"
  （元素其实已可见且稳定 → 伪失败）。
- `poll_ms` 下限 20ms（此前 `poll_ms<=0` 会退化成忙等）。
- 报错区分"未可见"（Playwright 抛）与"未稳定"（本函数抛）。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅类型检查时导入，避免运行期强依赖 playwright
    from playwright.sync_api import Locator

# 轮询下限：低于此值即为忙等（CPU 空转且无观测收益）
_MIN_POLL_MS = 20


def wait_until_stable(
    locator: Locator,
    timeout_ms: int = 10000,
    stable_ms: int = 300,
    poll_ms: int = 100,
) -> None:
    """等待目标元素可见且位置/尺寸稳定。

    Args:
        locator: 目标元素定位器。
        timeout_ms: 总超时（毫秒），必须 > 0；超时抛 TimeoutError。
        stable_ms: 需要保持不变的持续时长（毫秒），须 >= 0。
        poll_ms: 轮询间隔（毫秒），内部下限 20ms。

    Raises:
        ValueError: timeout_ms <= 0 或 stable_ms < 0（非法参数不再静默变成无界等待）。
        TimeoutError: 元素在预算内未可见（Playwright 抛）或可见后未达稳定（本函数抛）。
    """
    if timeout_ms <= 0:
        raise ValueError(f"timeout_ms 必须为正数（0 在 Playwright 语义里是'不超时'）: {timeout_ms}")
    if stable_ms < 0:
        raise ValueError(f"stable_ms 不能为负: {stable_ms}")
    poll = max(_MIN_POLL_MS, poll_ms)

    # 总预算从函数入口起算；可见性最多占用 (timeout_ms - stable_ms)，把 stable_ms 留给稳定判定
    deadline = time.monotonic() + timeout_ms / 1000
    visible_timeout = max(1, timeout_ms - stable_ms)
    locator.wait_for(state="visible", timeout=visible_timeout)

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
            raise TimeoutError(f"元素已可见但在 {timeout_ms}ms 预算内未达到稳定状态")
        time.sleep(poll / 1000)


def _safe_box(locator: Locator) -> Any:
    """读取 bounding_box，失败返回 None（视为暂不可测）。"""
    try:
        return locator.bounding_box()
    except Exception:  # noqa: BLE001 - 读取失败按暂不可测处理
        return None
