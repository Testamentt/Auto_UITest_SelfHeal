"""现场采集器。

把一次失败的多维现场打包成 Scene，供 diagnose 与 strategies 消费。
采集是**尽力而为**：单项失败不炸闭环（置默认值），visual 无截图自然返回 None 被跳过。

网络日志（C5）：构造时挂载 page.on('request'/'response') 监听，缓存近期请求/响应
（限长防膨胀），capture() 时随 Scene 带出——供"页面加载分析"展示。trace 由测试框架
层录制（conftest `--trace-healing` 经 context.tracing 落盘 + CI 上传），采集器不参与。

注意：Playwright 仅作类型依赖（TYPE_CHECKING），本模块可在无浏览器环境下被单元测试导入。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from selfheal.agent.dom import (
    CrossCheck,
    cross_validate_interactive,
    parse_interactive_elements,
    parse_interactive_elements_native,
)

if TYPE_CHECKING:  # 仅类型检查时导入，避免运行期强依赖 playwright
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)

# 网络日志缓存上限（页面加载请求较多，防内存膨胀）
_NETWORK_LOG_LIMIT = 200


@dataclass
class Scene:
    """一次失败的完整现场快照。"""

    url: str
    screenshot: bytes | None = None
    dom_snapshot: str | None = None
    network_logs: list[dict] = field(default_factory=list)
    trace_path: str | None = None
    # T8：Playwright 原生解析的可交互候选 + 与静态解析的交叉校验结果（page 可用时填充，否则 None）
    native_elements: list | None = None
    dom_cross_check: CrossCheck | None = None


class SceneCollector:
    """失败现场采集。page 在构造时不做调用，capture() 时才采集；构造时挂网络监听。"""

    def __init__(self, page: Page | None):
        self._page = page
        self._network_logs: list[dict] = []
        self._attach_network_listeners(page)

    def _attach_network_listeners(self, page: Any) -> None:
        """挂载请求/响应监听，缓存近期网络日志（best-effort：页面不支持则跳过）。"""
        if page is None:
            return
        try:
            on = getattr(page, "on", None)
            if on is None:
                return
            on("request", self._on_request)
            on("response", self._on_response)
        except Exception:  # noqa: BLE001 - 监听挂载失败不影响采集
            logger.warning("网络日志监听挂载失败（跳过）", exc_info=True)

    def _on_request(self, req: Any) -> None:
        try:
            self._network_logs.append(
                {
                    "type": "request",
                    "url": getattr(req, "url", ""),
                    "method": getattr(req, "method", ""),
                }
            )
            self._trim_network_logs()
        except Exception:  # noqa: BLE001 - 单条日志失败不影响
            pass

    def _on_response(self, resp: Any) -> None:
        try:
            self._network_logs.append(
                {
                    "type": "response",
                    "url": getattr(resp, "url", ""),
                    "status": getattr(resp, "status", ""),
                }
            )
            self._trim_network_logs()
        except Exception:  # noqa: BLE001 - 单条日志失败不影响
            pass

    def _trim_network_logs(self) -> None:
        if len(self._network_logs) > _NETWORK_LOG_LIMIT:
            del self._network_logs[: len(self._network_logs) - _NETWORK_LOG_LIMIT]

    def capture(self) -> Scene:
        """采集当前页面现场（尽力而为：单项失败置默认，不中断主流程）。"""
        if self._page is None:  # 允许无 page 构造（便于纯逻辑测试）
            return Scene(url="")
        scene = Scene(url=self._safe_url())
        scene.screenshot = self._safe_screenshot()
        scene.dom_snapshot = self._safe_dom()
        self._try_native_cross_check(scene)  # T8：原生解析 + 交叉校验（best-effort）
        scene.network_logs = list(self._network_logs)  # C5：随现场带出近期请求/响应
        return scene

    def _try_native_cross_check(self, scene: Scene) -> None:
        """T8：用 Playwright 原生查询解析交互候选，并与静态解析交叉校验。

        结果写入 scene（native_elements / dom_cross_check），供策略链优先原生、
        报告溯源一致性。**best-effort**：原生查询失败仅记 debug，scene 字段保持 None
        （策略链随即回退静态解析，零行为变化）。
        """
        try:
            native = parse_interactive_elements_native(self._page)
            static = parse_interactive_elements(scene.dom_snapshot)
            check = cross_validate_interactive(static, native)
            scene.native_elements = native
            scene.dom_cross_check = check
            if check.total > 0 and not check.consistent:
                # R4：不静默——两来源口径漂移要可发现（明细进 Scene 供报告/审计溯源）
                logger.warning(
                    "DOM 两来源解析不一致：静态独有=%s，原生独有=%s",
                    check.only_static,
                    check.only_native,
                )
        except Exception:  # noqa: BLE001 - 原生解析失败降级静态，不炸采集
            logger.debug("原生 DOM 解析/交叉校验失败（回退静态解析）", exc_info=True)

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
