"""SQLite 知识库后端（stdlib sqlite3，零新增依赖）。

持久化修复案例与弹窗特征，重启后仍可命中复用（"越用越聪明"）。
find_repair 先按原定位器精确匹配，命中多条时优先 dom_fingerprint 相同者
（同一页面结构下的修复更可靠），其余按置信度降序。
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
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_repairs_selector ON repairs(original_selector);
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
        # #10 upsert：同 (原,新,指纹) 已存在则更新策略/置信度/url，不重复插入
        self._conn.execute(
            "INSERT INTO repairs"
            " (original_selector, new_selector, strategy, confidence, page_url, dom_fingerprint)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(original_selector, new_selector, dom_fingerprint)"
            " DO UPDATE SET strategy=excluded.strategy, confidence=excluded.confidence,"
            "               page_url=excluded.page_url",
            (
                case.original_selector,
                case.new_selector,
                case.strategy,
                case.confidence,
                case.page_url,
                case.dom_fingerprint,
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
        )

    @staticmethod
    def _row_to_popup(row: sqlite3.Row) -> PopupFeature:
        return PopupFeature(
            signature=row["signature"],
            dismiss_selector=row["dismiss_selector"],
            category=row["category"],
        )
