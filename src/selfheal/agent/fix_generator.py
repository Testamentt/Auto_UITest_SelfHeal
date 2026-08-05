"""修复提案生成器（A1 协作者）—— 专职「知识库优先 → 诊断 → 策略链调度」。

产出 FixProposal（知识缓存命中 / 最佳候选 / 根因），由 PersistenceHandler 做阈值路由。
策略注册表由 orchestrator 注入（共享同一 dict，测试 monkeypatch 仍生效）。
"""

from __future__ import annotations

import logging

from selfheal.agent.context import FixProposal, HealingContext, HealOutcome, selector_exists
from selfheal.agent.dom import compute_repair_key
from selfheal.agent.strategies import SemanticStrategy, VisualStrategy
from selfheal.agent.strategies.base import RepairCandidate
from selfheal.config import Settings
from selfheal.knowledge.base import KnowledgeBackend

logger = logging.getLogger(__name__)


def counting_proxy(client, key: str, stats: dict):
    """T17：计数代理工厂——每次真实模型调用（chat/analyze_image/complete）递增 stats[key]。

    语义向量检索（L3）命中时不走 LLM，调用不计数 → 成本看板如实反映"策略短路省下的调用"。
    client 为 None 时返回 None（调用方优雅降级）。
    """
    if client is None:
        return None

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


