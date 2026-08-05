"""自愈定位器 —— 插件层代理（见 RULE.md 决策 D5 / D6）。

设计要点：
- **接口兼容代理**：HealingPage / HealingLocator 与 Playwright 的 Page / Locator 接口兼容，
  面向 Page 接口编写的 POM 代码，开 / 关自愈都能运行（你的第 1 点：POM 无缝切换）。
- **只拦截小集合动作**：仅对 HEALABLE 动作方法包一层"失败→自愈→重试"，其余 API 原样透传。
- **可开关、零侵入**：enabled=False 时动作直通、不做任何修复，行为等同原生（你的第 2 点：插件/挂件）。
- **兜底（D6）**：AI 不确定时按 on_uncertain 处理——use_fallback（默认）/ pause（仅交互模式）/ fail。

Playwright 为运行期可选依赖：TimeoutError 导入做了降级，保证本模块可在无浏览器环境被单元测试导入。
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from selfheal.agent.diagnose import FailureContext
from selfheal.agent.orchestrator import HealOutcome, SelfHealOrchestrator
from selfheal.config import HealingConfig, Settings
from selfheal.engine.popup_guard import PopupGuard
from selfheal.engine.smart_wait import wait_until_stable as _wait_until_stable
from selfheal.knowledge.base import KnowledgeBackend
from selfheal.knowledge.factory import build_knowledge_store
from selfheal.reporting.hooks import HealingReporter

if TYPE_CHECKING:  # 仅类型检查时导入
    from playwright.sync_api import Locator, Page

logger = logging.getLogger(__name__)

# 运行期可选依赖 playwright：未安装时降级，使纯逻辑单测可导入本模块
try:  # pragma: no cover - 取决于运行环境是否安装 playwright
    from playwright.sync_api import TimeoutError as _PWTimeoutError
except ImportError:  # pragma: no cover
    _PWTimeoutError = None


class HealingFailedError(Exception):
    """自愈失败且无可用兜底时抛出，携带诊断报告。"""


def _is_timeout_error(exc: BaseException) -> bool:
    """判定是否为 Playwright 定位/等待超时（自愈的触发条件）。"""
    if _PWTimeoutError is not None and isinstance(exc, _PWTimeoutError):
        return True
    return "Timeout" in type(exc).__name__  # 兜底：无 playwright 时按类名粗判


def _interactive() -> bool:
    """是否处于可交互终端（pause 模式仅在此生效，CI 中恒为 False）。"""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


class HealingLocator:
    """带自愈能力的 Locator 代理。

    通过 __getattr__ 透传原生 Locator 的全部属性；仅当开关开启且访问的是 HEALABLE
    动作时，返回一个包了"失败→自愈→重试"的包装函数。
    """

    # 需要纳入自愈的动作方法（定位失败最可能发生处）
    HEALABLE = (
        "click",
        "dblclick",
        "fill",
        "check",
        "uncheck",
        "press",
        "select_option",
        "hover",
        "tap",
        "set_input_files",
    )

    def __init__(
        self,
        locator: Locator,
        page: Page,
        selector: str,
        cfg: HealingConfig,
        orchestrator: SelfHealOrchestrator,
        *,
        fallback: str | None = None,
        description: str | None = None,
        enabled: bool = True,
        popup_guard: PopupGuard | None = None,
    ):
        self._locator = locator
        self._page = page
        self._selector = selector
        self._cfg = cfg
        self._orch = orchestrator
        self._fallback = fallback
        self._description = description
        self._enabled = enabled
        self._popup_guard = popup_guard

    def __getattr__(self, name: str) -> Any:
        # 仅对实例上不存在的属性触发；__init__ 设置的属性不走这里
        attr = getattr(self._locator, name)
        if self._enabled and name in self.HEALABLE and callable(attr):
            return self._healing_action(name, attr)
        return attr

    def _healing_action(self, name: str, action: Callable) -> Callable:
        """把原生动作包成：失败（超时）→ 自愈 → 用新定位器重试。"""

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            try:
                return action(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 需甄别超时后决定自愈或上抛
                if not _is_timeout_error(exc):
                    raise
                # 先尝试清除弹窗（"被遮挡"类失败的高频根因）；清掉则直接重试原动作
                if self._popup_guard is not None and self._popup_guard.dismiss_if_present():
                    try:
                        return action(*args, **kwargs)
                    except Exception:  # noqa: BLE001 - 弹窗非根因，继续走自愈闭环
                        pass
                relocated = self._heal_and_resolve(exc)
                try:
                    return getattr(relocated, name)(*args, **kwargs)
                except Exception as retry_exc:  # noqa: BLE001 - 自愈后的选择器仍失败
                    if not _is_timeout_error(retry_exc):
                        raise
                    # 二次自愈（T4）：跳过知识缓存，防重复命中同一失效项；
                    # 上限一次、其结果为裸调用不再包裹，故不会死循环。
                    relocated2 = self._heal_and_resolve(retry_exc, use_knowledge=False)
                    return getattr(relocated2, name)(*args, **kwargs)

        wrapped.__name__ = name
        return wrapped

    def _heal_and_resolve(self, exc: BaseException | None = None, use_knowledge: bool = True) -> Locator:
        """运行闭环并决定用哪个定位器重试（含 D6 兜底）。use_knowledge=False 用于二次自愈（跳过缓存）。

        顶层兜底（H1）：闭环内部异常（采集/知识读取/策略等）不替换原始定位失败异常，
        而是转为"自愈内部失败"outcome 走 D6 兜底（fallback/pause/fail），保留触发自愈的语义。
        """
        failure = FailureContext(
            failure_type=type(exc).__name__ if exc is not None else None,
            message=str(exc) if exc is not None else None,
        )
        try:
            outcome = self._orch.run(self._selector, self._description, failure=failure, use_knowledge=use_knowledge)
        except Exception as run_exc:  # noqa: BLE001 - 闭环内部异常兜底，不替换原始异常
            logger.warning("自愈闭环内部异常，按不确定兜底处理", exc_info=True)
            outcome = HealOutcome(success=False, root_cause=f"heal_internal:{type(run_exc).__name__}")
        if outcome.success and outcome.new_selector:
            return self._page.locator(outcome.new_selector)
        return self._resolve_uncertain(outcome)

    def _resolve_uncertain(self, outcome: HealOutcome) -> Locator:
        """AI 不确定时的兜底分支（决策 D6）。"""
        mode = self._cfg.on_uncertain
        if mode == "use_fallback" and self._fallback:
            return self._page.locator(self._fallback)
        if mode == "pause" and _interactive():
            input(f"[自愈] 定位器 {self._selector!r} 失败且无法自动修复，请人工处理后回车继续…")
            return self._page.locator(self._selector)
        raise HealingFailedError(self._failure_report(outcome))

    def _failure_report(self, outcome: HealOutcome) -> str:
        return (
            f"自愈失败：原定位器={self._selector!r}, 描述={self._description!r}, "
            f"根因={outcome.root_cause!r}, 最高置信度={outcome.confidence}, "
            f"备用定位器={self._fallback!r}, on_uncertain={self._cfg.on_uncertain!r}"
        )

    def wait_until_stable(
        self, timeout_ms: int = 10000, stable_ms: int = 300, poll_ms: int = 100
    ) -> None:
        """智能等待：等元素可见且位置/尺寸稳定（可选增强，POM 显式调用）。"""
        _wait_until_stable(self._locator, timeout_ms, stable_ms, poll_ms)


class HealingPage:
    """带自愈能力的 Page 代理（插件层）。

    仅扩展 locator()（增加 description / fallback 两个自愈增强参数），其余 API 全部透传。
    POM 面向 Page 接口编写，开关自愈都能跑；enabled=False 时行为与原生 Page 一致。
    """

    def __init__(
        self,
        page: Page,
        settings: Settings,
        *,
        knowledge: KnowledgeBackend | None = None,
        reporter: HealingReporter | None = None,
        enabled_override: bool | None = None,
    ):
        self._page = page
        self._settings = settings
        self._enabled = settings.healing.enabled if enabled_override is None else enabled_override
        self._reporter = reporter or HealingReporter()
        if self._enabled:
            # 仅开启时构建知识库 / 闭环 / 弹窗处理（关闭时零开销、等同原生 Page，不产生文件副作用）
            self._knowledge = knowledge or build_knowledge_store(settings)
            self._orchestrator = SelfHealOrchestrator(page, settings, self._knowledge, self._reporter)
            self._popup_guard = PopupGuard(page, self._knowledge)
        else:
            self._knowledge = knowledge
            self._orchestrator = None
            self._popup_guard = None

    @property
    def healing_enabled(self) -> bool:
        return self._enabled

    @property
    def reporter(self) -> HealingReporter:
        return self._reporter

    def locator(
        self,
        selector: str,
        *,
        description: str | None = None,
        fallback: str | None = None,
        **kwargs: Any,
    ) -> HealingLocator:
        """返回自愈定位器。description / fallback 为自愈增强参数（关闭时被接受但忽略）。"""
        raw = self._page.locator(selector, **kwargs)
        return HealingLocator(
            raw,
            self._page,
            selector,
            self._settings.healing,
            self._orchestrator,
            fallback=fallback,
            description=description,
            enabled=self._enabled,
            popup_guard=self._popup_guard,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._page, name)
