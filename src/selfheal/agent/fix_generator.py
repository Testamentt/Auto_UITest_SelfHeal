# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""修复提案生成器 —— 专职「知识库优先 → 诊断 → 策略链调度」。

产出 FixProposal（知识缓存命中 / 最佳候选 / 根因），由 PersistenceHandler 做阈值路由。
策略注册表由 orchestrator 注入（共享同一 dict，测试 monkeypatch 仍生效）。

阈值配置的未登记策略名与 `strategy_order` 一样记 warning（此前静默回退全局，"阈值配了
没生效"无从察觉）；回退行为不变。
"""

from __future__ import annotations

import logging
from dataclasses import replace

from selfheal.agent.context import (
    FixProposal,
    HealingContext,
    HealOutcome,
    action_requires_actionability,
    selector_actionable,
    selector_exists,
)
from selfheal.agent.strategies import SemanticStrategy, VisualStrategy
from selfheal.agent.strategies.base import RepairCandidate
from selfheal.agent.strategies.semantic import L3Thresholds
from selfheal.config import Settings
from selfheal.dom import compute_repair_key
from selfheal.knowledge.base import KnowledgeBackend
from selfheal.knowledge.schema import RepairQuery

logger = logging.getLogger(__name__)

# 按策略阈值配置的键名清单（与 strategy_order 的未知名 warning 对称）
_THRESHOLD_KEY_FIELDS = ("strategy_thresholds", "strategy_early_accept")


def _better_rejected(
    current: RepairCandidate | None, candidate: RepairCandidate
) -> RepairCandidate:
    """在被拒候选里保留置信度更高者（仅用于人审建议，不代表可采纳）。"""
    if current is None or candidate.confidence > current.confidence:
        return candidate
    return current


def counting_proxy(client, key: str, stats: dict):
    """计数代理工厂——每次真实模型调用（chat/analyze_image/complete）递增 stats[key]。

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
    """专职生成修复提案（知识库优先 → 诊断 → 策略链调度 → LLM 深挖归因）。"""

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
        # 已告警过的阈值键（实例级，只提示一次，避免每轮调度刷屏）
        self._warned_threshold_keys: set[tuple[str, str]] = set()

    def generate(
        self,
        context: HealingContext,
        failure=None,
        use_knowledge: bool = True,
        action: str | None = None,
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
        root_cause = self._rule_diagnoser.diagnose(
            context.scene, context.original_selector, failure
        )
        # 3) 策略链调度：**逐候选**先按自身阈值 + 定位器唯一性过滤，再在合格候选里取最优。
        #    旧实现先按原始置信度取最大、再用赢家自己的阈值裁决：高置信但未达自身阈值的候选
        #    会遮蔽低置信但已达标的候选（heuristic 0.75/阈值 0.8 遮蔽 visual 0.62/阈值 0.6）。
        best, rejected = self._select_candidates(context, action)
        # 采纳按该策略的独立阈值裁决（缺省回退全局 confidence_threshold）；best=None 时用全局
        threshold = (
            self._settings.healing.accept_threshold(best.strategy)
            if best is not None
            else self._settings.healing.confidence_threshold
        )
        if best is not None and best.confidence >= threshold:
            # 低置信度成功（[threshold, llm_diagnose_threshold)）也补 LLM 归因，
            # 丰富审计与人审清单；高置信成功直接用规则归因，省一次 LLM 调用。
            if (
                self._llm_diagnoser is not None
                and best.confidence < self._settings.healing.llm_diagnose_threshold
            ):
                root_cause = self._llm_diagnoser.diagnose(
                    context.scene, context.original_selector, failure
                )
                self._bump_diag_count()
            return FixProposal(best=best, root_cause=root_cause)
        # 策略链失败（无候选或置信度不足）：LLM 深挖归因，供人审清单/失败报告（诊断后置）
        if self._llm_diagnoser is not None:
            root_cause = self._llm_diagnoser.diagnose(
                context.scene, context.original_selector, failure
            )
            self._bump_diag_count()
        # rejected 一并带出：失败路径的人审建议要用它，过滤不能顺手丢掉审计信息
        return FixProposal(best=best, root_cause=root_cause, rejected=rejected)

    def lookup_knowledge(self, context: HealingContext) -> HealOutcome | None:
        """知识库优先查询：L1 repair_key 精确命中硬短路；未中回退旧式 selector+指纹检索。

        经知识库门面 query() 按优先级取候选（L1 → legacy），此处只做置信度边界 + 缓存验证
        （编排决策保留在调用方）；对 L1 候选命中递增热度并标注 root_cause=cached_l1。
        """
        repair_key: str | None = None
        element_context = context.element_context
        # v2：repair_key = md5(page_fingerprint|tag_path)，不含文本。
        # 仅当有结构上下文（tag_path 非空）才提供 key：静态兜底上下文 tag_path 为空，
        # 若照常计算会使同页所有静态失败折叠成同一键（碰撞、跨用例误命中）→ 只走旧式检索。
        if (
            element_context is not None
            and not element_context.is_empty
            and element_context.tag_path
        ):
            repair_key = compute_repair_key(context.page_fingerprint, element_context.tag_path)
        for case, source in self._knowledge.query(
            RepairQuery(
                original_selector=context.original_selector,
                dom_fingerprint=context.dom_fingerprint,
                repair_key=repair_key,
            )
        ):
            # 读取时校验置信度边界（None/越界按未命中），防脏数据进入复用；再验证缓存选择器**唯一命中**。
            # 复用门槛与案例来源策略的独立阈值对齐（缺省回退全局），知识复用不用单一全局尺子；
            # 缓存选择器按唯一性校验：失效 / 多命中一律不复用，防脏案例回流（可见性属瞬态，不在此判）。
            conf = case.confidence
            if (
                conf is None
                or not (0.0 <= conf <= 1.0)
                or conf < self._settings.healing.accept_threshold(case.strategy or "knowledge")
            ):
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

    def _warn_unknown_threshold_keys(self) -> None:
        """`strategy_thresholds` / `strategy_early_accept` 里的未登记策略名各记一次 warning。

        与 `strategy_order` 的未知名处理对称：都走 warning + 继续运行。行为**不变**——
        未知名仍"宽待"（`accept_threshold` / `early_accept_for` 回退全局阈值），因为阈值段
        允许先于策略实现落地（先调配置再看行为）；但静默会让"阈值配了没生效"变成纯粹的
        错觉（例如把键写成 `heuristic_strategy` 时表现为"配置似乎不起作用"）。
        每个 (来源字段, 策略名) 只提示一次：本方法每轮调度都会调用，重复告警会淹没日志。
        """
        healing = self._settings.healing
        for field_name in _THRESHOLD_KEY_FIELDS:
            for key in getattr(healing, field_name):
                marker = (field_name, key)
                if key in self._strategy_registry or marker in self._warned_threshold_keys:
                    continue
                self._warned_threshold_keys.add(marker)
                logger.warning(
                    "healing.%s 含未登记策略名 %r，已按全局阈值回退（可用: %s）",
                    field_name,
                    key,
                    list(self._strategy_registry),
                )

    def best_candidate(
        self, context: HealingContext, action: str | None = None
    ) -> RepairCandidate | None:
        """按 strategy_order 逐个尝试策略，返回**合格候选**里的最优者（无合格候选 → None）。

        合格 = 同时通过两项校验：
        1. **定位器唯一命中**（`selector_exists`）——strict-mode 安全底线：多命中的定位器在重试时
           抛 strict-mode violation（非 TimeoutError，不触发自愈），脏案例还会落库；
        2. **达该策略自己的采纳阈值**（`strategy_thresholds`，缺省回退全局）。

        先过滤再比较：旧实现按原始置信度取最大、再用赢家自己的阈值裁决，高置信但未达自身
        阈值的候选会遮蔽低置信但已达标的候选（heuristic 0.75/阈值 0.8 遮蔽 visual
        0.62/阈值 0.6 → 整体误判失败）。早接受（省钱）保留，但要求候选当前可交互才短路
        （`set_input_files` 等不要求可交互的动作除外，见 `action_requires_actionability`）。
        """
        best, _ = self._select_candidates(context, action)
        return best

    def _select_candidates(
        self, context: HealingContext, action: str | None = None
    ) -> tuple[RepairCandidate | None, RepairCandidate | None]:
        """策略链调度：返回 `(合格最优, 被拒最优)`。

        合格 = ①唯一命中（硬，`selector_exists`）②达自身策略阈值（硬）。
        可交互性（`selector_actionable`）**不淘汰**候选：它取决于动作（`set_input_files` 对隐藏
        input 同样可用）且 Playwright 会自动等待；硬拒会把"暂不可见但稍后可见"的元素与隐藏
        上传框一起误杀。取舍方式：
        - **置信度优先**：不拿"当前可交互"压过高置信候选（D6：宁可失败，也不错误地动作——
          一个可交互但置信度更低的诱饵候选点错了代价更高）；
        - 可交互性用于两处：要求可交互的动作**不早接受**非可交互候选（给后续策略机会），
          以及**同分 tie-break** 时让可交互者胜出；
        - 最终候选若当前不可交互，仍采纳并记日志（交给 Playwright 自动等待，不放弃修复机会）。
        不要求可交互的动作（`set_input_files` / 未知来源）：不探测、行为与既有实现一致。
        被拒候选（非唯一 / 未达自身阈值）不参与采纳，只回传给 generate() 的 `rejected` 字段，
        供失败路径写人审清单——过滤掉审计信息会让"为什么没修"无从追溯。
        """
        self._warn_unknown_threshold_keys()
        require_actionable = action_requires_actionability(action)
        best: RepairCandidate | None = None
        best_actionable = False
        rejected: RepairCandidate | None = None
        for name in self._settings.healing.strategy_order:
            strategy_cls = self._strategy_registry.get(name)
            if strategy_cls is None:
                # 未知策略名不再静默跳过，记 warning 便于排查配置笔误
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
            # 候选来源策略以**调度键**为准：阈值过滤与后续路由都按同一个键算，
            # 避免插件策略注册名与自报 strategy 不一致时"过滤用 A 阈值、路由按 B 判"的自相矛盾。
            # 用 replace 而非原地改：策略可能返回共享/缓存对象，原地改名会污染调用方。
            if candidate.strategy != name:
                logger.debug(
                    "策略 %r 自报 strategy=%r，按调度键归一（阈值与路由统一口径）",
                    name,
                    candidate.strategy,
                )
                candidate = replace(candidate, strategy=name)
            # ① 唯一性（硬）：多命中在重试时必抛 strict-mode violation（非超时，不会二次自愈）
            if not selector_exists(self._page, candidate.selector):
                logger.warning(
                    "策略 %s 的候选定位器非唯一命中（或页面不可读），已跳过：%s",
                    name,
                    candidate.selector,
                )
                rejected = _better_rejected(rejected, candidate)
                continue
            # ② 逐候选按自身阈值过滤：不达标者不得参与比较，否则会遮蔽后续达标候选
            accept = self._settings.healing.accept_threshold(name)
            if candidate.confidence < accept:
                logger.info(
                    "策略 %s 候选置信度 %.3f 未达该策略阈值 %.3f，不参与择优",
                    name,
                    candidate.confidence,
                    accept,
                )
                rejected = _better_rejected(rejected, candidate)
                continue
            # 可交互性只在动作要求时才探测：set_input_files 对隐藏 input 同样可用，探测纯属多花往返
            actionable = (
                selector_actionable(self._page, candidate.selector) if require_actionable else True
            )
            # 择优：置信度优先；同分时让可交互者胜出（确定性 tie-break）
            if best is None or candidate.confidence > best.confidence:
                best, best_actionable = candidate, actionable
            elif candidate.confidence == best.confidence and actionable and not best_actionable:
                best, best_actionable = candidate, True
            # ③ 早接受（省钱）：要求可交互的动作只在候选当前可交互时短路，避免跳过后续更可用的策略
            if (actionable or not require_actionable) and candidate.confidence >= (
                self._settings.healing.early_accept_for(name)
            ):
                return candidate, rejected
        if require_actionable and best is not None and not best_actionable:
            logger.info(
                "最优候选当前不可交互（动作=%s），仍采纳并交 Playwright 自动等待：%s",
                action,
                best.selector,
            )
        return best, rejected

    def _build_strategy(self, strategy_cls, context: HealingContext):
        """实例化策略；语义/视觉策略透传（计数代理包裹的）LLM/VLM client。

        语义策略注入 knowledge/embedding/页面指纹/元素上下文，执行 L3 向量检索。
        L3 阈值由 `settings.knowledge.semantic` 注入（可按被测系统标定，不必发版）。
        client 包一层计数代理，真实 LLM/VLM 调用计入 reporter.stats。
        """
        if strategy_cls is SemanticStrategy:
            return strategy_cls(
                client=self._counting_client(self._llm_client, "llm_calls"),
                knowledge=self._knowledge if self._embedding is not None else None,
                embedding=self._embedding,
                page_fingerprint=context.page_fingerprint,
                element_context=context.element_context,
                review_writer=self._review_writer,  # L3 人审清单收敛出口
                page=self._page,  # L3 采纳前验证候选 selector 仍唯一命中
                shrink_self_reported=self._settings.healing.shrink_self_reported,  # 自报置信度收缩
                thresholds=L3Thresholds.from_config(self._settings.knowledge.semantic),  # 阈值外置
            )
        if strategy_cls is VisualStrategy:
            return strategy_cls(client=self._counting_client(self._vision_client, "vlm_calls"))
        return strategy_cls()

    def _review_writer(self, **kwargs) -> None:
        """L3 人审清单写出（收敛出口）：策略不直连 reporting，由编排侧统一落盘。best-effort。"""
        try:
            from selfheal.reporting.fix_proposals import append_review_proposal

            append_review_proposal(**kwargs)
        except Exception:  # noqa: BLE001 - 人审清单写出失败不阻塞流水线
            logger.warning("L3 人审清单写出失败", exc_info=True)

    def _counting_client(self, client, key: str):
        """计数代理（委托模块级 counting_proxy，与 orchestrator 共享同一实现）。"""
        return counting_proxy(client, key, self._reporter.stats)

    def _bump_hit(self, case) -> None:
        """L1 命中递增（热度 / 衰减用）；失败不阻塞硬短路。"""
        try:
            self._knowledge.bump_hit(case.repair_key)
        except Exception:  # noqa: BLE001 - 命中计数失败不阻塞
            logger.warning("知识命中计数失败", exc_info=True)

    def _bump_diag_count(self) -> None:
        """验证计数器：记录 LLM 诊断触发次数（事后核对触发频率与降频效果）。"""
        stats = self._reporter.stats
        stats["llm_diagnoses"] = stats.get("llm_diagnoses", 0) + 1
