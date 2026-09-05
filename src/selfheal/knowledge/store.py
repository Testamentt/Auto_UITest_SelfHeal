"""知识库存储与检索（内存后端，与 SQLite 并列为双实现之一，见决策 D10）。

适用测试 / 临时场景；SQLite 为持久化默认。Phase 5 A 语义化：
- find_by_repair_key：L1 精确命中（确定性 repair_key）。
- find_semantic：L3 按 page_fingerprint 分桶 → numpy 余弦。
- bump_hit / set_verified：防污染衰减与人工审核。
"""

from __future__ import annotations

from selfheal.knowledge.schema import PopupFeature, RepairCase, RepairQuery


class KnowledgeStore:
    def __init__(self) -> None:
        self._repairs: list[RepairCase] = []
        self._popups: list[PopupFeature] = []

    def add_repair(self, case: RepairCase) -> None:
        self._repairs.append(case)

    def add_popup(self, feature: PopupFeature) -> None:
        """按 signature upsert（V5 复核：与 SQLite 后端语义对齐，已存在则更新为最新观察）。"""
        for idx, existing in enumerate(self._popups):
            if existing.signature == feature.signature:
                self._popups[idx] = feature
                return
        self._popups.append(feature)

    def find_repair(self, original_selector: str, dom_fingerprint: str | None = None):
        """按原定位器检索；多条命中时优先指纹匹配者，否则取置信度最高（对齐 SQLite 语义）。"""
        matches = [c for c in self._repairs if c.original_selector == original_selector]
        if not matches:
            return None
        if dom_fingerprint:
            for case in matches:
                if case.dom_fingerprint == dom_fingerprint:
                    return case
        return max(matches, key=lambda c: c.confidence)

    def find_by_repair_key(self, repair_key: str) -> RepairCase | None:
        """L1：按确定性 repair_key 精确命中。"""
        for case in self._repairs:
            if case.repair_key == repair_key:
                return case

    def query(self, q: RepairQuery) -> list[tuple[RepairCase, str]]:
        """按优先级返回候选（L1 精确命中优先，其次旧式 selector+指纹）；可能为空列表。

        去重按 (new_selector, confidence)：同一案例经两条路径取回时不重复追加。
        """
        results: list[tuple[RepairCase, str]] = []
        if q.repair_key:
            case = self.find_by_repair_key(q.repair_key)
            if case is not None:
                results.append((case, "l1"))
        legacy = self.find_repair(q.original_selector, q.dom_fingerprint)
        if legacy is not None and not any(
            (c.new_selector, c.confidence) == (legacy.new_selector, legacy.confidence)
            for c, _s in results
        ):
            results.append((legacy, "legacy"))
        return results

    def find_semantic(
        self,
        query_vec: bytes,
        page_fingerprint: str,
        embedding_version: str,
        k: int = 1,
        threshold: float = 0.75,
    ) -> list[tuple[RepairCase, float]]:
        """L3：按 page_fingerprint 分桶 → 桶内 numpy 余弦，返回 top-k（sim >= threshold）。"""
        import numpy as np

        bucket = [
            c
            for c in self._repairs
            if c.page_fingerprint == page_fingerprint
            and c.embedding_version == embedding_version
            and c.embedding is not None
            # 审查 C3：维度与查询向量不符的行跳过（防 matmul 崩溃）
            and len(c.embedding) == len(query_vec)
        ]
        if not bucket:
            return []
        qv = np.frombuffer(query_vec, dtype=np.float32)
        matrix = np.stack([np.frombuffer(c.embedding, dtype=np.float32) for c in bucket])
        sims = matrix @ qv
        results: list[tuple[RepairCase, float]] = []
        for idx in np.argsort(-sims):
            sim = float(sims[idx])
            if sim < threshold:
                break
            results.append((bucket[idx], sim))
            if len(results) >= k:
                break
        return results

    def bump_hit(self, repair_key: str) -> None:
        """命中递增（热度 / 衰减用）；last_hit_at 用真实 UTC 时间戳（与 SQLite 端一致，B6）。"""
        from datetime import datetime, timezone

        for case in self._repairs:
            if case.repair_key == repair_key:
                case.hit_count += 1
                case.last_hit_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                return

    def set_verified(self, repair_key: str, verified: bool) -> None:
        """人工审核标记。"""
        for case in self._repairs:
            if case.repair_key == repair_key:
                case.is_verified = verified
                return

    def find_popup(self, signature: str):
        for feature in self._popups:
            if feature.signature == signature:
                return feature
        return None

    def count_popups(self) -> int:
        return len(self._popups)

    def count_repairs(self) -> int:
        return len(self._repairs)
