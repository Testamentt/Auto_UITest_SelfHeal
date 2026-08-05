"""自愈闭环编排器 —— 本项目最核心的链路。

流程：现场采集 →（知识库优先）→ 智能诊断 → 多策略修复 → 验证与沉淀 → 报告审计。
Playwright 仅作类型依赖（TYPE_CHECKING），核心逻辑可在无浏览器环境下被单元测试覆盖。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from selfheal.agent.diagnose import Diagnoser, FailureContext
from selfheal.agent.diagnose_llm import LLMDiagnoser
from selfheal.agent.dom import (
    ElementContext,
    compute_page_fingerprint,
    compute_repair_key,
    dom_fingerprint,
    extract_element_context,
)
from selfheal.agent.strategies import HeuristicStrategy, SemanticStrategy, VisualStrategy
from selfheal.agent.strategies.base import RepairCandidate
from selfheal.collect.collector import Scene, SceneCollector
from selfheal.config import Settings
from selfheal.knowledge.base import KnowledgeBackend
from selfheal.knowledge.factory import build_knowledge_store
from selfheal.knowledge.schema import RepairCase
from selfheal.llm import get_embedding_for_settings
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
    """一次自愈的最终结果。

    - proposed_selector（T14 dry_run）: 仅报告模式下建议采用的定位器（不实际应用）。
    """

    success: bool
    new_selector: str | None = None
    confidence: float = 0.0
    strategy: str | None = None
    root_cause: str | None = None
    proposed_selector: str | None = None


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
        # Embedding 可用才构建；不可用则 None（L1/L3 语义检索被跳过，零回归）
        self._embedding = get_embedding_for_settings(settings)
        # Phase 5 A：失败上下文缓存（一次提取，L1/L3/_persist 复用，避免全量 DOM 解析多次）
        self._failure_context_cache: dict[str, ElementContext] = {}
        # 单次 run() 的页面指纹与元素上下文（策略构建 / 沉淀复用）
        self._page_fingerprint = ""
        self._current_ctx: ElementContext | None = None

    def run(
        self,
        original_selector: str,
        description: str | None = None,
        failure: FailureContext | None = None,
        use_knowledge: bool = True,
    ) -> HealOutcome:
        scene = self._collector.capture()
        # T13：高风险页豁免——URL 命中 exclude_url_patterns 则不触发自愈（只报告，不动作）
        if self._is_excluded(scene.url):
            return HealOutcome(success=False, root_cause="high_risk_page_excluded")

        fingerprint = dom_fingerprint(scene.dom_snapshot)
        # Phase 5 A：页面指纹 + 失败元素上下文（提取一次，L1/L3/_persist 复用）
        page_fingerprint = compute_page_fingerprint(scene.url, scene.dom_snapshot)
        element_context = self._element_context(scene, original_selector, description)
        self._page_fingerprint = page_fingerprint
        self._current_ctx = element_context

        dry_run = self._settings.healing.dry_run
        # 1) 知识库优先：命中已有修复案例则直接复用（降本增效）。
        #    use_knowledge=False 用于二次自愈（跳过缓存，防重复命中同一失效项）。
        #    dry_run 时不走知识早返回——只报告建议、不"应用"缓存修复。
        if (
            use_knowledge
            and not dry_run
            and self._settings.healing.knowledge_first
            and (
                cached := self._lookup_knowledge(
                    scene, original_selector, fingerprint, page_fingerprint, element_context
                )
            )
        ):
            self._record(original_selector, cached)  # 知识复用也记录（供审计/指标）
            return cached

        # 2) 智能诊断根因（透传失败上下文，供 LLM 诊断参考）
        root_cause = self._diagnoser.diagnose(scene, original_selector, failure)

        # 3) 按配置顺序尝试多策略，取置信度最高者
        best = self._best_candidate(scene, original_selector, description)
        threshold = self._settings.healing.confidence_threshold
        if best and best.confidence >= threshold:
            if dry_run:
                # T14 dry-run：只生成修复建议、不实际应用（不持久化、不换定位器重试），供人审
                self._emit_proposal(original_selector, best, scene, root_cause)
                return HealOutcome(
                    success=False,
                    confidence=best.confidence,
                    strategy=best.strategy,
                    root_cause="dry_run",
                    proposed_selector=best.selector,
                )
            outcome = HealOutcome(
                success=True,
                new_selector=best.selector,
                confidence=best.confidence,
                strategy=best.strategy,
                root_cause=root_cause,
            )
            self._persist(scene, original_selector, outcome, fingerprint, page_fingerprint, element_context)  # 4) 验证成功后沉淀
            return outcome

        return HealOutcome(success=False, root_cause=root_cause)

    # --- 内部步骤 ---

    def _is_excluded(self, url: str) -> bool:
        """T13：URL 是否命中高风险页豁免模式（glob 匹配；无模式 / 空 URL 恒 False）。"""
        patterns = self._settings.healing.exclude_url_patterns
        if not patterns or not url:
            return False
        import fnmatch

        return any(fnmatch.fnmatch(url, p) for p in patterns)

    def _emit_proposal(self, original_selector: str, best: RepairCandidate, scene: Scene, root_cause: str | None) -> None:
        """T14/T15：把「原→新」建议写出（fix-proposals），供人审后手动采纳。best-effort。"""
        try:
            from selfheal.reporting.fix_proposals import write_fix_proposal

            write_fix_proposal(
                original_selector=original_selector,
                new_selector=best.selector,
                strategy=best.strategy,
                confidence=best.confidence,
                page_url=scene.url,
                root_cause=root_cause,
                verified=False,
            )
        except Exception:  # noqa: BLE001 - 建议写出失败不阻塞 dry_run
            logger.warning("dry-run 建议写出失败", exc_info=True)

    def _element_context(self, scene: Scene, selector: str, description: str | None) -> ElementContext:
        """提取失败元素上下文；命中缓存直接复用（一次提取原则，避免重复 DOM 解析）。"""
        if selector in self._failure_context_cache:
            return self._failure_context_cache[selector]
        ctx = extract_element_context(self._page, scene.dom_snapshot if scene else None, selector, description)
        self._failure_context_cache[selector] = ctx
        return ctx

    def _bump_hit(self, case) -> None:
        """L1 命中递增（热度 / 衰减用）；失败不阻塞硬短路。"""
        try:
            self._knowledge.bump_hit(case.repair_key)
        except Exception:  # noqa: BLE001 - 命中计数失败不阻塞
            logger.warning("知识命中计数失败", exc_info=True)

    def _lookup_knowledge(
        self,
        scene: Scene,
        selector: str,
        dom_fingerprint: str | None = None,
        page_fingerprint: str = "",
        element_context: ElementContext | None = None,
    ) -> HealOutcome | None:
        # L1（Phase 5 A）：确定性 repair_key 精确命中 → 硬短路直接返回
        if element_context is not None and not element_context.is_empty:
            repair_key = compute_repair_key(page_fingerprint, element_context.text, element_context.tag_path)
            case = self._knowledge.find_by_repair_key(repair_key)
            # #10：读取时校验置信度边界（None/越界按未命中），防脏数据进入复用；再验证缓存选择器可用
            if (
                case is not None
                and case.confidence is not None
                and 0.0 <= case.confidence <= 1.0
                and case.confidence >= self._settings.healing.confidence_threshold
                and self._selector_exists(case.new_selector)
            ):
                self._bump_hit(case)
                return HealOutcome(
                    success=True,
                    new_selector=case.new_selector,
                    confidence=case.confidence,
                    strategy="knowledge",
                    root_cause="cached_l1",
                )
        # 旧式精确 selector + 指纹检索（向后兼容）
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
        """实例化策略；语义/视觉策略需透传 LLM/VLM client（无 client 时其内部返回 None 被跳过）。

        Phase 5 A：语义策略注入 knowledge/embedding/页面指纹/元素上下文，升级为 L3 向量检索。
        T17：client 包一层计数代理，真实 LLM/VLM 调用计入 reporter.stats（估算成本）。
        """
        if strategy_cls is SemanticStrategy:
            return strategy_cls(
                client=self._counting_client(self._llm_client, "llm_calls"),
                knowledge=self._knowledge if self._embedding is not None else None,
                embedding=self._embedding,
                page_fingerprint=self._page_fingerprint,
                element_context=self._current_ctx,
            )
        if strategy_cls is VisualStrategy:
            return strategy_cls(client=self._counting_client(self._vision_client, "vlm_calls"))
        return strategy_cls()

    def _counting_client(self, client, key: str):
        """T17：计数代理——每次真实模型调用（chat/analyze_image/complete）递增 reporter.stats[key]。

        语义向量检索（L3）命中时不走 LLM，调用不计数 → 成本看板如实反映"策略短路省下的调用"。
        """
        if client is None:
            return None
        stats = self._reporter.stats

        def _bump() -> None:
            stats[key] = stats.get(key, 0) + 1

        class _CountingProxy:
            def chat(self, messages, **kwargs):
                _bump()
                return client.chat(messages, **kwargs)

            def analyze_image(self, image, prompt, **kwargs):
                _bump()
                return client.analyze_image(image, prompt, **kwargs)

            def complete(self, prompt, **kwargs):
                _bump()
                return client.complete(prompt, **kwargs)

        return _CountingProxy()

    def _persist(
        self,
        scene: Scene,
        original_selector: str,
        outcome: HealOutcome,
        dom_fingerprint: str | None = None,
        page_fingerprint: str = "",
        element_context: ElementContext | None = None,
    ) -> None:
        """修复成功后：写入知识库（供后续命中复用）+ 记录审计（供报告展示）。

        Phase 5 A：同时沉淀 page_fingerprint / repair_key / embedding / embedding_version / created_at，
        使 L1 精确命中与 L3 语义检索可用。
        """
        # 沉淀失败不应影响已成功的自愈结果；但按 R4 不能静默吞错，记 warning。
        try:
            repair_key: str | None = None
            embedding: bytes | None = None
            embedding_version: str | None = None
            if element_context is not None and not element_context.is_empty and self._embedding is not None:
                repair_key = compute_repair_key(
                    page_fingerprint, element_context.text, element_context.tag_path
                )
                embedding = self._embedding.embed(element_context.query_text)
                embedding_version = self._embedding.embedding_version
            self._knowledge.add_repair(
                RepairCase(
                    original_selector=original_selector,
                    new_selector=outcome.new_selector or "",
                    strategy=outcome.strategy or "",
                    confidence=outcome.confidence,
                    page_url=scene.url,
                    dom_fingerprint=dom_fingerprint,
                    page_fingerprint=page_fingerprint,
                    repair_key=repair_key,
                    embedding=embedding,
                    embedding_version=embedding_version,
                    created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                )
            )
            # T15：修复写回代码的人审清单——生成「原→新」PR 化建议（不自动改库），供人确认后合入
            if self._settings.healing.fix_proposals:
                try:
                    from selfheal.reporting.fix_proposals import write_fix_proposal

                    write_fix_proposal(
                        original_selector=original_selector,
                        new_selector=outcome.new_selector or "",
                        strategy=outcome.strategy or "",
                        confidence=outcome.confidence,
                        page_url=scene.url,
                        root_cause=outcome.root_cause,
                        verified=not self._selector_exists(original_selector),
                    )
                except Exception:  # noqa: BLE001 - 建议写出失败不影响已成功的自愈
                    logger.warning("修复建议写出失败", exc_info=True)
        except Exception:  # noqa: BLE001 - 沉淀失败不影响已成功的自愈
            logger.warning("知识沉淀失败（自愈本身已成功）", exc_info=True)
        self._record(original_selector, outcome)

    def _record(self, original_selector: str, outcome: HealOutcome) -> None:
        """记录一次成功自愈（含知识复用），供审计与指标统计（T3）。审计失败不影响主流程。"""
        try:
            # T16：真自愈 vs flaky——修复后原定位器仍失效 → 真修复（verified=True）；
            # 原定位器已恢复 → 失败是瞬时的（flaky，verified=False），勿把偶发绿计为修复成功。
            verified = not self._selector_exists(original_selector)
            self._reporter.record(
                HealingRecord(
                    original_selector=original_selector,
                    new_selector=outcome.new_selector,
                    strategy=outcome.strategy,
                    confidence=outcome.confidence,
                    root_cause=outcome.root_cause,
                    success=True,
                    verified=verified,
                )
            )
        except Exception:  # noqa: BLE001 - 审计失败不影响已成功的自愈
            logger.warning("自愈审计记录失败", exc_info=True)
