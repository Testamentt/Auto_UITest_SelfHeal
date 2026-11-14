# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""单元测试：知识库运维 CLI（`scripts/knowledge_admin.py`，不依赖浏览器 / 网络）。

覆盖的策略性契约（比"函数能跑"更重要）：

1. **写操作默认 dry-run**：不加 `--apply` 时绝不改库——运维脚本误删知识库是不可逆事故；
2. **保护口径**：`--prune` 只删「未人审 + 从未命中 + 超龄」的案例，
   已人审 / 被复用过的**永不**自动删（前者是信任来源，后者已被证明有价值）；
3. **去重与读侧并列口径一致**：保留 rowid 最小者（与 `find_repair` 的 `rowid ASC` 同源）；
4. **统计与导出只读**：不改动任何数据。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# scripts/ 不是包：按路径导入（与脚本自身 `sys.path.insert` 的做法一致）
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import knowledge_admin as ka  # noqa: E402

_SCHEMA = """
CREATE TABLE repairs (
    original_selector TEXT NOT NULL,
    new_selector TEXT NOT NULL,
    strategy TEXT NOT NULL,
    confidence REAL NOT NULL,
    page_url TEXT NOT NULL,
    dom_fingerprint TEXT,
    page_fingerprint TEXT,
    repair_key TEXT,
    embedding BLOB,
    embedding_version TEXT,
    hit_count INTEGER DEFAULT 0,
    last_hit_at TEXT,
    is_verified INTEGER DEFAULT 0,
    created_at TEXT
);
"""


def _insert(
    conn: sqlite3.Connection,
    *,
    created_at: str,
    is_verified: int = 0,
    hit_count: int = 0,
    original: str = "#old",
    new: str = "#new",
    fingerprint: str | None = None,
    repair_key: str = "rk-1",
) -> None:
    conn.execute(
        """INSERT INTO repairs (original_selector, new_selector, strategy, confidence, page_url,
                                dom_fingerprint, repair_key, hit_count, is_verified, created_at)
           VALUES (?, ?, 'heuristic', 0.9, 'demo://page', ?, ?, ?, ?, ?)""",
        (original, new, fingerprint, repair_key, hit_count, is_verified, created_at),
    )


@pytest.fixture
def db(tmp_path) -> str:
    path = tmp_path / "knowledge.db"
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return str(path)


