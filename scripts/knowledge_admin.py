# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""知识库运维 CLI：统计 / 清理失效案例 / 去重 / 导出。

**为什么需要它**：`verify_case.py` 只负责人审标记（`is_verified`），缺"看全貌、清垃圾"的入口。
知识库长期运行会积累三类问题：
1. **失效案例**——页面又改版了，`new_selector` 早已不存在，但仍占着检索空间；
2. **低价值案例**——从未被命中过（`hit_count=0`）且未人审，只是冷启动噪声；
3. **重复行**——早期 schema 无唯一索引时代留下的同键多行。

**安全边界（默认 dry-run）**：
- 所有写操作默认只**打印将影响的行数**，必须显式加 `--apply` 才真删；
- `--prune` 只删「未人审 + 命中 0 次 + 超过 N 天」的案例——已人审的、被复用过的永不自动删；
- 不做任何"改代码 / 改定位器"的事，只动知识库数据（守人审边界）。

用法：
    python scripts/knowledge_admin.py --stats
    python scripts/knowledge_admin.py --prune --older-than 30            # 预览
    python scripts/knowledge_admin.py --prune --older-than 30 --apply    # 真删
    python scripts/knowledge_admin.py --dedupe                           # 预览
    python scripts/knowledge_admin.py --dedupe --apply
    python scripts/knowledge_admin.py --export cases.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 允许 `python scripts/knowledge_admin.py` 直接运行（不必先 pip install -e .）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from selfheal.config import load_settings  # noqa: E402

# upsert 唯一键（与 schema 的 UNIQUE(original_selector, new_selector, dom_fingerprint) 对齐；
# NULL 指纹归一为 "" 才能正确分组——SQLite 的 UNIQUE 对 NULL 不生效）
_DEDUPE_GROUP = "COALESCE(dom_fingerprint, '')"


def _connect(db_path: str) -> sqlite3.Connection:
    """只读/读写连接（WAL 与 busy_timeout 与运行时一致，避免与正在跑的用例抢锁）。"""
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _default_db_path() -> str:
    return load_settings().knowledge.path


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------


def _ensure_row_factory(conn: sqlite3.Connection) -> sqlite3.Connection:
    """保证连接按列名取数（幂等）。

    这些运维函数会被 `main()` 之外的内嵌调用（单测、脚本内复用）传入**裸连接**；
    若调用方没设 `row_factory`，`row["col"]` 会抛 `IndexError`（实测踩到过）。
    统一在函数入口归一，避免每个函数各自处理两种取数方式。
    """
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row
    return conn


def collect_stats(conn: sqlite3.Connection) -> dict:
    """汇总知识库全貌：总量、按策略、按验证状态、命中分布、时间范围。"""
    conn = _ensure_row_factory(conn)
    if not _table_exists(conn, "repairs"):
        return {"total": 0, "empty": True}
    total = conn.execute("SELECT COUNT(*) FROM repairs").fetchone()[0]
    if total == 0:
        return {"total": 0, "empty": True}
    by_strategy = {
        row["strategy"]: row["n"]
        for row in conn.execute(
            "SELECT strategy, COUNT(*) AS n FROM repairs GROUP BY strategy ORDER BY n DESC"
        )
    }
    verified = conn.execute("SELECT COUNT(*) FROM repairs WHERE is_verified = 1").fetchone()[0]
    never_hit = conn.execute(
        "SELECT COUNT(*) FROM repairs WHERE COALESCE(hit_count, 0) = 0"
    ).fetchone()[0]
    span = conn.execute(
        "SELECT MIN(created_at) AS oldest, MAX(created_at) AS newest FROM repairs"
    ).fetchone()
    stale = conn.execute("SELECT COUNT(*) FROM repairs WHERE repair_key IS NULL").fetchone()[0]
    return {
        "total": total,
        "by_strategy": by_strategy,
        "verified": verified,
        "unverified": total - verified,
        "never_hit": never_hit,
        "no_repair_key": stale,
        "oldest": span["oldest"],
        "newest": span["newest"],
    }


