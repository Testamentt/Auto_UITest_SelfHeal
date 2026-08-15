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
from selfheal.knowledge.schema import RepairQuery

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
        """知识库优先查询：L1 repair_key 精确命中硬短路；未中回退旧式 selector+指纹检索。

        经 A3 门面 query() 按优先级取候选（L1 → legacy），此处只做置信度边界 + 缓存验证
        （编排决策保留在调用方）；对 L1 候选命中递增热度并标注 root_cause=cached_l1。
        """
        repair_key: str | None = None
        element_context = context.element_context
        # v2：repair_key = md5(page_fingerprint|tag_path)，不含文本。
        # 仅当有结构上下文（tag_path 非空）才提供 key：静态兜底上下文 tag_path 为空，
        # 若照常计算会使同页所有静态失败折叠成同一键（碰撞、跨用例误命中）→ 只走旧式检索。
        if element_context is not None and not element_context.is_empty and element_context.tag_path:
            repair_key = compute_repair_key(context.page_fingerprint, element_context.tag_path)
        for case, source in self._knowledge.query(
            RepairQuery(
                original_selector=context.original_selector,
                dom_fingerprint=context.dom_fingerprint,
                repair_key=repair_key,
            )
        ):
            # #10：读取时校验置信度边界（None/越界按未命中），防脏数据进入复用；再验证缓存选择器可用
            conf = case.confidence
            if conf is None or not (0.0 <= conf <= 1.0) or conf < self._settings.healing.confidence_threshold:
                continue
            if not selector_exists(self._page, case.new_selector):
                continue
            if source == "l1":
                self._bump_hit(case)
                root_cause = "cached_l1"
            else:
                root_cause = "cached"
            return HealOutcome(
                success=True,
                new_selector=case.new_selector,
                confidence=case.confidence,
                strategy="knowledge",
                root_cause=root_cause,
            )
        return None

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
            candidate = None
            try:
                candidate = self._build_strategy(strategy_cls, context).repair(
                    context.scene, context.original_selector, context.description
                )
            except Exception:  # noqa: BLE001 - 单个策略内部异常（数据/embedding 等）不中断策略链
                logger.warning("策略 %s 执行异常，已跳过（继续后续策略）", name, exc_info=True)
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
                review_writer=self._review_writer,  # B6：L3 人审清单收敛出口
                page=self._page,  # M1：L3 采纳前验证候选 selector 仍存在
            )
        if strategy_cls is VisualStrategy:
            return strategy_cls(client=self._counting_client(self._vision_client, "vlm_calls"))
        return strategy_cls()

    def _review_writer(self, **kwargs) -> None:
        """L3 人审清单写出（B6 收敛出口）：策略不直连 reporting，由编排侧统一落盘。best-effort。"""
        try:
            from selfheal.reporting.fix_proposals import append_review_proposal

            append_review_proposal(**kwargs)
        except Exception:  # noqa: BLE001 - 人审清单写出失败不阻塞流水线
            logger.warning("L3 人审清单写出失败", exc_info=True)

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
