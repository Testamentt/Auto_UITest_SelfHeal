"""自愈 Page 代理 —— 插件装配层（M10-②）。

`HealingPage` 是自愈插件的**组合根**：开启时构建知识库 → 编排器（orchestrator 及其协作者）
→ 弹窗处理，并把这些资源的所有权（owns 标记）与释放义务收口在 `close()`。
它同时是 `HealingLocator`（engine 原语）的装配方：把编排器、配置、弹窗处理器注入定位器。

**位置约束**：本模块属 plugin 层（可依赖 engine + agent + knowledge + reporting）。
此前它住在 `engine/healing_locator.py`，使 engine 在 import 期反向拉起 agent / knowledge /
reporting（评审 M10 分层倒置）；上移后 engine 只留原语。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from selfheal.agent.orchestrator import SelfHealOrchestrator
from selfheal.config import Settings
from selfheal.engine.healing_locator import HealingLocator, _safe_close
from selfheal.engine.popup_guard import PopupGuard
from selfheal.knowledge.base import KnowledgeBackend
from selfheal.knowledge.factory import build_knowledge_store
from selfheal.reporting.hooks import HealingReporter

if TYPE_CHECKING:  # 仅类型检查时导入，避免运行期强依赖 playwright
    from playwright.sync_api import Page

__all__ = ["HealingPage"]


class HealingPage:
    """带自愈能力的 Page 代理（插件层）。

    仅扩展 locator()（增加 description / fallback 两个自愈增强参数），其余 API 全部透传。
    POM 面向 Page 接口编写，开关自愈都能跑；enabled=False 时行为与原生 Page 一致。

    **自愈边界（L2：请按此预期使用，勿把"POM 无缝切换"读成"全部 API 都自愈"）**：
    - **会自愈**：自愈入口只有 `HealingPage.locator(sel, description=…, fallback=…)`，
      以及它返回的 HealingLocator 上的：① HEALABLE 动作（click / fill / hover / press 等）；
      ② 链式定位（`locator()` / `get_by_*` / `first` / `last` / `nth` / `filter`）——
      链式定位逐环包裹为 HealingLocator，自愈后在修复的根定位器上重放整条链。
    - **不自愈（原样透传原生 Page/Locator）**：其余全部 API。典型如
      ③ `page.get_by_role / get_by_text / get_by_label / …`（在 HealingPage 上直呼，
      返回原生 Locator）；④ `page.click(sel)` / `page.fill(sel, …)` 这类"选择器直调"动作；
      ⑤ `page.expect_*` 系列与模块级 `playwright.sync_api.expect(...)` 断言。
      这些调用定位失败即按原生超时抛出，不进入"失败→自愈→重试"闭环。
      需要自愈请改写成 `page.locator(sel).<action>()` 形式（含 get_by_*：写
      `page.locator(root).get_by_role(...)` 即作为链环被自愈）。
    - **判定口径**：返回值不是 HealingLocator 的 API 一律视为未受自愈保护。
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
        # H2（2026-10-08 审查）：自建的知识库由本实例负责关闭——此前把同一个对象交给
        # orchestrator 后，orchestrator 视其为"注入资源"（owns=False）而不关闭，
        # HealingPage.close() 也只关 orchestrator → SQLite 连接随每个页面实例泄漏。
        self._owns_knowledge = False
        if self._enabled:
            # 仅开启时构建知识库 / 闭环 / 弹窗处理（关闭时零开销、等同原生 Page，不产生文件副作用）
            self._knowledge = knowledge or build_knowledge_store(settings)
            self._owns_knowledge = knowledge is None
            self._orchestrator = SelfHealOrchestrator(
                page, settings, self._knowledge, self._reporter
            )
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

    def close(self) -> None:
        """释放自愈资源（orchestrator 自建的 LLM/VLM 客户端 + 本实例自建的知识库连接）。

        注入的 knowledge 由注入方负责；关闭后本实例不可再用于自愈。幂等。
        同时由 orchestrator 级联摘除采集器挂在页面上的网络监听（L3 生命周期收口）。
        """
        if self._orchestrator is not None:
            self._orchestrator.close()
        if self._owns_knowledge and self._knowledge is not None:
            _safe_close(self._knowledge)
            self._owns_knowledge = False

    def locator(
        self,
        selector: str,
        *,
        description: str | None = None,
        fallback: str | None = None,
        **kwargs: Any,
    ) -> HealingLocator:
        """返回自愈定位器。description / fallback 为自愈增强参数（关闭时被接受但忽略）。

        边界（L2）：自愈只覆盖本方法返回的 HealingLocator 及其链式派生对象（HEALABLE 动作
        + 链式定位）。`page.get_by_*`、`page.click(selector)`、`page.fill(selector, …)`、
        `page.expect_*` 与模块级 `expect(...)` 断言**均不自愈**（原样透传，原生超时直接
        抛出）；需要自愈请统一写成 `page.locator(sel).<action>()`。
        """
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
        # L1：与 HealingLocator 同源的代理递归风险——内部字段未就绪（构造中途 / 拷贝协议
        # 探测 `__setstate__` 等）时若写成 `self._page`，会再次进入 __getattr__ 无限递归。
        try:
            page = object.__getattribute__(self, "_page")
        except AttributeError:
            raise AttributeError(name) from None
        return getattr(page, name)
