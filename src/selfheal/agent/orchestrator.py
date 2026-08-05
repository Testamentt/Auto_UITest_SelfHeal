"""自愈闭环编排器 —— 本项目最核心的链路。

流程：现场采集 →（知识库优先）→ 智能诊断 → 多策略修复 → 验证与沉淀 → 报告审计。
Playwright 仅作类型依赖（TYPE_CHECKING），核心逻辑可在无浏览器环境下被单元测试覆盖。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from selfheal.agent.diagnose import Diagnoser, FailureContext
from selfheal.agent.diagnose_llm import LLMDiagnoser
from selfheal.agent.dom import dom_fingerprint
from selfheal.agent.strategies import HeuristicStrategy, SemanticStrategy, VisualStrategy
from selfheal.agent.strategies.base import RepairCandidate
from selfheal.collect.collector import Scene, SceneCollector
from selfheal.config import Settings
from selfheal.knowledge.base import KnowledgeBackend
from selfheal.knowledge.factory import build_knowledge_store
from selfheal.knowledge.schema import RepairCase
from selfheal.llm.base import LLMClient, VisionClient
from selfheal.llm.factory import get_llm_for_settings, get_vision_for_settings
from selfheal.reporting.hooks import HealingRecord, HealingReporter

if TYPE_CHECKING:  # 仅类型检查时导入，避免运行期强依赖 playwright
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)

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
        page: Page | None,
        settings: Settings,
        knowledge: KnowledgeBackend | None = None,
        reporter: HealingReporter | None = None,
        llm_client: LLMClient | None = None,
        vision_client: VisionClient | None = None,
    ):
        self._page = page
        self._settings = settings
        self._collector = SceneCollector(page)
        self._knowledge = knowledge or build_knowledge_store(settings)
        self._reporter = reporter or HealingReporter()
        # LLM 可用才构建；缺省时按配置判定，不可用则 None（降级回规则式 / 跳过语义策略）
        self._llm_client = llm_client if llm_client is not None else get_llm_for_settings(settings)
        self._diagnoser = LLMDiagnoser(self._llm_client) if self._llm_client else Diagnoser()
        # VLM 可用才构建；不可用则 None（视觉策略返回 None 被跳过）
        self._vision_client = (
            vision_client if vision_client is not None else get_vision_for_settings(settings)
        )

    def run(
        self,
        original_selector: str,
        description: str | None = None,
        failure: FailureContext | None = None,
        use_knowledge: bool = True,
    ) -> HealOutcome:
        scene = self._collector.capture()
        fingerprint = dom_fingerprint(scene.dom_snapshot)

        # 1) 知识库优先：命中已有修复案例则直接复用（降本增效）。
        #    use_knowledge=False 用于二次自愈（跳过缓存，防重复命中同一失效项）。
        if use_knowledge and self._settings.healing.knowledge_first and (
            cached := self._lookup_knowledge(scene, original_selector, fingerprint)
        ):
            self._record(original_selector, cached)  # 知识复用也记录（供审计/指标）
            return cached

        # 2) 智能诊断根因（透传失败上下文，供 LLM 诊断参考）
        root_cause = self._diagnoser.diagnose(scene, original_selector, failure)

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
            self._persist(scene, original_selector, outcome, fingerprint)  # 4) 验证成功后沉淀
            return outcome

        return HealOutcome(success=False, root_cause=root_cause)

    # --- 内部步骤 ---

    def _lookup_knowledge(
        self, scene: Scene, selector: str, dom_fingerprint: str | None = None
    ) -> HealOutcome | None:
        case = self._knowledge.find_repair(selector, dom_fingerprint)
        conf = case.confidence if case else None
        # #10：读取时校验置信度边界（None/越界按未命中），防脏数据进入复用
        if conf is None or not (0.0 <= conf <= 1.0) or conf < self._settings.healing.confidence_threshold:
            return None
        # 缓存验证（T4）：缓存的新选择器须仍可在当前页面定位，否则视为失效、转策略重修
        if not self._selector_exists(case.new_selector):
            return None
        return HealOutcome(
            success=True,
            new_selector=case.new_selector,
            confidence=case.confidence,
            strategy="knowledge",
            root_cause="cached",
        )

    def _selector_exists(self, selector: str) -> bool:
        """校验选择器能否在当前页面定位到元素（缓存验证）。无 page 时视为存在（纯逻辑场景不校验）。"""
        if self._page is None:
            return True
        try:
            return self._page.locator(selector).count() > 0
        except Exception:  # noqa: BLE001 - 读取失败按"不存在"处理
            return False

    def _best_candidate(
        self, scene: Scene, selector: str, description: str | None
    ) -> RepairCandidate | None:
        """按 strategy_order 逐个尝试策略取最优；置信度达"早接受"阈值即短路（T1，省 LLM/VLM）。"""
        best: RepairCandidate | None = None
        early_accept = self._settings.healing.early_accept_threshold
        for name in self._settings.healing.strategy_order:
            strategy_cls = _STRATEGY_REGISTRY.get(name)
            if strategy_cls is None:
                # #8：未知策略名不再静默跳过，记 warning 便于排查配置笔误
                logger.warning("strategy_order 含未知策略名 %r，已跳过（可用: %s）", name, list(_STRATEGY_REGISTRY))
                continue
            candidate = self._build_strategy(strategy_cls).repair(scene, selector, description)
            if candidate is None:
                continue
            # 短路：已达"早接受"阈值，不再尝试后续（更贵的）策略
            if candidate.confidence >= early_accept:
                return candidate
            if best is None or candidate.confidence > best.confidence:
                best = candidate
        return best

    def _build_strategy(self, strategy_cls: type) -> object:
        """实例化策略；语义/视觉策略需透传 LLM/VLM client（无 client 时其内部返回 None 被跳过）。"""
        if strategy_cls is SemanticStrategy:
            return strategy_cls(client=self._llm_client)
        if strategy_cls is VisualStrategy:
            return strategy_cls(client=self._vision_client)
        return strategy_cls()

    def _persist(
        self,
        scene: Scene,
        original_selector: str,
        outcome: HealOutcome,
        dom_fingerprint: str | None = None,
    ) -> None:
        """修复成功后：写入知识库（供后续命中复用）+ 记录审计（供报告展示）。"""
        # 沉淀失败不应影响已成功的自愈结果；但按 R4 不能静默吞错，记 warning。
        try:
            self._knowledge.add_repair(
                RepairCase(
                    original_selector=original_selector,
                    new_selector=outcome.new_selector or "",
                    strategy=outcome.strategy or "",
                    confidence=outcome.confidence,
                    page_url=scene.url,
                    dom_fingerprint=dom_fingerprint,
                )
            )
        except Exception:  # noqa: BLE001 - 沉淀失败不影响已成功的自愈
            logger.warning("知识沉淀失败（自愈本身已成功）", exc_info=True)
        self._record(original_selector, outcome)

    def _record(self, original_selector: str, outcome: HealOutcome) -> None:
        """记录一次成功自愈（含知识复用），供审计与指标统计（T3）。审计失败不影响主流程。"""
        try:
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
        except Exception:  # noqa: BLE001 - 审计失败不影响已成功的自愈
            logger.warning("自愈审计记录失败", exc_info=True)
