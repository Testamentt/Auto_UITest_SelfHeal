"""知识库后端抽象。

定义知识存储与检索的统一接口（Protocol）。内存实现（store.KnowledgeStore）与
SQLite 实现（sqlite_store.SqliteKnowledgeStore）均遵循该接口，由 factory 按配置选择，
调用方（orchestrator / PopupGuard / 测试）不感知具体后端。
"""

from __future__ import annotations

from typing import Protocol

from selfheal.knowledge.schema import PopupFeature, RepairCase, RepairQuery


class KnowledgeBackend(Protocol):
    """知识库统一接口（内存 / SQLite 实现均遵循）。

    Phase 5 A 语义化新增：
    - find_by_repair_key：L1 精确命中（确定性 repair_key）。
    - find_semantic：L3 按 page_fingerprint 分桶 → numpy 余弦相似度。
    - bump_hit / set_verified：防污染衰减与人工审核（命中递增、verified 标记）。
    A3 门面：query(RepairQuery) 收敛「L1 精确 → 旧式检索」的择优（置信度/缓存验证留调用方）。
    """

    def add_repair(self, case: RepairCase) -> None:
        """沉淀一条修复案例。"""
        ...

    def add_popup(self, feature: PopupFeature) -> None:
        """沉淀一条弹窗特征。"""
        ...

    def find_repair(
        self, original_selector: str, dom_fingerprint: str | None = None
    ) -> RepairCase | None:
        """按原定位器检索修复案例；给定 dom_fingerprint 时优先同结构页面。"""
        ...

    def find_by_repair_key(self, repair_key: str) -> RepairCase | None:
        """L1：按确定性 repair_key 精确命中（页面指纹 + 元素文本 + 标签路径）。"""
        ...

    def query(self, q: RepairQuery) -> list[tuple[RepairCase, str]]:
        """按优先级返回候选（L1 精确命中优先，其次旧式 selector+指纹）；可能为空列表。

        返回 [(case, source)]，source ∈ {"l1", "legacy"}；供调用方按序做置信度/缓存验证。
        """
        ...

    def find_semantic(
        self,
        query_vec: bytes,
        page_fingerprint: str,
        embedding_version: str,
        k: int = 1,
        threshold: float = 0.75,
    ) -> list[tuple[RepairCase, float]]:
        """L3：同页分桶 + numpy 余弦相似度，返回 top-k（sim >= threshold）。"""
        ...

    def bump_hit(self, repair_key: str) -> None:
        """命中递增（热度 / 衰减用）。"""
        ...

    def set_verified(self, repair_key: str, verified: bool) -> None:
        """人工审核标记（人审确认后置 True）。"""
        ...

    def find_popup(self, signature: str) -> PopupFeature | None:
        """按弹窗签名检索关闭方式。"""
        ...

    def count_popups(self) -> int:
        """返回已沉淀的弹窗特征数量。"""
        ...
