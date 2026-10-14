"""知识库存储与检索（内存后端，与 SQLite 并列为双实现之一，见决策 D10）。

适用测试 / 临时场景；SQLite 为持久化默认。Phase 5 A 语义化：
- find_by_repair_key：L1 精确命中（确定性 repair_key）。
- find_semantic：L3 按 page_fingerprint 分桶 → numpy 余弦。
- bump_hit / set_verified：防污染衰减与人工审核。

M4：与 SqliteKnowledgeStore 语义逐条对齐——add_repair 走 upsert（键与覆盖列清单一致）、
find_repair 择优规则一致（指纹命中取置信度最高者，退化取置信度最高者）、last_hit_at 同格式
（base.utc_now_iso）、补 close()。

R1：``created_at`` 同法归位——写入侧 ``None`` 时补 ``base.utc_now_iso()``（此前只靠 SQLite
列默认值兜底，内存端落 ``None``，两端 L3 新鲜窗口判定口径不同）；upsert 保留首写值。
R3：``confidence`` 与 SQLite 端同规则归一（``None``/负值 → 0.0，``> 1.0`` 留给消费者
fail-closed 拒绝），脏值不会让两端排序结论相反。
"""

from __future__ import annotations

from dataclasses import replace

from selfheal.knowledge.base import utc_now_iso, utc_now_iso_if_missing
from selfheal.knowledge.schema import PopupFeature, RepairCase, RepairQuery


def _normalize_confidence(value: float | None) -> float:
    """confidence 的 NULL / 负值归一（R3；与 SQLite 端同规则，实现各自独立）。

    内存端正常写入即 0.0–1.0，本函数是**契约护栏**：调用方塞了 ``None`` / 负值时，与 SQLite
    端（``sqlite_store._normalize_confidence``）归一到同一取值，保证两端读出的 ``confidence``
    逐值相同、排序结论一致（R3：脏库含负值时两端曾排序相反）。

    刻意**不**夹取 ``> 1.0``：越上界的置信度属无效数据，由 ``fix_generator.lookup_knowledge``
    的 ``0.0 <= conf <= 1.0`` 门槛 fail-closed 拒绝；在此洗成 1.0 会静默绕过该门槛（R4）。
    """
    if value is None:
        return 0.0
    return max(0.0, float(value))


def _normalized_case(case: RepairCase) -> RepairCase:
    """读出归一（R3）：confidence 的 ``NULL``/负值归零，返回新对象，不改库内数据。

    与 SQLite 端 ``_row_to_repair`` 的读侧归一对称——两端"读出的 confidence 同值域"这一
    契约不能只靠写入侧保证（内存对象可被测试/调试直接改，见
    ``test_dirty_confidence_ordering_parity`` 的直插脏值场景）。``> 1.0`` 值原样透出，交给
    消费者 fail-closed 门槛（不在此静默洗白）。
    """
    return replace(case, confidence=_normalize_confidence(case.confidence))