def _conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _count(db_path: str) -> int:
    conn = _conn(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM repairs").fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. dry-run 是默认行为
# ---------------------------------------------------------------------------


def test_prune_dry_run_does_not_delete(db):
    """不加 --apply：只统计候选，一行都不删（防误删）。"""
    conn = _conn(db)
    _insert(conn, created_at="2020-01-01T00:00:00+00:00")
    conn.commit()
    try:
        result = ka.prune_stale(conn, older_than_days=30, apply=False)
        assert result["candidates"] == 1
        assert result["applied"] is False
    finally:
        conn.close()
    assert _count(db) == 1


def test_dedupe_dry_run_does_not_delete(db):
    conn = _conn(db)
    _insert(conn, created_at="2026-01-01T00:00:00+00:00")
    _insert(conn, created_at="2026-01-02T00:00:00+00:00")
    conn.commit()
    try:
        result = ka.dedupe(conn, apply=False)
        assert result["groups"] == 1
        assert result["removable"] == 1
        assert result["applied"] is False
    finally:
        conn.close()
    assert _count(db) == 2


def test_prune_apply_deletes_candidates(db):
    conn = _conn(db)
    _insert(conn, created_at="2020-01-01T00:00:00+00:00")
    conn.commit()
    conn.close()

    conn = _conn(db)
    try:
        result = ka.prune_stale(conn, older_than_days=30, apply=True)
    finally:
        conn.close()
    assert result["applied"] is True
    assert _count(db) == 0


# ---------------------------------------------------------------------------
# 2. 保护口径：人审 / 命中过 / 未超龄 一律不删
# ---------------------------------------------------------------------------


def test_prune_keeps_verified_case(db):
    conn = _conn(db)
    _insert(conn, created_at="2020-01-01T00:00:00+00:00", is_verified=1)
    conn.commit()
    try:
        result = ka.prune_stale(conn, older_than_days=30, apply=True)
    finally:
        conn.close()
    assert result["candidates"] == 0
    assert _count(db) == 1


def test_prune_keeps_hit_case(db):
    """被复用过的案例已被证明有价值，即使很老、未人审也不删。"""
    conn = _conn(db)
    _insert(conn, created_at="2020-01-01T00:00:00+00:00", hit_count=3)
    conn.commit()
    try:
        result = ka.prune_stale(conn, older_than_days=30, apply=True)
    finally:
        conn.close()
    assert result["candidates"] == 0
    assert _count(db) == 1


def test_prune_keeps_recent_case(db):
    from selfheal.knowledge.base import utc_now_iso

    conn = _conn(db)
    _insert(conn, created_at=utc_now_iso())
    conn.commit()
    try:
        result = ka.prune_stale(conn, older_than_days=30, apply=True)
    finally:
        conn.close()
    assert result["candidates"] == 0
    assert _count(db) == 1


def test_prune_keeps_null_created_at(db):
    """created_at 为 NULL（历史脏数据）：无法判断年龄，保守保留。"""
    conn = _conn(db)
    _insert(conn, created_at=None)
    conn.commit()
    try:
        result = ka.prune_stale(conn, older_than_days=0, apply=True)
    finally:
        conn.close()
    assert result["candidates"] == 0


# ---------------------------------------------------------------------------
# 3. 去重保留 rowid 最小者（与 find_repair 的 rowid ASC 一致）
# ---------------------------------------------------------------------------


def test_dedupe_keeps_smallest_rowid(db):
    conn = _conn(db)
    _insert(conn, created_at="2026-01-01T00:00:00+00:00", new="#first")
    _insert(conn, created_at="2026-01-02T00:00:00+00:00", new="#first")
    conn.commit()
    conn.close()

    conn = _conn(db)
    try:
        ka.dedupe(conn, apply=True)
        rows = conn.execute("SELECT rowid, created_at FROM repairs ORDER BY rowid").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["created_at"] == "2026-01-01T00:00:00+00:00"  # 无更高优先级信息时保最早者


# --- 去重回归：去重不得丢掉人审标记与热度 ---


def test_dedupe_prefers_verified_row(db):
    """回归：同键多行时，**已人审**的行必须被保留（此前只保 MIN(rowid)，会删掉信任来源）。"""
    conn = _conn(db)
    _insert(conn, created_at="2026-01-01T00:00:00+00:00", is_verified=0)  # rowid 最小
    _insert(conn, created_at="2026-01-01T00:00:00+00:00", is_verified=1)  # 已人审
    conn.commit()
    conn.close()

    conn = _conn(db)
    try:
        ka.dedupe(conn, apply=True)
        rows = conn.execute("SELECT is_verified FROM repairs").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["is_verified"] == 1, "去重删掉了已人审行（L3 信任来源丢失）"


def test_dedupe_prefers_hit_row_over_unverified(db):
    """回归：无已人审行时，**被复用过的**（hit_count 高）行必须优先保留。"""
    conn = _conn(db)
    _insert(conn, created_at="2026-01-01T00:00:00+00:00", hit_count=0)
    _insert(conn, created_at="2026-01-01T00:00:00+00:00", hit_count=5)
    conn.commit()
    conn.close()

    conn = _conn(db)
    try:
        ka.dedupe(conn, apply=True)
        rows = conn.execute("SELECT hit_count FROM repairs").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["hit_count"] == 5, "去重删掉了被复用过的行（热度丢失）"


def test_dedupe_verified_beats_hit_count(db):
    """优先级：已人审 > 热度 > rowid。"""
    conn = _conn(db)
    _insert(conn, created_at="2026-01-01T00:00:00+00:00", hit_count=9, is_verified=0)
    _insert(conn, created_at="2026-01-01T00:00:00+00:00", hit_count=0, is_verified=1)
    conn.commit()
    conn.close()

    conn = _conn(db)
    try:
        ka.dedupe(conn, apply=True)
        rows = conn.execute("SELECT is_verified, hit_count FROM repairs").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["is_verified"] == 1


# --- prune 回归：时间比较不得用字符串 ---


def test_prune_parses_legacy_space_format(db):
    """回归：旧格式 `YYYY-MM-DD HH:MM:SS` 的**新**行不得被判超龄。

    字符串比较在"同一天、晚于 cutoff 时刻"上会出错：
    `'2026-10-13 05:41:24' < '2026-10-13T02:41:24+00:00'` 为 True，而该行其实更晚创建。
    """
    from datetime import datetime, timedelta, timezone

    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=30)
    just_after_cutoff_same_day = (cutoff_dt + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")

    conn = _conn(db)
    _insert(conn, created_at=just_after_cutoff_same_day)
    conn.commit()
    try:
        result = ka.prune_stale(conn, older_than_days=30, apply=True)
    finally:
        conn.close()
    assert result["candidates"] == 0, "旧格式的新行被判超龄（字符串比较缺陷）"
    assert _count(db) == 1


def test_prune_deletes_legacy_format_when_really_old(db):
    """旧格式且确实很旧 → 应被识别为候选（解析路径不能把旧格式一律当成"无法判断"）。"""
    conn = _conn(db)
    _insert(conn, created_at="2020-01-01 00:00:00")
    conn.commit()
    try:
        result = ka.prune_stale(conn, older_than_days=30, apply=True)
    finally:
        conn.close()
    assert result["candidates"] == 1
    assert _count(db) == 0


def test_prune_keeps_unparseable_created_at(db):
    """无法解析的 created_at：无法判断年龄 → 保守保留。"""
    conn = _conn(db)
    _insert(conn, created_at="not-a-date")
    conn.commit()
    try:
        result = ka.prune_stale(conn, older_than_days=0, apply=True)
    finally:
        conn.close()
    assert result["candidates"] == 0
    assert _count(db) == 1


# --- 裸连接回归：内嵌调用方可能不设 row_factory ---


def test_prune_and_dedupe_work_with_plain_connection(db):
    """回归：传入**未设 row_factory** 的裸连接时，prune / dedupe / stats 不得抛 IndexError。

    `main()` 自己设了 `row_factory`，但单测/内嵌调用会传裸连接——此前 `row["col"]`
    会抛 `IndexError: No item with that key`（实测踩到）。
    """
    conn = sqlite3.connect(db)  # 刻意不设 row_factory
    _insert(conn, created_at="2020-01-01 00:00:00")
    _insert(conn, created_at="2020-01-01 00:00:00")  # 同键 → 可去重
    conn.commit()
    try:
        assert ka.collect_stats(conn)["total"] == 2
        assert ka.dedupe(conn, apply=False)["removable"] == 1
        assert ka.prune_stale(conn, older_than_days=30, apply=False)["candidates"] == 2
    finally:
        conn.close()


def test_export_works_with_plain_connection(db):
    """回归：`export_cases` 对裸连接不得抛 ValueError（`dict(tuple)` 会崩）。"""
    conn = sqlite3.connect(db)  # 刻意不设 row_factory
    _insert(conn, created_at="2026-01-01T00:00:00+00:00")
    conn.commit()
    try:
        cases = ka.export_cases(conn)
    finally:
        conn.close()
    assert len(cases) == 1
    assert cases[0]["original_selector"] == "#old"
    assert isinstance(cases[0], dict)


def test_dedupe_treats_null_fingerprint_as_empty(db):
    """NULL 与 "" 指纹视为同一键（与 upsert 的 `COALESCE(dom_fingerprint,'')` 对齐）。"""
    conn = _conn(db)
    _insert(conn, created_at="2026-01-01T00:00:00+00:00", fingerprint=None)
    _insert(conn, created_at="2026-01-02T00:00:00+00:00", fingerprint="")
    conn.commit()
    try:
        result = ka.dedupe(conn, apply=True)
    finally:
        conn.close()
    assert result["removable"] == 1
    assert _count(db) == 1


def test_dedupe_keeps_distinct_keys(db):
    """不同 (original,new,fingerprint) 组合不是重复。"""
    conn = _conn(db)
    _insert(conn, created_at="2026-01-01T00:00:00+00:00", new="#a")
    _insert(conn, created_at="2026-01-01T00:00:00+00:00", new="#b")
    _insert(conn, created_at="2026-01-01T00:00:00+00:00", fingerprint="fp-x")
    conn.commit()
    try:
        result = ka.dedupe(conn, apply=True)
    finally:
        conn.close()
    assert result["removable"] == 0
    assert _count(db) == 3


# ---------------------------------------------------------------------------
# 4. 统计与导出只读
# ---------------------------------------------------------------------------


def test_stats_counts_and_readonly(db):
    conn = _conn(db)
    _insert(conn, created_at="2026-01-01T00:00:00+00:00", is_verified=1, hit_count=2)
    _insert(conn, created_at="2026-02-01T00:00:00+00:00", original="#x", repair_key="rk-2")
    conn.commit()
    try:
        stats = ka.collect_stats(conn)
    finally:
        conn.close()
    assert stats["total"] == 2
    assert stats["verified"] == 1
    assert stats["unverified"] == 1
    assert stats["never_hit"] == 1
    assert stats["by_strategy"] == {"heuristic": 2}
    assert stats["oldest"] == "2026-01-01T00:00:00+00:00"
    assert _count(db) == 2  # 统计不改数据


def test_stats_on_empty_db(db):
    conn = _conn(db)
    try:
        stats = ka.collect_stats(conn)
    finally:
        conn.close()
    assert stats["empty"] is True
    assert "为空" in ka.render_stats(stats)


def test_export_writes_json(db, tmp_path):
    conn = _conn(db)
    _insert(conn, created_at="2026-01-01T00:00:00+00:00")
    conn.commit()
    try:
        cases = ka.export_cases(conn)
    finally:
        conn.close()
    assert len(cases) == 1
    assert cases[0]["original_selector"] == "#old"
    assert _count(db) == 1


# ---------------------------------------------------------------------------
# 5. CLI 入口
# ---------------------------------------------------------------------------


def test_cli_requires_an_action(db, capsys):
    rc = ka.main(["--db", db])
    assert rc == 1


def test_cli_missing_db(tmp_path, capsys):
    rc = ka.main(["--db", str(tmp_path / "nope.db"), "--stats"])
    assert rc == 1
    assert "不存在" in capsys.readouterr().out


def test_cli_stats_prints_summary(db, capsys):
    conn = _conn(db)
    _insert(conn, created_at="2026-01-01T00:00:00+00:00")
    conn.commit()
    conn.close()
    assert ka.main(["--db", db, "--stats"]) == 0
    out = capsys.readouterr().out
    assert "案例总数" in out


def test_cli_prune_preview_mentions_apply_flag(db, capsys):
    conn = _conn(db)
    _insert(conn, created_at="2020-01-01T00:00:00+00:00")
    conn.commit()
    conn.close()
    assert ka.main(["--db", db, "--prune", "--older-than", "30"]) == 0
    out = capsys.readouterr().out
    assert "将删除 1 条" in out
    assert "--apply" in out
    assert _count(db) == 1  # 未 --apply，未删


def test_cli_export_to_file(db, tmp_path, capsys):
    conn = _conn(db)
    _insert(conn, created_at="2026-01-01T00:00:00+00:00")
    conn.commit()
    conn.close()
    out_file = tmp_path / "cases.json"
    assert ka.main(["--db", db, "--export", str(out_file)]) == 0
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert len(data) == 1


def test_cli_rejects_negative_older_than(db, capsys):
    conn = _conn(db)
    _insert(conn, created_at="2026-01-01T00:00:00+00:00")
    conn.commit()
    conn.close()
    assert ka.main(["--db", db, "--prune", "--older-than", "-5"]) == 1
