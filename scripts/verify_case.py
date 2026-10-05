"""人工审核入口：把知识库里的修复案例标记为 `is_verified`（L3 防污染自动采纳的信任开关）。

**为什么需要它**（2026-10-08 审查 M3）：`KnowledgeBackend.set_verified()` 在内存与 SQLite 两端
都已实现，但框架内**没有任何调用入口**——而 `agent/strategies/semantic.py` 的 L3 采纳规则
"sim > 0.92 且 is_verified → 自动采纳"依赖这个标记。缺入口 = 该规则只能靠人工直连数据库，
防污染闭环缺一环。本脚本只改 `is_verified` 标记，**不自动改代码、不改定位器**（守 T15 人审边界）。

用法：
    python scripts/verify_case.py --list                       # 列出最近案例（含 repair_key/置信度/标记）
    python scripts/verify_case.py --repair-key <rk>            # 标记为"已人工审核"
    python scripts/verify_case.py --repair-key <rk> --unverify # 撤销标记
    python scripts/verify_case.py --selector "#submit-btn-old" # 按原定位器标记（该选择器下全部案例）
    python scripts/verify_case.py --list --db .cache/knowledge.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# 允许 `python scripts/verify_case.py` 直接运行（不必先 pip install -e .）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from selfheal.config import load_settings  # noqa: E402
from selfheal.knowledge.sqlite_store import SqliteKnowledgeStore  # noqa: E402

_LIST_SQL = (
    "SELECT repair_key, original_selector, new_selector, strategy, confidence,"
    " is_verified, created_at FROM repairs ORDER BY rowid DESC LIMIT ?"
)


def _default_db_path() -> str:
    """取配置里的知识库路径（settings.knowledge.path），便于与运行时一致。"""
    return load_settings().knowledge.path


def list_cases(db_path: str, limit: int = 20) -> list[tuple]:
    """只读列出最近案例（独立连接，避免为"看一眼"而实例化整个 store）。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        return conn.execute(_LIST_SQL, (limit,)).fetchall()
    finally:
        conn.close()


def _keys_for_selector(store: SqliteKnowledgeStore, selector: str) -> list[str]:
    """按原定位器取该选择器下的全部 repair_key（复用 store 的检索语义）。"""
    rows = store._conn.execute(  # noqa: SLF001 - 脚本内直读，避免为小工具扩公共 API
        "SELECT repair_key FROM repairs WHERE original_selector = ? AND repair_key IS NOT NULL",
        (selector,),
    ).fetchall()
    return [row["repair_key"] for row in rows]


def main(argv: list[str] | None = None) -> int:
    """命令行入口：--list 只读展示；--repair-key/--selector 写 is_verified。"""
    parser = argparse.ArgumentParser(description="知识库修复案例人工审核标记（L3 防污染闭环入口）")
    parser.add_argument("--db", default=None, help="知识库路径（默认取 settings.knowledge.path）")
    parser.add_argument("--list", action="store_true", help="列出最近案例")
    parser.add_argument("--limit", type=int, default=20, help="--list 条数（默认 20）")
    parser.add_argument("--repair-key", default=None, help="要标记的 repair_key（L1 精确键）")
    parser.add_argument("--selector", default=None, help="按原定位器标记该选择器下全部案例")
    parser.add_argument("--unverify", action="store_true", help="撤销标记（置回 False）")
    args = parser.parse_args(argv)

    db_path = args.db or _default_db_path()
    if not Path(db_path).exists():
        print(f"知识库不存在：{db_path}（先跑一次带自愈的用例再审核）")
        return 1

    if args.list:
        rows = list_cases(db_path, args.limit)
        if not rows:
            print("知识库暂无修复案例。")
            return 0
        print(f"{'repair_key':<34} {'verified':<9} {'conf':<6} selector → new_selector")
        for row in rows:
            key = row["repair_key"] or "(无 L1 键)"
            print(
                f"{key:<34} {str(bool(row['is_verified'])):<9} "
                f"{row['confidence']:<6} {row['original_selector']} → {row['new_selector']}"
            )
        if not (args.repair_key or args.selector):
            return 0

    keys: list[str] = []
    if args.repair_key:
        keys.append(args.repair_key)

    store = SqliteKnowledgeStore(db_path)
    try:
        if args.selector:
            keys.extend(_keys_for_selector(store, args.selector))
        keys = [key for key in dict.fromkeys(keys) if key]
        if not keys:
            print("没有命中任何案例：请用 --list 查看可用 repair_key（或确认 --selector 拼写）")
            return 1
        verified = not args.unverify
        for key in keys:
            store.set_verified(key, verified)
        print(f"已把 {len(keys)} 条案例标记为 is_verified={verified}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