class FixGenerator:
    """A1：专职生成修复提案（知识库优先 → 诊断 → 策略链调度 → LLM 深挖归因）。"""

    def __init__(
        self,
        settings: Settings,
        knowledge: KnowledgeBackend,
        reporter,
        rule_diagnoser,
        llm_diagnoser,
        llm_client,
        vision_client,
        embedding,
        page,
        strategy_registry: dict,
    ):
        self._settings = settings
        self._knowledge = knowledge
        self._reporter = reporter
        self._rule_diagnoser = rule_diagnoser
        self._llm_diagnoser = llm_diagnoser
        self._llm_client = llm_client
        self._vision_client = vision_client
        self._embedding = embedding
        self._page = page
        self._strategy_registry = strategy_registry

    def generate(
        self, context: HealingContext, failure=None, use_knowledge: bool = True
    ) -> FixProposal:
        """一次闭环的中间提案：知识命中短路 / 规则诊断 / 策略链 / LLM 归因。"""
        dry_run = self._settings.healing.dry_run
        # 1) 知识库优先：命中已有修复案例则直接复用（降本增效）。
        #    use_knowledge=False 用于二次自愈（跳过缓存）；dry_run 不走知识早返回。
        if (
            use_knowledge
            and not dry_run
            and self._settings.healing.knowledge_first
            and (cached := self.lookup_knowledge(context)) is not None
        ):
            return FixProposal(cached_outcome=cached)
        # 2) 规则式诊断（零成本恒跑）：成功路径的根因归因，不触发 LLM
        root_cause = self._rule_diagnoser.diagnose(context.scene, context.original_selector, failure)
        # 3) 策略链调度，取置信度最高者
        best = self.best_candidate(context)
        threshold = self._settings.healing.confidence_threshold
        if best is not None and best.confidence >= threshold:
            # C7：低置信度成功（[threshold, llm_diagnose_threshold)）也补 LLM 归因，
            # 丰富审计与人审清单；高置信成功直接用规则归因，省一次 LLM 调用。
            if (
                self._llm_diagnoser is not None
                and best.confidence < self._settings.healing.llm_diagnose_threshold
            ):
                root_cause = self._llm_diagnoser.diagnose(context.scene, context.original_selector, failure)
                self._bump_diag_count()
            return FixProposal(best=best, root_cause=root_cause)
        # 策略链失败（无候选或置信度不足）：LLM 深挖归因，供人审清单/失败报告（诊断后置，C3）
        if self._llm_diagnoser is not None:
            root_cause = self._llm_diagnoser.diagnose(context.scene, context.original_selector, failure)
            self._bump_diag_count()
        return FixProposal(best=best, root_cause=root_cause)

    def lookup_knowledge(self, context: HealingContext) -> HealOutcome | None:
        """知识库优先查询：L1 repair_key 精确命中硬短路；未中回退旧式 selector+指纹检索。"""
        # L1（Phase 5 A）：确定性 repair_key 精确命中 → 硬短路直接返回
        # v2：repair_key = md5(page_fingerprint|tag_path)，不含文本。
        # 仅当有结构上下文（tag_path 非空）才计算：静态兜底上下文 tag_path 为空，
        # 若照常计算会使同页所有静态失败折叠成同一键（碰撞、跨用例误命中）→ 改走旧式检索。
        element_context = context.element_context
        if element_context is not None and not element_context.is_empty and element_context.tag_path:
            repair_key = compute_repair_key(context.page_fingerprint, element_context.tag_path)
            case = self._knowledge.find_by_repair_key(repair_key)
            # #10：读取时校验置信度边界（None/越界按未命中），防脏数据进入复用；再验证缓存选择器可用
            if (
                case is not None
                and case.confidence is not None
                and 0.0 <= case.confidence <= 1.0
                and case.confidence >= self._settings.healing.confidence_threshold
                and selector_exists(self._page, case.new_selector)
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
        case = self._knowledge.find_repair(context.original_selector, context.dom_fingerprint)
        conf = case.confidence if case else None
        # #10：读取时校验置信度边界（None/越界按未命中），防脏数据进入复用
        if conf is None or not (0.0 <= conf <= 1.0) or conf < self._settings.healing.confidence_threshold:
            return None
        # 缓存验证（T4）：缓存的新选择器须仍可在当前页面定位，否则视为失效、转策略重修
        if not selector_exists(self._page, case.new_selector):
            return None
        return HealOutcome(
            success=True,
            new_selector=case.new_selector,
            confidence=case.confidence,
            strategy="knowledge",
            root_cause="cached",
        )

    def best_candidate(self, context: HealingContext) -> RepairCandidate | None:
        """按 strategy_order 逐个尝试策略取最优；置信度达"早接受"阈值即短路（T1，省 LLM/VLM）。"""
        best: RepairCandidate | None = None
        early_accept = self._settings.healing.early_accept_threshold
        for name in self._settings.healing.strategy_order:
            strategy_cls = self._strategy_registry.get(name)
            if strategy_cls is None:
                # #8：未知策略名不再静默跳过，记 warning 便于排查配置笔误
                logger.warning(
                    "strategy_order 含未知策略名 %r，已跳过（可用: %s）",
                    name,
                    list(self._strategy_registry),
                )
                continue
            candidate = self._build_strategy(strategy_cls, context).repair(
                context.scene, context.original_selector, context.description
            )
            if candidate is None:
                continue
            # 短路：已达"早接受"阈值，不再尝试后续（更贵的）策略
            if candidate.confidence >= early_accept:
                return candidate
            if best is None or candidate.confidence > best.confidence:
                best = candidate
        return best

    def _build_strategy(self, strategy_cls, context: HealingContext):
        """实例化策略；语义/视觉策略透传（计数代理包裹的）LLM/VLM client。

        Phase 5 A：语义策略注入 knowledge/embedding/页面指纹/元素上下文，升级为 L3 向量检索。
        T17：client 包一层计数代理，真实 LLM/VLM 调用计入 reporter.stats。
        """
        if strategy_cls is SemanticStrategy:
            return strategy_cls(
                client=self._counting_client(self._llm_client, "llm_calls"),
                knowledge=self._knowledge if self._embedding is not None else None,
                embedding=self._embedding,
                page_fingerprint=context.page_fingerprint,
                element_context=context.element_context,
            )
        if strategy_cls is VisualStrategy:
            return strategy_cls(client=self._counting_client(self._vision_client, "vlm_calls"))
        return strategy_cls()

    def _counting_client(self, client, key: str):
        """T17：计数代理（委托模块级 counting_proxy，与 orchestrator 共享同一实现）。"""
        return counting_proxy(client, key, self._reporter.stats)

    def _bump_hit(self, case) -> None:
        """L1 命中递增（热度 / 衰减用）；失败不阻塞硬短路。"""
        try:
            self._knowledge.bump_hit(case.repair_key)
        except Exception:  # noqa: BLE001 - 命中计数失败不阻塞
            logger.warning("知识命中计数失败", exc_info=True)

    def _bump_diag_count(self) -> None:
        """C7 验证计数器：记录 LLM 诊断触发次数（事后核对触发频率与降频效果）。"""
        stats = self._reporter.stats
        stats["llm_diagnoses"] = stats.get("llm_diagnoses", 0) + 1
