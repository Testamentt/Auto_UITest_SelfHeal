"""自愈闭环编排器 —— 本项目最核心的链路。

流程：现场采集 →（知识库优先）→ 智能诊断 → 多策略修复 → 验证与沉淀 → 报告审计。
Playwright 仅作类型依赖（TYPE_CHECKING），核心逻辑可在无浏览器环境下被单元测试覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from selfheal.agent.diagnose import Diagnoser
from selfheal.agent.strategies import HeuristicStrategy, SemanticStrategy, VisualStrategy
from selfheal.agent.strategies.base import RepairCandidate
from selfheal.collect.collector import Scene, SceneCollector
from selfheal.config import Settings
from selfheal.knowledge.schema import RepairCase
from selfheal.knowledge.store import KnowledgeStore
from selfheal.reporting.hooks import HealingRecord, HealingReporter

if TYPE_CHECKING:  # 仅类型检查时导入，避免运行期强依赖 playwright
    from playwright.sync_api import Page

# 策略注册表：名字 → 策略类。新增策略在此登记即可被 orchestrator 调度。
_STRATEGY_REGISTRY: dict[str, type] = {
    "heuristic": HeuristicStrategy,
    "semantic": SemanticStrategy,
    "visual": VisualStrategy,
}


@dataclass
class HealOutcome:
    """一次自愈的最终结果。"""

    success: bool
    new_selector: str | None = None
    confidence: float = 0.0
    strategy: str | None = None
    root_cause: str | None = None


class SelfHealOrchestrator:
    """编排「感知-诊断-决策-修复」闭环。

    knowledge / reporter 可注入（默认自建），便于测试替换与跨用例复用知识库。
    """

    def __init__(
        self,
        page: "Page | None",
        settings: Settings,
        knowledge: KnowledgeStore | None = None,
        reporter: HealingReporter | None = None,
    ):
        self._page = page
        self._settings = settings
        self._collector = SceneCollector(page)
        self._diagnoser = Diagnoser()
        self._knowledge = knowledge or KnowledgeStore()
        self._reporter = reporter or HealingReporter()

    def run(self, original_selector: str, description: str | None = None) -> HealOutcome:
        scene = self._collector.capture()

        # 1) 知识库优先：命中已有修复案例则直接复用（降本增效）
        if self._settings.healing.knowledge_first:
            if cached := self._lookup_knowledge(scene, original_selector):
                return cached

        # 2) 智能诊断根因
        root_cause = self._diagnoser.diagnose(scene, original_selector)

        # 3) 按配置顺序尝试多策略，取置信度最高者
        best = self._best_candidate(scene, original_selector, description)
        threshold = self._settings.healing.confidence_threshold
        if best and best.confidence >= threshold:
            outcome = HealOutcome(
                success=True,
                new_selector=best.selector,
                confidence=best.confidence,
                strategy=best.strategy,
                root_cause=root_cause,
            )
            self._persist(scene, original_selector, outcome)  # 4) 验证成功后沉淀
            return outcome

        return HealOutcome(success=False, root_cause=root_cause)

    # --- 内部步骤 ---

    def _lookup_knowledge(self, scene: Scene, selector: str) -> HealOutcome | None:
        case = self._knowledge.find_repair(selector)
        if case and case.confidence >= self._settings.healing.confidence_threshold:
            return HealOutcome(
                success=True,
                new_selector=case.new_selector,
                confidence=case.confidence,
                strategy="knowledge",
                root_cause="cached",
            )
        return None

    def _best_candidate(
        self, scene: Scene, selector: str, description: str | None
    ) -> RepairCandidate | None:
        best: RepairCandidate | None = None
        for name in self._settings.healing.strategy_order:
            strategy_cls = _STRATEGY_REGISTRY.get(name)
            if strategy_cls is None:
                continue
            candidate = strategy_cls().repair(scene, selector, description)
            if candidate and (best is None or candidate.confidence > best.confidence):
                best = candidate
        return best

    def _persist(self, scene: Scene, original_selector: str, outcome: HealOutcome) -> None:
        """修复成功后：写入知识库（供后续命中复用）+ 记录审计（供报告展示）。"""
        self._knowledge.add_repair(
            RepairCase(
                original_selector=original_selector,
                new_selector=outcome.new_selector or "",
                strategy=outcome.strategy or "",
                confidence=outcome.confidence,
                page_url=scene.url,
            )
        )
        self._reporter.record(
            HealingRecord(
                original_selector=original_selector,
                new_selector=outcome.new_selector,
                strategy=outcome.strategy,
                confidence=outcome.confidence,
                root_cause=outcome.root_cause,
                success=True,
            )
        )
