"""SQLite 知识库后端（stdlib sqlite3 + numpy 向量）。

持久化修复案例与弹窗特征，重启后仍可命中复用（"越用越聪明"）。
Phase 5 A 语义化：
- L1：find_by_repair_key 按确定性 repair_key 精确命中（硬短路）。
- L3：find_semantic 按 page_fingerprint 分桶 → 桶内 numpy 余弦（向量存 BLOB，避免 JSON 反序列化瓶颈）。
- 防污染：hit_count/last_hit_at 递增、is_verified 标记（人工审核）。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from selfheal.knowledge.schema import PopupFeature, RepairCase, RepairQuery

logger = logging.getLogger(__name__)

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
CREATE TABLE IF NOT EXISTS popups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signature TEXT NOT NULL,
    dismiss_selector TEXT NOT NULL,
    category TEXT DEFAULT 'generic'
);
"""

# 索引在迁移之后统一创建：旧库缺列时 CREATE INDEX 会直接抛 OperationalError（V5 复核）
_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_repairs_selector ON repairs(original_selector);
CREATE INDEX IF NOT EXISTS idx_repairs_key ON repairs(repair_key);
CREATE INDEX IF NOT EXISTS idx_repairs_semantic
    ON repairs(page_fingerprint, embedding_version);
-- #10：同 (原选择器, 新选择器, 指纹) 去重，避免记录无界增长
CREATE UNIQUE INDEX IF NOT EXISTS idx_repairs_unique
    ON repairs(original_selector, new_selector, dom_fingerprint);