class KnowledgeStore:
    def __init__(self) -> None:
        self._repairs: list[RepairCase] = []
        self._popups: list[PopupFeature] = []

    @staticmethod
    def _upsert_key(case: RepairCase) -> tuple[str, str, str]:
        """upsert 键，与 SQLite 唯一索引 (original_selector, new_selector, dom_fingerprint) 对齐。

        dom_fingerprint 归一化 None → ""：SQLite 端同样归一（UNIQUE 索引对 NULL 不生效，
        见审查 C2），不归一化会让"无指纹"案例在两端落成不同条数。
        """
        return (case.original_selector, case.new_selector, case.dom_fingerprint or "")

    def add_repair(self, case: RepairCase) -> None:
        """沉淀一条修复案例：按键 upsert，已存在则更新（M4：对齐 SQLite 的 ON CONFLICT）。

        覆盖列与 SQLite 的 DO UPDATE 清单完全一致：strategy / confidence / page_url /
        page_fingerprint / repair_key / embedding / embedding_version；
        hit_count / last_hit_at / is_verified / created_at 保留原值——尤其 is_verified 是
        人工审核标记，再次沉淀不得静默清除（L3 防污染自动采纳依赖该信任状态）。

        R1：``created_at`` 缺席（``None``）时补 ``base.utc_now_iso()``，与 SQLite 端
        （列默认值已被写侧显式值取代）同格式同语义；仅首写生效（upsert 保留首写值）。

        R3：``confidence`` 经 ``_normalize_confidence`` 夹取到 ``[0.0, 1.0]``（``None`` →
        0.0），与 SQLite 端写入归一一致，脏值不会以不同数值落在两端。
        """
        # 写侧归一化与 SQLite 一致：空指纹统一按 None 存储，读回也是 None（SQLite 端存 "" 但读侧还原）
        stored = case if case.dom_fingerprint else replace(case, dom_fingerprint=None)
        # 时间字段同样在写侧补齐并归一，保证"读出的 created_at 永远可解析"这一契约两端一致
        stored = replace(
            stored,
            created_at=utc_now_iso_if_missing(stored.created_at),
            confidence=_normalize_confidence(stored.confidence),
        )
        key = self._upsert_key(case)
        for idx, existing in enumerate(self._repairs):
            if self._upsert_key(existing) == key:
                self._repairs[idx] = replace(
                    stored,
                    hit_count=existing.hit_count,
                    last_hit_at=existing.last_hit_at,
                    is_verified=existing.is_verified,
                    created_at=existing.created_at,
                )
                return
        self._repairs.append(stored)

    def add_popup(self, feature: PopupFeature) -> None:
        """按 signature upsert（V5 复核：与 SQLite 后端语义对齐，已存在则更新为最新观察）。"""
        for idx, existing in enumerate(self._popups):
            if existing.signature == feature.signature:
                self._popups[idx] = feature
                return
        self._popups.append(feature)

    def find_repair(self, original_selector: str, dom_fingerprint: str | None = None):
        """按原定位器检索；择优规则与 SQLite 端逐条对齐（M4）。

        对 SQLite 的 ``ORDER BY confidence DESC, rowid ASC`` 取首个指纹匹配者；无指纹匹配
        （或未给指纹）时退化取置信度最高者。confidence 相同时按插入序取先写入者——
        ``sorted`` 稳定（并列保持列表序）且列表序恒为插入序（upsert 原地替换、不搬到表尾），
        与 SQLite 的 ``rowid`` 升序（insert 自增、update 不变）一致，故同输入必同结果（R2：
        该并列次序是既定契约，两端都由测试锁定）。

        R3：读出前统一归一 confidence（``_normalized_case``），与 SQLite 端 ``_row_to_repair``
        对称——脏值（``None`` / 负值 / 越界）不会让两端对"谁更优"给出相反判断；排序键用
        归一后的值，避免 ``None`` 参与比较抛 ``TypeError``。
        """
        matches = [c for c in self._repairs if c.original_selector == original_selector]
        if not matches:
            return None
        ordered = sorted(
            (_normalized_case(c) for c in matches), key=lambda c: c.confidence, reverse=True
        )
        if dom_fingerprint:
            for case in ordered:
                if case.dom_fingerprint == dom_fingerprint:
                    return case
        return ordered[0]

    def find_by_repair_key(self, repair_key: str) -> RepairCase | None:
        """L1：按确定性 repair_key 精确命中（R3：读出归一 confidence，与 SQLite 端对称）。"""
        for case in self._repairs:
            if case.repair_key == repair_key:
                return _normalized_case(case)

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
        """L3：按 page_fingerprint 分桶 → 桶内 numpy 余弦，返回 top-k（sim >= threshold）。

        返回案例的 confidence 同样经读出归一（R3），与 SQLite 端一致。
        """
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
            results.append((_normalized_case(bucket[idx]), sim))
            if len(results) >= k:
                break
        return results

    def bump_hit(self, repair_key: str) -> None:
        """命中递增（热度 / 衰减用）。

        last_hit_at 统一写 UTC ISO 串（形如 ``2026-10-08T12:00:00+00:00``，见
        base.utc_now_iso），与 SQLite 端同格式——修复前 SQLite 端写 ``datetime('now')``
        （空格分隔、无时区），两端读出的字符串不可互认。
        """
        for case in self._repairs:
            if case.repair_key == repair_key:
                case.hit_count += 1
                case.last_hit_at = utc_now_iso()
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

    def close(self) -> None:
        """释放后端资源：内存后端无外部句柄，no-op 且幂等（M4，供生命周期统一收口）。

        刻意不清空已沉淀数据：与 SQLite 的 close（断开连接、文件数据仍在）语义对齐；
        关闭后不保证可继续使用，但重复调用不抛异常。
        """
        return None