def render_stats(stats: dict) -> str:
    """把统计结果渲染为可读文本。"""
    if stats.get("empty"):
        return "知识库为空（或尚无 repairs 表）：先跑一次带自愈的用例再统计。"
    lines = [
        f"案例总数       ：{stats['total']}",
        f"已人审 / 未人审：{stats['verified']} / {stats['unverified']}",
        f"从未被命中     ：{stats['never_hit']}（冷启动噪声候选）",
        f"缺少 L1 键     ：{stats['no_repair_key']}（无法被精确命中复用）",
        f"时间范围       ：{stats['oldest']} → {stats['newest']}",
        "按策略分布     ：" + "、".join(f"{k}={v}" for k, v in stats["by_strategy"].items()),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 清理
# ---------------------------------------------------------------------------


def _cutoff_iso(days: int) -> str:
    """`days` 天前的 UTC ISO 串（与知识库 created_at 同格式，可直接比较）。"""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _parse_created_at(raw: str | None) -> datetime | None:
    """把 `created_at` 解析为 tz-aware datetime；无法解析返回 None（保守：不删）。

    兼容两种历史格式（见 `knowledge/base.py` 的时间契约）：
    - ISO 8601：``2026-10-13T02:41:24+00:00``（当前写入格式）；
    - SQLite 历史格式：``2026-09-01 03:09:05``（空格分隔、无时区，早期 CURRENT_TIMESTAMP 产物）。
    """
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _select_prune_candidates(conn: sqlite3.Connection, cutoff: datetime) -> list[int]:
    """挑出可删行的 rowid：「未人审 + 从未命中 + 创建时间早于 cutoff（**按时间解析后比较**）」。

    为什么在 Python 侧比时间而不是 SQL `created_at < ?`：`created_at` 存在两种历史格式，
    字符串比较在"同一天、晚于 cutoff 时刻"的旧格式行上会误判为超龄（实测：
    ``'2026-10-13 05:41:24' < '2026-10-13T02:41:24+00:00'`` 为 True，而该行其实是 3 小时后创建的）。
    prune 是删除操作，宁可多写几行代码也不能误删。
    """
    # 用位置索引取列（0=rowid, 1=created_at）：本函数不依赖调用方是否设了 row_factory，
    # 传进来的连接可能是裸 sqlite3.connect（`main()` 设了 row_factory，但单测/内嵌调用未必）。
    rows = conn.execute(
        "SELECT rowid, created_at FROM repairs WHERE is_verified = 0 AND COALESCE(hit_count, 0) = 0"
    ).fetchall()
    candidates: list[int] = []
    for row in rows:
        rowid, raw_created = row[0], row[1]
        created = _parse_created_at(raw_created)
        # 解析失败 ≠ 超龄：无法判断年龄就保留（保守口径）
        if created is not None and created < cutoff:
            candidates.append(rowid)
    return candidates


def prune_stale(conn: sqlite3.Connection, *, older_than_days: int, apply: bool) -> dict:
    """删除「未人审 + 从未命中 + 超过 N 天」的案例。

    保护口径：`is_verified=1`（人审过的）与 `hit_count>0`（被复用过的）**永不**自动删除——
    前者是信任来源，后者是已被证明有价值的经验。`created_at` 为空或无法解析同样保留。
    """
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    cutoff = cutoff_dt.isoformat(timespec="seconds")
    rowids = _select_prune_candidates(conn, cutoff_dt)
    if apply and rowids:
        conn.executemany("DELETE FROM repairs WHERE rowid = ?", [(rid,) for rid in rowids])
        conn.commit()
    return {"candidates": len(rowids), "cutoff": cutoff, "applied": bool(apply and rowids)}


# ---------------------------------------------------------------------------
# 去重
# ---------------------------------------------------------------------------


def find_duplicates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """按 upsert 键分组，返回存在多行的组（含行数与将被保留的 rowid）。

    **保留策略（防丢信任与热度）**：优先保留「已人审」的行，其次保留「命中次数最高」的行，
    最后才用 rowid 最小者兜底。此前只按 `MIN(rowid)` 保留——实测会把 `is_verified=1` 与
    `hit_count=5` 的行删掉，只留下未审未命中的那行，直接瓦解 L3 的信任来源（哑删除）。
    """
    conn = _ensure_row_factory(conn)
    return conn.execute(
        f"""SELECT original_selector, new_selector, {_DEDUPE_GROUP} AS fp, COUNT(*) AS n,
                   MIN(rowid) AS first_rowid,
                   (SELECT rowid FROM repairs r2
                     WHERE r2.original_selector = r.original_selector
                       AND r2.new_selector = r.new_selector
                       AND COALESCE(r2.dom_fingerprint, '') = COALESCE(r.dom_fingerprint, '')
                     ORDER BY r2.is_verified DESC, COALESCE(r2.hit_count, 0) DESC, r2.rowid ASC
                     LIMIT 1) AS keep_rowid
            FROM repairs r
            GROUP BY original_selector, new_selector, {_DEDUPE_GROUP}
            HAVING COUNT(*) > 1
            ORDER BY n DESC"""  # noqa: S608 - 常量片段，无外部输入
    ).fetchall()


def dedupe(conn: sqlite3.Connection, *, apply: bool) -> dict:
    """同键多行合并到一行（保留哪一行见 `find_duplicates` 的保留策略）。

    淘汰顺序：`is_verified` 优先 → `hit_count` 次之 → rowid 最小者兜底。
    这样"已人审"与"被复用过的"信息不会被去重吞掉（此前只保 MIN(rowid)，会丢信任标记与热度）。
    """
    groups = find_duplicates(conn)
    removable = sum(row["n"] - 1 for row in groups)
    if apply and removable:
        for row in groups:
            conn.execute(
                f"""DELETE FROM repairs
                    WHERE original_selector = ? AND new_selector = ?
                      AND {_DEDUPE_GROUP} = ? AND rowid != ?""",  # noqa: S608
                (row["original_selector"], row["new_selector"], row["fp"], row["keep_rowid"]),
            )
        conn.commit()
    return {"groups": len(groups), "removable": removable, "applied": bool(apply and removable)}


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def export_cases(conn: sqlite3.Connection) -> list[dict]:
    """导出全部案例为 JSON 友好结构（向量 BLOB 转十六进制，便于跨环境对比）。

    `_ensure_row_factory` 必填：`dict(row)` 只有在 `sqlite3.Row` 下才按列名映射；
    裸连接拿到的是 tuple，会抛 `ValueError: dictionary update sequence element #2 ...`。
    """
    conn = _ensure_row_factory(conn)
    rows = conn.execute(
        """SELECT original_selector, new_selector, strategy, confidence, page_url,
                  dom_fingerprint, page_fingerprint, repair_key, embedding_version,
                  hit_count, last_hit_at, is_verified, created_at
           FROM repairs ORDER BY rowid"""
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="知识库运维（统计 / 清理 / 去重 / 导出）")
    parser.add_argument("--db", default=None, help="知识库路径（默认取 settings.knowledge.path）")
    parser.add_argument("--stats", action="store_true", help="显示统计（只读）")
    parser.add_argument("--prune", action="store_true", help="清理失效案例（默认仅预览）")
    parser.add_argument(
        "--older-than", type=int, default=30, help="--prune 的年龄门槛（天，默认 30）"
    )
    parser.add_argument("--dedupe", action="store_true", help="按 upsert 键去重（默认仅预览）")
    parser.add_argument("--export", default=None, help="导出为 JSON 文件路径")
    parser.add_argument("--apply", action="store_true", help="真正执行写操作（默认 dry-run）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not any([args.stats, args.prune, args.dedupe, args.export]):
        build_parser().print_help()
        return 1

    db_path = args.db or _default_db_path()
    if not Path(db_path).exists():
        print(f"知识库不存在：{db_path}（先跑一次带自愈的用例）")
        return 1

    conn = _connect(db_path)
    try:
        if args.stats:
            print(render_stats(collect_stats(conn)))

        if args.prune:
            if args.older_than < 0:
                print("--older-than 不能为负")
                return 1
            result = prune_stale(conn, older_than_days=args.older_than, apply=args.apply)
            verb = "已删除" if result["applied"] else "将删除"
            print(
                f"{verb} {result['candidates']} 条（未人审 + 从未命中 + 早于 {result['cutoff']}）"
                + ("" if args.apply else "；加 --apply 才真删")
            )

        if args.dedupe:
            result = dedupe(conn, apply=args.apply)
            verb = "已删除" if result["applied"] else "将删除"
            print(
                f"{verb} {result['removable']} 条重复（涉及 {result['groups']} 组 upsert 键）"
                + ("" if args.apply else "；加 --apply 才真删")
            )

        if args.export:
            cases = export_cases(conn)
            Path(args.export).write_text(
                json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"已导出 {len(cases)} 条案例到 {args.export}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
