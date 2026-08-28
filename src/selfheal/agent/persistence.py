"""持久化处理器（A1 协作者）—— 专职「阈值路由 → 知识沉淀 → 审计报告」。

B1 语义：run() 成功修复先 stage 暂存，引擎层重试成功后 commit_pending 才真正落库，
未验证（重试失败）的修复不得进入知识库，防后续 L1/L3 复用到"没生效"的修复。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from selfheal.agent.context import FixProposal, HealingContext, HealOutcome, selector_exists
from selfheal.agent.dom import compute_repair_key
from selfheal.config import Settings
from selfheal.knowledge.base import KnowledgeBackend
from selfheal.knowledge.schema import RepairCase
from selfheal.reporting.hooks import HealingRecord, HealingReporter

logger = logging.getLogger(__name__)


class PersistenceHandler:
    """A1：专职持久化与审计（阈值路由 / dry-run 建议 / 暂存 → 验证后 commit / 写库 + 记录）。"""

    _PENDING_LIMIT = 64  # 暂存上限，防引擎失败不 commit 时膨胀

    def __init__(
        self,
        settings: Settings,
        knowledge: KnowledgeBackend,
        reporter: HealingReporter,
        embedding,
        page,
    ):
        self._settings = settings
        self._knowledge = knowledge
        self._reporter = reporter
        self._embedding = embedding
        self._page = page
        # B1：成功修复先暂存，引擎层重试成功后 commit_pending（幂等防重复提交）
        self._pending: dict[str, tuple] = {}
        self._committed: set[str] = set()
        self._attempt_counter = 0

    def resolve(self, context: HealingContext, proposal: FixProposal) -> HealOutcome:
        """阈值路由：知识复用 / dry-run 建议 / 成功暂存 / 失败（含人审建议）。"""
        if proposal.cached_outcome is not None:
            self._record(
                context.original_selector, proposal.cached_outcome
            )  # 知识复用也记录（供审计）
            return proposal.cached_outcome
        # T5：采纳阈值按候选来源策略独立裁决（缺省回退全局）；best=None（策略链全失败）时用全局
        threshold = (
            self._settings.healing.accept_threshold(proposal.best.strategy)
            if proposal.best is not None
            else self._settings.healing.confidence_threshold
        )
        if proposal.best is not None and proposal.best.confidence >= threshold:
            if self._settings.healing.dry_run:
                # T14 dry-run：只生成修复建议、不实际应用（不持久化、不换定位器重试），供人审
                self._emit_proposal(context, proposal.best, proposal.root_cause)
                return HealOutcome(
                    success=False,
                    confidence=proposal.best.confidence,
                    strategy=proposal.best.strategy,
                    root_cause="dry_run",
                    proposed_selector=proposal.best.selector,
                )
            outcome = HealOutcome(
                success=True,
                new_selector=proposal.best.selector,
                confidence=proposal.best.confidence,
                strategy=proposal.best.strategy,
                root_cause=proposal.root_cause,
            )
            self.stage(context, outcome)  # B1：暂存，验证后由引擎 commit
            return outcome
        # 策略链失败：有候选但置信度不足 → 建议人审（写 fix-proposals）
        if proposal.best is not None and proposal.best.confidence < threshold:
            self._emit_proposal(context, proposal.best, proposal.root_cause)
        return HealOutcome(success=False, root_cause=proposal.root_cause)

    def stage(self, context: HealingContext, outcome: HealOutcome) -> None:
        """暂存待验证的修复（B1）：设置 attempt_id 并入 pending。"""
        self._attempt_counter += 1
        outcome.attempt_id = f"heal-{self._attempt_counter}"
        self._pending[outcome.attempt_id] = (outcome, context)
        if (
            len(self._pending) >= self._PENDING_LIMIT
        ):  # 上限防膨胀（引擎失败不 commit 时，丢弃最旧暂存）
            self._pending.pop(next(iter(self._pending)))

    def commit_pending(self, attempt_id: str) -> None:
        """验证成功后沉淀知识 + 审计（B1）。按 attempt_id 幂等：已提交/未知 id → no-op。"""
        if attempt_id in self._committed:
            return
        payload = self._pending.get(attempt_id)
        if payload is None:
            return
        outcome, context = payload
        self._persist(context, outcome)  # 内部不抛异常（R4 记日志）
        self._committed.add(attempt_id)
        self._pending.pop(attempt_id, None)

    def _persist(self, context: HealingContext, outcome: HealOutcome) -> None:
        """修复成功后：写入知识库（供后续命中复用）+ 可选 T15 人审建议 + 审计。"""
        # 沉淀失败不影响已成功的自愈结果；但按 R4 不能静默吞错，记 warning。
        try:
            repair_key: str | None = None
            embedding: bytes | None = None
            embedding_version: str | None = None
            element_context = context.element_context
            if element_context is not None and not element_context.is_empty:
                # B4：L1 键写入不依赖 embedding；仅当有结构上下文（tag_path）时写键，
                # 静态上下文（tag_path 为空）不产生键，防同页碰撞（与查询侧门控对称）。
                if element_context.tag_path:
                    repair_key = compute_repair_key(
                        context.page_fingerprint, element_context.tag_path
                    )
                if self._embedding is not None:
                    embedding = self._embedding.embed(element_context.query_text)
                    embedding_version = self._embedding.embedding_version
            self._knowledge.add_repair(
                RepairCase(
                    original_selector=context.original_selector,
                    new_selector=outcome.new_selector or "",
                    strategy=outcome.strategy or "",
                    confidence=outcome.confidence,
                    page_url=context.scene.url,
                    dom_fingerprint=context.dom_fingerprint,
                    page_fingerprint=context.page_fingerprint,
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
                        original_selector=context.original_selector,
                        new_selector=outcome.new_selector or "",
                        strategy=outcome.strategy or "",
                        confidence=outcome.confidence,
                        page_url=context.scene.url,
                        root_cause=outcome.root_cause,
                        verified=not selector_exists(self._page, context.original_selector),
                    )
                except Exception:  # noqa: BLE001 - 建议写出失败不影响已成功的自愈
                    logger.warning("修复建议写出失败", exc_info=True)
        except Exception:  # noqa: BLE001 - 沉淀失败不影响已成功的自愈
            logger.warning("知识沉淀失败（自愈本身已成功）", exc_info=True)
        self._record(context.original_selector, outcome)

    def _record(self, original_selector: str, outcome: HealOutcome) -> None:
        """记录一次成功自愈（含知识复用），供审计与指标统计（T3）。审计失败不影响主流程。"""
        try:
            # T16：真自愈 vs flaky——修复后原定位器仍失效 → 真修复（verified=True）；
            # 原定位器已恢复 → 失败是瞬时的（flaky，verified=False），勿把偶发绿计为修复成功。
            verified = not selector_exists(self._page, original_selector)
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

    def _emit_proposal(self, context: HealingContext, best, root_cause: str | None) -> None:
        """T14/T15：把「原→新」建议写出（fix-proposals），供人审后手动采纳。best-effort。"""
        try:
            from selfheal.reporting.fix_proposals import write_fix_proposal

            write_fix_proposal(
                original_selector=context.original_selector,
                new_selector=best.selector,
                strategy=best.strategy,
                confidence=best.confidence,
                page_url=context.scene.url,
                root_cause=root_cause,
                verified=False,
            )
        except Exception:  # noqa: BLE001 - 建议写出失败不阻塞
            logger.warning("fix-proposal 建议写出失败", exc_info=True)
