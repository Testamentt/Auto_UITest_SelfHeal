"""SQLite 知识库后端（stdlib sqlite3 + numpy 向量）。

持久化修复案例与弹窗特征，重启后仍可命中复用（"越用越聪明"）。
Phase 5 A 语义化：
- L1：find_by_repair_key 按确定性 repair_key 精确命中（硬短路）。
- L3：find_semantic 按 page_fingerprint 分桶 → 桶内 numpy 余弦（向量存 BLOB，避免 JSON 反序列化瓶颈）。
- 防污染：hit_count/last_hit_at 递增、is_verified 标记（人工审核）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from selfheal.knowledge.schema import PopupFeature, RepairCase

_SCHEMA = """
CREATE TABLE IF NOT EXISTS repairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_selector TEXT NOT NULL,
    new_selector TEXT NOT NULL,
    strategy TEXT,
    confidence REAL,
    page_url TEXT,
    dom_fingerprint TEXT,
    page_fingerprint TEXT,
    repair_key TEXT,
    embedding BLOB,
    embedding_version TEXT,
    hit_count INTEGER DEFAULT 0,
    last_hit_at TEXT,
    is_verified INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_repairs_selector ON repairs(original_selector);
CREATE INDEX IF NOT EXISTS idx_repairs_key ON repairs(repair_key);
CREATE INDEX IF NOT EXISTS idx_repairs_semantic
    ON repairs(page_fingerprint, embedding_version);
-- #10：同 (原选择器, 新选择器, 指纹) 去重，避免记录无界增长
CREATE UNIQUE INDEX IF NOT EXISTS idx_repairs_unique
    ON repairs(original_selector, new_selector, dom_fingerprint);
CREATE TABLE IF NOT EXISTS popups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signature TEXT NOT NULL,
    dismiss_selector TEXT NOT NULL,
    category TEXT DEFAULT 'generic'
);
CREATE INDEX IF NOT EXISTS idx_popups_signature ON popups(signature);
"""


class SqliteKnowledgeStore:
    """基于 SQLite 的知识库实现（遵循 KnowledgeBackend 接口）。"""

    def __init__(self, path: str):
        self._path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def add_repair(self, case: RepairCase) -> None:
        # #10 upsert：同 (原,新,指纹) 已存在则更新，不重复插入
        self._conn.execute(
            "INSERT INTO repairs"
            " (original_selector, new_selector, strategy, confidence, page_url, dom_fingerprint,"
            "  page_fingerprint, repair_key, embedding, embedding_version, is_verified)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(original_selector, new_selector, dom_fingerprint)"
            " DO UPDATE SET strategy=excluded.strategy, confidence=excluded.confidence,"
            "               page_url=excluded.page_url, page_fingerprint=excluded.page_fingerprint,"
            "               repair_key=excluded.repair_key, embedding=excluded.embedding,"
            "               embedding_version=excluded.embedding_version, is_verified=excluded.is_verified",
            (
                case.original_selector,
                case.new_selector,
                case.strategy,
                case.confidence,
                case.page_url,
                case.dom_fingerprint,
                case.page_fingerprint,
                case.repair_key,
                case.embedding,
                case.embedding_version,
                int(case.is_verified),
            ),
        )
        self._conn.commit()

    def add_popup(self, feature: PopupFeature) -> None:
        self._conn.execute(
            "INSERT INTO popups (signature, dismiss_selector, category) VALUES (?, ?, ?)",
            (feature.signature, feature.dismiss_selector, feature.category),
        )
        self._conn.commit()

    def find_repair(
        self, original_selector: str, dom_fingerprint: str | None = None
    ) -> RepairCase | None:
        """（旧知识优先）按原定位器精确匹配，指纹优先、其余按置信度。"""
        rows = self._conn.execute(
            "SELECT * FROM repairs WHERE original_selector = ? ORDER BY confidence DESC",
            (original_selector,),
        ).fetchall()
        if not rows:
            return None
        if dom_fingerprint:
            for row in rows:
                if row["dom_fingerprint"] == dom_fingerprint:
                    return self._row_to_repair(row)
        return self._row_to_repair(rows[0])

    def find_by_repair_key(self, repair_key: str) -> RepairCase | None:
        """L1：按确定性 repair_key 精确命中（页面指纹 + 元素文本 + 标签路径的 md5）。"""
        row = self._conn.execute(
            "SELECT * FROM repairs WHERE repair_key = ? LIMIT 1", (repair_key,)
        ).fetchone()
        return self._row_to_repair(row) if row else None

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

        rows = self._conn.execute(
            "SELECT * FROM repairs WHERE page_fingerprint = ? AND embedding_version = ?"
            " AND embedding IS NOT NULL",
            (page_fingerprint, embedding_version),
        ).fetchall()
        if not rows:
            return []
        qv = np.frombuffer(query_vec, dtype=np.float32)
        matrix = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        sims = matrix @ qv  # 向量已归一化 → 余弦
        results: list[tuple[RepairCase, float]] = []
        for idx in np.argsort(-sims):
            sim = float(sims[idx])
            if sim < threshold:
                break
            results.append((self._row_to_repair(rows[idx]), sim))
            if len(results) >= k:
                break
        return results

    def bump_hit(self, repair_key: str) -> None:
        """命中递增（热度 / 衰减用）。"""
        self._conn.execute(
            "UPDATE repairs SET hit_count = hit_count + 1, last_hit_at = datetime('now')"
            " WHERE repair_key = ?",
            (repair_key,),
        )
        self._conn.commit()

    def set_verified(self, repair_key: str, verified: bool) -> None:
        """人工审核标记（人审队列确认后置 True）。"""
        self._conn.execute(
            "UPDATE repairs SET is_verified = ? WHERE repair_key = ?",
            (int(verified), repair_key),
        )
        self._conn.commit()

    def find_popup(self, signature: str) -> PopupFeature | None:
        row = self._conn.execute(
            "SELECT * FROM popups WHERE signature = ? LIMIT 1", (signature,)
        ).fetchone()
        return self._row_to_popup(row) if row else None

    def close(self) -> None:
        """关闭连接（幂等由 sqlite3 保证）。"""
        self._conn.close()

    def __enter__(self) -> SqliteKnowledgeStore:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def count_popups(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM popups").fetchone()[0]

    def count_repairs(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM repairs").fetchone()[0]

    @staticmethod
    def _row_to_repair(row: sqlite3.Row) -> RepairCase:
        return RepairCase(
            original_selector=row["original_selector"],
            new_selector=row["new_selector"],
            strategy=row["strategy"],
            confidence=row["confidence"],
            page_url=row["page_url"],
            dom_fingerprint=row["dom_fingerprint"],
            page_fingerprint=row["page_fingerprint"],
            repair_key=row["repair_key"],
            embedding=bytes(row["embedding"]) if row["embedding"] is not None else None,
            embedding_version=row["embedding_version"],
            hit_count=row["hit_count"],
            last_hit_at=row["last_hit_at"],
            is_verified=bool(row["is_verified"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_popup(row: sqlite3.Row) -> PopupFeature:
        return PopupFeature(
            signature=row["signature"],
            dismiss_selector=row["dismiss_selector"],
            category=row["category"],
        )
