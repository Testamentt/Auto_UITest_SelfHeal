"""语义定位策略（Phase 2 → Phase 5 A 升级）。

Phase 5 A 语义化后，本策略成为调度链的 **L3 语义向量检索**：
优先从知识库按 `find_semantic`（page 分桶 + numpy 余弦）命中语义相似的修复案例；
未命中（或上下文为空）时，**退回 Phase 2 的 LLM 语义定位**（自然语言描述 + 防幻觉护栏）。

**L3 采纳规则（防污染，见计划 v5）**：
- sim > 0.92 且 `is_verified`（人审过）→ 自动采纳；
- sim > 0.80 且 `created_at` 在 7 天新鲜窗口内 → 自动采纳（免人审，冷启动）；
- 其余 0.75 < sim ≤ 0.92 → 仅写 `reports/review-queue.md` 人审清单，返回 None 降级 L4（不阻塞流水线）。

不可用（无 client / 无 description / 调用异常 / 解析失败）一律返回 None。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from selfheal.agent.confidence import KEY_SEMANTIC_L3, KEY_SEMANTIC_LLM, calibrate
from selfheal.agent.dom import (
    ElementContext,
    build_compact_dom,
    build_stable_selector,
    interactive_candidates,
)
from selfheal.agent.strategies.base import RepairCandidate, RepairStrategy
from selfheal.collect.collector import Scene
from selfheal.knowledge.base import KnowledgeBackend
from selfheal.llm.base import ChatMessage, LLMClient
from selfheal.llm.embedding import EmbeddingClient
from selfheal.llm.io import extract_json, safe_float, safe_str

# L3 语义检索：最低相似度 / 自动采纳阈值 / 新鲜窗口
L3_MIN_SIM = 0.75
L3_VERIFIED_SIM = 0.92
L3_FRESH_SIM = 0.80
L3_FRESH_DAYS = 7

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """你是 Web UI 自动化测试专家。原定位器已失效，请根据描述在页面中重新定位目标元素。
已失效的原始选择器: {selector}
目标描述: {description}
当前页面可交互元素索引（每行一条，含可用的稳定 selector）:
{compact_dom}
要求: 只输出一个 JSON 对象，不要输出解释:
{{"selector": "上面索引中存在的稳定 selector，找不到则留空字符串", "confidence": 0.0~1.0}}"""


def _is_fresh(created_at: str | None, days: int = L3_FRESH_DAYS) -> bool:
    """created_at 是否在新鲜窗口内（容错解析：ISO 'T' 或 SQLite ' ' 分隔）。"""
    if not created_at:
        return False
    try:
        dt = datetime.fromisoformat(created_at.replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - dt
        return age.total_seconds() <= days * 86400
    except ValueError:
        return False


class SemanticStrategy(RepairStrategy):
    name = "semantic"

    def __init__(
        self,
        client: LLMClient | None = None,
        *,
        knowledge: KnowledgeBackend | None = None,
        embedding: EmbeddingClient | None = None,
        page_fingerprint: str | None = None,
        element_context: ElementContext | None = None,
        review_writer: Callable | None = None,
        page=None,  # 页面引用（M1：L3 采纳前验证候选 selector 仍存在）；None=页面不可用，视为存在
        shrink_self_reported: bool = False,  # T5：LLM 自报段保守收缩开关（见 agent/confidence.py）
    ):
        self._client = client
        self._knowledge = knowledge
        self._embedding = embedding
        self._page_fingerprint = page_fingerprint or ""
        self._element_context = element_context
        self._page = page
        self._shrink_self_reported = shrink_self_reported
        # B6：L3 未达自动采纳时的人审清单写出（由编排侧注入，策略不再直连 reporting 层）
        self._review_writer = review_writer

    def repair(self, scene: Scene, original_selector: str, description: str | None = None):
        # L3 知识语义向量检索（Phase 5 A）—— 优先；未命中再退回 LLM 语义定位
        if (
            self._knowledge is not None
            and self._embedding is not None
            and self._element_context is not None
            and not self._element_context.is_empty
            and (candidate := self._knowledge_semantic(original_selector)) is not None
        ):
            return candidate
        # 原 LLM 语义定位（Phase 2，防幻觉护栏保留）
        return self._llm_semantic(scene, original_selector, description)

    def _knowledge_semantic(self, original_selector: str) -> RepairCandidate | None:
        """L3：知识库向量检索 → 采纳或建议（不采纳返回 None，由编排继续 L4）。"""
        query_vec = self._embedding.embed(self._element_context.query_text)
        hits = self._knowledge.find_semantic(
            query_vec,
            self._page_fingerprint,
            self._embedding.embedding_version,
            k=1,
            threshold=L3_MIN_SIM,
        )
        if not hits:
            return None
        case, sim = hits[0]
        # M1 护栏（与 L1 对称）：知识库候选可能已再次失效（页面又改版）——
        # 采纳前验证 new_selector 仍真实存在；失效则写人审清单、不采纳（交 L4 重定位）。
        # page=None（页面不可用）时视为存在，与 selector_exists 语义一致。
        # selector_exists 惰性导入：context ↔ strategies 包存在导入环（orchestrator → context → strategies）。
        from selfheal.agent.context import selector_exists

        if not selector_exists(self._page, case.new_selector):
            if self._review_writer is not None:
                self._review_writer(
                    original_selector=original_selector,
                    new_selector=case.new_selector,
                    confidence=sim,
                    page_url=case.page_url,
                    strategy=self.name,
                    reason="cached_selector_stale",
                )
            return None
        if self._accept(case, sim):
            self._bump(case)
            return RepairCandidate(
                selector=case.new_selector,
                # T5：L3 相似度段统一标尺出口（恒等，契约见 agent/confidence.py）
                confidence=calibrate(KEY_SEMANTIC_L3, sim),
                strategy=self.name,
            )
        # 未达自动采纳：写人审清单（经编排侧注入的 review_writer，B6 收敛出口），
        # 不采纳（继续 L4）、不阻塞流水线；writer 未注入则跳过（不写不炸）。
        if self._review_writer is not None:
            self._review_writer(
                original_selector=original_selector,
                new_selector=case.new_selector,
                confidence=sim,
                page_url=case.page_url,
                strategy=self.name,
                reason="sim_not_auto_accept",
            )
        return None

    def _accept(self, case, sim: float) -> bool:
        """L3 采纳规则：已 verified 高相似 / 新鲜窗口内高相似 → 自动采纳。"""
        return (sim > L3_VERIFIED_SIM and case.is_verified) or (
            sim > L3_FRESH_SIM and _is_fresh(case.created_at, L3_FRESH_DAYS)
        )

    def _bump(self, case) -> None:
        """命中递增（热度 / 衰减用）；失败记 warning 不阻塞采纳（V5 复核：不再静默吞错）。"""
        try:
            self._knowledge.bump_hit(case.repair_key)
        except Exception:  # noqa: BLE001 - 热度更新失败不影响已成功的采纳
            logger.warning("知识热度更新失败（repair_key=%s）", case.repair_key, exc_info=True)

    def _llm_semantic(
        self, scene: Scene, original_selector: str, description: str | None = None
    ) -> RepairCandidate | None:
        if self._client is None or not description or not scene.dom_snapshot:
            return None
        # 护栏白名单：页面中真实存在、可由 build_stable_selector 生成的稳定定位器。
        # T8：候选来源优先 Playwright 原生解析（有 page 采集），静态 DOM 快照解析兜底。
        real_selectors = {
            s for el in interactive_candidates(scene) if (s := build_stable_selector(el))
        }
        if not real_selectors:
            return None

        rows = build_compact_dom(scene.dom_snapshot)
        prompt = _PROMPT_TEMPLATE.format(
            selector=original_selector,
            description=description,
            compact_dom="\n".join(rows),
        )
        try:
            reply = self._client.chat([ChatMessage("user", prompt)])
            data = extract_json(reply)
        except Exception:  # noqa: BLE001 - 模型异常返回 None
            return None
        if not data:
            return None

        selector = safe_str(data, "selector")
        confidence = safe_float(data, "confidence")
        # 护栏：selector 必须在真实索引中存在；置信度越界 / 空 → 拒绝
        if selector not in real_selectors or not (0.0 <= confidence <= 1.0):
            return None
        return RepairCandidate(
            selector=selector,
            # T5：LLM 自报段统一标尺出口（shrink_self_reported 开启时保守收缩，见 confidence.py）
            confidence=calibrate(
                KEY_SEMANTIC_LLM,
                round(confidence, 3),
                shrink_self_reported=self._shrink_self_reported,
            ),
            strategy=self.name,
        )
