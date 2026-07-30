"""智能等待。

在固定超时之外，结合元素状态与页面稳定性做自适应等待，减少因加载抖动导致的误判。
TODO: 实现基于 DOM 稳定度 / 网络空闲的等待策略。
"""

from __future__ import annotations

from playwright.sync_api import Locator


def wait_until_stable(locator: Locator, timeout_ms: int = 10000) -> None:
    """等待目标元素进入可交互状态。当前为占位实现。"""
    locator.wait_for(state="visible", timeout=timeout_ms)
    # TODO: 追加 DOM 稳定度检测，避免元素闪现后重渲染。