-- V5 复核：signature 唯一（add_popup 走 upsert），复现弹窗不再无界追加重复行
CREATE UNIQUE INDEX IF NOT EXISTS idx_popups_signature ON popups(signature);
"""

# V5 复核：旧库平滑迁移——Phase 5 A 前创建的持久库缺列时逐列补齐（ALTER TABLE ADD COLUMN），
# 而不是让建索引直接炸掉整个会话（fixture 阶段报错且无降级）。补列后旧数据原样保留。
_REPAIR_COLUMNS: dict[str, str] = {
    "strategy": "TEXT",
    "confidence": "REAL",
    "page_url": "TEXT",
    "dom_fingerprint": "TEXT",
    "page_fingerprint": "TEXT",
    "repair_key": "TEXT",
    "embedding": "BLOB",
    "embedding_version": "TEXT",
    "hit_count": "INTEGER DEFAULT 0",
    "last_hit_at": "TEXT",
    "is_verified": "INTEGER DEFAULT 0",
    "created_at": "TEXT",
}
_POPUP_COLUMNS: dict[str, str] = {
    "dismiss_selector": "TEXT",
    "category": "TEXT DEFAULT 'generic'",
}


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """读表现有列名（PRAGMA table_info）。"""
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_legacy_tables(conn: sqlite3.Connection) -> list[str]:
    """旧库缺列补齐；返回实际补上的列（table.column），供日志/测试断言。"""
    added: list[str] = []
    for table, wanted in (("repairs", _REPAIR_COLUMNS), ("popups", _POPUP_COLUMNS)):
        existing = _existing_columns(conn, table)
        for name, decl in wanted.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                added.append(f"{table}.{name}")
    return added


def _dedupe_legacy_rows(conn: sqlite3.Connection) -> int:
    """建 UNIQUE 索引前清除历史重复行（保留最小 rowid）；返回清除的行数。

    仅历史库需要：新库写入路径本身按唯一键 upsert，不会产生重复行。
    """
    removed = 0
    for table, keys in (
        ("repairs", "original_selector, new_selector, dom_fingerprint"),
        ("popups", "signature"),
    ):
        cur = conn.execute(
            f"DELETE FROM {table} WHERE rowid NOT IN"
            f" (SELECT MIN(rowid) FROM {table} GROUP BY {keys})"
        )
        removed += max(cur.rowcount, 0)
    return removed


class SqliteKnowledgeStore:
    """基于 SQLite 的知识库实现（遵循 KnowledgeBackend 接口）。"""

    def __init__(self, path: str):
        self._path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # T21 并行兼容：多进程共享同一知识库文件时（如 xdist 并行 / 多项目共用），
        # 写冲突以 busy_timeout 排队等待（30s 上限）而非立即抛 "database is locked"；
        # WAL 允许读写并发（默认 DELETE 模式整库锁）。临时库 / 单进程下同样无害。
        self._conn = sqlite3.connect(path, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 30000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_SCHEMA)
        # V5 复核：先迁移（补列 + 清历史重复行），再建索引——顺序反了旧库直接 OperationalError
        added = _migrate_legacy_tables(self._conn)
        deduped = _dedupe_legacy_rows(self._conn)
        # 旧库的 idx_popups_signature 可能是非唯一索引：先删再建（幂等），
        # 否则 add_popup 的 ON CONFLICT(signature) 找不到唯一约束会运行时报错。
        self._conn.execute("DROP INDEX IF EXISTS idx_popups_signature")
        self._conn.executescript(_INDEXES)
        self._conn.commit()
        if added or deduped:
            # R4：迁移不是静默行为，留下审计日志（补了哪些列、清了几行重复）
            logger.warning(
                "知识库 schema 迁移完成 path=%s 补列=%s 去重行=%s", path, added or "无", deduped
            )

    def add_repair(self, case: RepairCase) -> None:
        # #10 upsert：同 (原,新,指纹) 已存在则更新，不重复插入。
        # dom_fingerprint 归一化（None → ""）：SQLite UNIQUE 索引对 NULL 不生效（NULL ≠ NULL），
        # 不归一化会使"无指纹"（页面无可交互元素）的重复修复绕过冲突检测、无界增长（审查 C2）。
        fp = case.dom_fingerprint or ""
        self._conn.execute(
            "INSERT INTO repairs"
            " (original_selector, new_selector, strategy, confidence, page_url, dom_fingerprint,"
            "  page_fingerprint, repair_key, embedding, embedding_version, is_verified)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(original_selector, new_selector, dom_fingerprint)"
            # V5 复核：is_verified 刻意不进 DO UPDATE 列——再次沉淀不覆盖人工审核标记，
            # 否则 L3 防污染自动采纳（sim>0.92 且 is_verified）依赖的信任状态被静默清除。
            " DO UPDATE SET strategy=excluded.strategy, confidence=excluded.confidence,"
            "               page_url=excluded.page_url, page_fingerprint=excluded.page_fingerprint,"
            "               repair_key=excluded.repair_key, embedding=excluded.embedding,"
            "               embedding_version=excluded.embedding_version",
            (
                case.original_selector,
                case.new_selector,
                case.strategy,
                case.confidence,
                case.page_url,
                fp,
                case.page_fingerprint,
                case.repair_key,
                case.embedding,
                case.embedding_version,
                int(case.is_verified),
            ),
        )
        self._conn.commit()

    def add_popup(self, feature: PopupFeature) -> None:
        """V5 复核：按 signature upsert——复现弹窗每次成功关闭不再无界追加重复行。

        最新观察胜出（站点改版后关闭定位器随之更新）；依赖 idx_popups_signature 唯一索引。
        """
        self._conn.execute(
            "INSERT INTO popups (signature, dismiss_selector, category) VALUES (?, ?, ?)"
            " ON CONFLICT(signature) DO UPDATE SET"
            " dismiss_selector=excluded.dismiss_selector, category=excluded.category",
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

        rows = self._conn.execute(
            "SELECT * FROM repairs WHERE page_fingerprint = ? AND embedding_version = ?"
            " AND embedding IS NOT NULL",
            (page_fingerprint, embedding_version),
        ).fetchall()
        if not rows:
            return []
        qv = np.frombuffer(query_vec, dtype=np.float32)
        # 审查 C3：防御脏数据——长度与查询向量不符的行跳过（版本号已含 dim，
        # 此处兜底旧库 / 手改库残留的异维向量，避免 np.stack 直接 ValueError 崩溃）。
        valid = [
            r for r in rows if r["embedding"] is not None and len(r["embedding"]) == len(query_vec)
        ]
        if not valid:
            return []
        matrix = np.stack([np.frombuffer(r["embedding"], dtype=np.float32) for r in valid])
        sims = matrix @ qv  # 向量已归一化 → 余弦
        results: list[tuple[RepairCase, float]] = []
        for idx in np.argsort(-sims):
            sim = float(sims[idx])
            if sim < threshold:
                break
            results.append((self._row_to_repair(valid[idx]), sim))
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
            dom_fingerprint=row["dom_fingerprint"]
            or None,  # 写侧归一化 ""（None→"" 防 NULL 去重失效），读侧还原语义
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
