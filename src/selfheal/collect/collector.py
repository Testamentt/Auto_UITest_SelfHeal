"""现场采集器。

把一次失败的多维现场打包成 Scene，供 diagnose 与 strategies 消费。
采集是**尽力而为**：单项失败不炸闭环（置默认值），visual 无截图自然返回 None 被跳过。

网络日志（C5）：构造时挂载 page.on('request'/'response') 监听，缓存近期请求/响应
（限长防膨胀），capture() 时随 Scene 带出——供"页面加载分析"展示。

trace（T11）：capture() 顺带产出**现场内联 trace**——外层录制中（conftest `--trace-healing`
或 settings.browser.trace）时，把当前片段导出为独立现场 trace 填入 `Scene.trace_path`
（可 show-trace 回放）并恢复录制；未录制则不擅自启动（占位保持 None）。整用例 trace
由测试框架层录制（conftest 经 context.tracing 落盘 + CI 上传），两者职责互补不冲突。

注意：Playwright 仅作类型依赖（TYPE_CHECKING），本模块可在无浏览器环境下被单元测试导入。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

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

    def __init__(self, page: Page | None, trace_dir: str = "reports/traces"):
        self._page = page
        self._trace_dir = trace_dir  # T11：现场内联 trace 输出目录（默认与 browser.trace_dir 一致）
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
        scene.trace_path = self._try_inline_trace()  # T11：现场内联 trace（未录制则 None）
        scene.network_logs = list(self._network_logs)  # C5：随现场带出近期请求/响应
        return scene

    def _try_inline_trace(self) -> str | None:
        """T11：失败现场内联 trace（best-effort），使 `Scene.trace_path` 真实落盘而非占位。

        仅当页面 context 的 tracing **正在录制**（外层 conftest `--trace-healing` 或
        settings.browser.trace）时：停止当前片段导出为独立现场 trace（trace_dir /
        inline-trace-<uuid>.zip，随后立即重新 start 恢复录制）——现场 trace 含失败发生
        过程、可独立回放（`playwright show-trace`），整用例 trace 的后半段继续录制不中断。

        未在录制时（Playwright 的 stop 会抛 "Must start tracing before stopping"，
        实测确认）→ 不擅自启动录制（避免副作用/拖慢），返回 None 保持占位空语义。
        """
        if self._page is None:
            return None
        try:
            tracing = self._page.context.tracing
            path = self._inline_trace_path()
            tracing.stop(path=str(path))  # 未录制时抛错 → 走 except（安全探测，零副作用）
        except Exception:  # noqa: BLE001 - 未录制/保存失败：不生成现场 trace
            logger.debug(
                "现场内联 trace 不可用（未录制或保存失败），Scene.trace_path 保持占位",
                exc_info=True,
            )
            return None
        # stop 成功：现场 trace 已保存；尽力恢复外层录制（与 conftest 同参数），
        # 恢复失败不丢已保存的现场 trace（仅整用例 trace 后半段缺失，不阻塞）
        try:
            tracing.start(screenshots=True, snapshots=True)
        except Exception:  # noqa: BLE001 - 恢复录制失败不阻塞
            logger.warning("恢复整用例 trace 录制失败（现场 trace 已单独保存）", exc_info=True)
        return str(path)

    def _inline_trace_path(self) -> Path:
        """现场 trace 输出路径：trace_dir 目录（自动创建）+ 唯一文件名（uuid 前缀防并发冲突）。"""
        directory = Path(self._trace_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"inline-trace-{uuid4().hex[:8]}.zip"

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
