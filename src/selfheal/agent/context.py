# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""自愈管线数据模型 + 上下文组装器。

- HealOutcome / HealingContext / FixProposal：管线共享的不可变 DTO。
  独立成模块避免 orchestrator ↔ 协作者（fix_generator / persistence）循环导入。
  `HealOutcome` 的定义已上移到中立 DTO 层 `selfheal.contracts`（engine 也要构造它，
  DTO 留在 agent 会让下层反向依赖上层，见 docs/decisions.md D19）；本模块保留 re-export，
  既有 `from selfheal.agent.context import HealOutcome` 继续可用（零破坏）。
- ContextAssembler：Pipeline 拆分的第一个协作者——专职「采集 → 高风险页豁免 →
  指纹 → 元素上下文三级回退」，产出 HealingContext；同时持有采集器生命周期
  （close() 摘除监听）。
- selector_exists / selector_actionable：候选定位器的**唯一性**（strict-mode 硬底线）与
  **可交互性**（按动作区分的择优信号）校验。
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from selfheal.agent.strategies.base import RepairCandidate
from selfheal.collect.collector import Scene, SceneCollector
from selfheal.config import Settings
from selfheal.contracts import HealOutcome, TraceActiveProvider
from selfheal.dom import (
    ElementContext,
    compute_page_fingerprint,
    dom_fingerprint,
    extract_element_context,
)

if TYPE_CHECKING:  # 仅类型检查时导入，避免运行期强依赖 playwright
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)

__all__ = [
    "ContextAssembler",
    "FixProposal",
    "HealOutcome",
    "HealingContext",
    "action_requires_actionability",
    "selector_actionable",
    "selector_exists",
]


@dataclass(frozen=True)
class HealingContext:
    """一次自愈闭环的运行上下文：把散落的现场/指纹/元素上下文收拢为不可变对象。

    由 ContextAssembler 组装，供知识库检索、诊断、策略、沉淀复用，
    替代 orchestrator 的实例状态（_page_fingerprint / _current_ctx）与长参数列表。
    """

    scene: Scene
    original_selector: str
    description: str | None
    dom_fingerprint: str | None
    page_fingerprint: str
    excluded: bool = False  # 高风险页豁免命中
    element_context: ElementContext | None = None


@dataclass
class FixProposal:
    """FixGenerator 的产出：一次闭环中「知识缓存命中 / 最佳候选 / 根因」的中间结果。

    rejected：逐候选过滤后被拒的最优候选（定位器不可用 / 未达自身策略阈值）。它**只**用于
    失败路径的人审建议出口，绝不进入采纳与落库链路；保留它是为了不让"为什么没修"随过滤丢失。
    """

    cached_outcome: HealOutcome | None = None
    best: RepairCandidate | None = None
    root_cause: str | None = None
    rejected: RepairCandidate | None = None


# 少数动作在 Playwright 里**不要求元素可见**：标准上传写法就是隐藏 `<input type=file>`，
# `set_input_files` 对 display:none 的 input 同样可用。其余动作（click / fill / ...）由 Playwright
# 自动等待 actionable 后再动作。据此决定候选校验要不要把"当前不可交互"当作早接受门槛 / 同分 tie-break。
_ACTIONS_WITHOUT_ACTIONABILITY = frozenset({"set_input_files"})


def action_requires_actionability(action: str | None) -> bool:
    """该动作是否要求候选元素**当前可交互**。

    None（未知来源，如脚本直调 `run()`）→ 不要求，回退既有行为；
    豁免集合外的动作（含未来新增的 HEALABLE 动作）→ 要求可交互（保守）。
    """
    return action is not None and action not in _ACTIONS_WITHOUT_ACTIONABILITY


def _probe_bool(probe: Any) -> bool:
    """调用可选的页面探测方法：方法不存在 → 视为通过；存在但抛异常/返回假 → 不通过。

    Playwright 的 `Locator` 一定实现 `is_visible` / `is_enabled`；无浏览器单测里的简易替身
    可能只实现 `count()`，此时按"可交互"处理（否则替身无法表达既有契约）。生产路径
    永远走真实探测——替身缺探测能力不等于生产缺。
    """
    if not callable(probe):
        return True
    try:
        return bool(probe())
    except Exception:  # noqa: BLE001 - 探测失败按"不可交互"处理（不阻断自愈主流程）
        return False


