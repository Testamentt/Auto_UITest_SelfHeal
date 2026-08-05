"""自愈管线数据模型 + 上下文组装器（A2 + A1）。

- HealOutcome / HealingContext / FixProposal：管线共享的不可变 DTO。
  独立成模块避免 orchestrator ↔ 协作者（fix_generator / persistence）循环导入。
- ContextAssembler：Pipeline 拆分的第一个协作者——专职「采集 → 高风险页豁免 →
  指纹 → 元素上下文三级回退」，产出 HealingContext。
- selector_exists：缓存验证 / verified 判定共用的页面查询助手。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from selfheal.agent.dom import (
    ElementContext,
    compute_page_fingerprint,
    dom_fingerprint,
    extract_element_context,
)
from selfheal.agent.strategies.base import RepairCandidate
from selfheal.collect.collector import Scene, SceneCollector
from selfheal.config import Settings

if TYPE_CHECKING:  # 仅类型检查时导入，避免运行期强依赖 playwright
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)


@dataclass
class HealOutcome:
    """一次自愈的最终结果。

    - proposed_selector（T14 dry_run）: 仅报告模式下建议采用的定位器（不实际应用）。
    - attempt_id（B1）: 策略修复成功后生成的暂存标识；引擎层重试成功后以此 commit_pending。
    """

    success: bool
    new_selector: str | None = None
    confidence: float = 0.0
    strategy: str | None = None
    root_cause: str | None = None
    proposed_selector: str | None = None
    attempt_id: str | None = None


@dataclass(frozen=True)
class HealingContext:
    """一次自愈闭环的运行上下文（A2）：把散落的现场/指纹/元素上下文收拢为不可变对象。

    由 ContextAssembler 组装，供知识库检索、诊断、策略、沉淀复用，
    替代 orchestrator 的实例状态（_page_fingerprint / _current_ctx）与长参数列表。
    """

    scene: Scene
    original_selector: str
    description: str | None
    dom_fingerprint: str | None
    page_fingerprint: str
    excluded: bool = False  # T13 高风险页豁免命中
    element_context: ElementContext | None = None


@dataclass
class FixProposal:
    """FixGenerator 的产出：一次闭环中「知识缓存命中 / 最佳候选 / 根因」的中间结果。"""

    cached_outcome: HealOutcome | None = None
    best: RepairCandidate | None = None
    root_cause: str | None = None


def selector_exists(page: Any, selector: str) -> bool:
    """校验选择器能否在当前页面定位到元素（缓存验证 / T16 verified 共用）；无 page 视为存在。"""
    if page is None:
        return True
    try:
        return page.locator(selector).count() > 0
    except Exception:  # noqa: BLE001 - 读取失败按"不存在"处理
        return False


class ContextAssembler:
    """A1 协作者：专职组装运行上下文（采集 → 豁免 → 指纹 → 元素上下文）。"""

    def __init__(self, page: Page | None, settings: Settings):
        self._page = page
        self._settings = settings
        self._collector = SceneCollector(page)
        # 失败上下文缓存（一次提取，L1/L3/persist 复用，避免全量 DOM 解析多次）
        self._failure_context_cache: dict[str, ElementContext] = {}

    def assemble(self, selector: str, description: str | None = None) -> HealingContext:
        """采集当前现场并组装上下文；excluded 标记 T13 高风险页豁免命中。"""
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
        """T13：URL 是否命中高风险页豁免模式（glob 匹配；无模式 / 空 URL 恒 False）。"""
        patterns = self._settings.healing.exclude_url_patterns
        if not patterns or not url:
            return False
        import fnmatch

        return any(fnmatch.fnmatch(url, p) for p in patterns)

    def _element_context(self, scene: Scene, selector: str, description: str | None) -> ElementContext:
        """提取失败元素上下文；命中缓存直接复用（一次提取原则，避免重复 DOM 解析）。"""
        if selector in self._failure_context_cache:
            return self._failure_context_cache[selector]
        ctx = extract_element_context(self._page, scene.dom_snapshot if scene else None, selector, description)
        self._failure_context_cache[selector] = ctx
        return ctx