def selector_exists(page: Any, selector: str) -> bool:
    """校验选择器是否**唯一命中**（`count() == 1`）——候选可用性的硬底线（strict mode 安全）。

    只判 `count() > 0` 不够：两个同名按钮时启发式产出的 `text="提交"` 会被
    当成"可用的修复"采纳，重试 `click()` 抛 Playwright strict-mode violation（**非 TimeoutError**，
    不触发自愈，直接冒泡成另一个异常），脏案例还会经 stage → commit 落库供后续复用——能骗过
    存在性检查的假修复就是知识库污染源。

    可交互性**不在这里判**：它取决于动作（`set_input_files` 对隐藏 input 同样可用）且
    Playwright 会自动等待，硬拒会把"暂不可见但稍后可见"的元素与隐藏上传框一起误杀；
    需要时用 `selector_actionable()`（作早接受门槛与同分 tie-break 信号，见 fix_generator）。
    - 无 page → 视为唯一可用（纯逻辑单测与知识查询不持有页面的既有契约）。
    - 页面变化 / 定位器解析失败 → 按不可用处理（失败安全，不阻断自愈主流程）。
    """
    if page is None:
        return True
    try:
        return page.locator(selector).count() == 1
    except Exception:  # noqa: BLE001 - 页面/定位器不可用按"不可用"处理
        return False


def selector_actionable(page: Any, selector: str) -> bool:
    """命中元素**当前是否可交互**（visible 且 enabled）——早接受门槛 / 同分 tie-break 信号，不淘汰候选。

    - 先判唯一命中：多命中时 `is_visible()` 自身也会抛 strict-mode violation；
    - 无 page / 简易替身缺探测方法 → 视为可交互（见 `_probe_bool` 契约）。
    """
    if page is None:
        return True
    try:
        locator = page.locator(selector)
        if locator.count() != 1:
            return False
        # `.first` 取唯一命中元素；简易替身没有该属性时用 locator 自身（此时语义已足够）
        target = getattr(locator, "first", locator)
        if not _probe_bool(getattr(target, "is_visible", None)):
            return False
        return _probe_bool(getattr(target, "is_enabled", None))
    except Exception:  # noqa: BLE001 - 探测失败按"不可交互"处理
        return False


class ContextAssembler:
    """专职组装运行上下文（采集 → 豁免 → 指纹 → 元素上下文）。

    同时持有采集器的**生命周期**：close() 摘除挂在页面上的网络监听，
    由 SelfHealOrchestrator.close() 级联调用。
    """

    def __init__(
        self,
        page: Page | None,
        settings: Settings,
        trace_active: TraceActiveProvider | None = None,
    ):
        self._page = page
        self._settings = settings
        # 现场内联 trace：录制状态由外层显式注入（provider 读取 CLI 覆盖 / 配置），
        # 未注入（None）时才由采集器回退 tracing.stop() 异常探测（Playwright 版本相关的旧行为）。
        self._collector = SceneCollector(
            page, settings.browser.trace_dir, trace_active=trace_active
        )
        # 失败上下文缓存（一次提取，知识库检索 / 沉淀复用，避免全量 DOM 解析多次）。
        # 键 = (url, selector)：SPA 多页面流程同 selector 不同元素不误用；
        # OrderedDict 做 LRU 上限 _CONTEXT_CACHE_LIMIT，防长流程内存膨胀。
        self._failure_context_cache: OrderedDict[tuple[str, str], ElementContext] = OrderedDict()
        self._context_cache_limit = 256

    def close(self) -> None:
        """生命周期收口：摘除采集器挂在页面上的网络监听（幂等）。

        链路：`HealingPage.close()` → `SelfHealOrchestrator.close()` → 本方法。
        收口后页面不再被监听，同页重建采集器不会因旧实例的挂载而重复计数；
        已采集到的网络日志仍在最后一次 capture() 的结果里带出（证据不丢）。
        """
        self._collector.detach()

    def assemble(self, selector: str, description: str | None = None) -> HealingContext:
        """采集当前现场并组装上下文；excluded 标记高风险页豁免命中。"""
        scene = self._collector.capture()
        return HealingContext(
            scene=scene,
            original_selector=selector,
            description=description,
            excluded=self._is_excluded(scene.url),
            dom_fingerprint=dom_fingerprint(scene.dom_snapshot),
            page_fingerprint=compute_page_fingerprint(scene.url, scene.dom_snapshot),
            element_context=self._element_context(scene, selector, description),
        )

    def _is_excluded(self, url: str) -> bool:
        """URL 是否命中高风险页豁免模式（glob 匹配；无模式 / 空 URL 恒 False）。"""
        patterns = self._settings.healing.exclude_url_patterns
        if not patterns or not url:
            return False
        import fnmatch

        return any(fnmatch.fnmatch(url, p) for p in patterns)

    def _element_context(
        self, scene: Scene, selector: str, description: str | None
    ) -> ElementContext:
        """提取失败元素上下文；命中缓存直接复用（一次提取原则，避免重复 DOM 解析）。

        键含 URL：同 selector 在不同页面（SPA 多流程）不误用旧上下文。
        """
        key = (scene.url, selector)
        cached = self._failure_context_cache.get(key)
        if cached is not None:
            return cached
        ctx = extract_element_context(
            self._page, scene.dom_snapshot if scene else None, selector, description
        )
        self._failure_context_cache[key] = ctx
        self._failure_context_cache.move_to_end(key)  # LRU：最近使用排到尾部
        if len(self._failure_context_cache) > self._context_cache_limit:
            self._failure_context_cache.popitem(last=False)  # 淘汰最久未用
        return ctx
