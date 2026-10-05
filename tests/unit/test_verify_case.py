"""单元测试：人工审核入口脚本（M3）——把知识库案例标记 is_verified（不触网、不碰真库）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import verify_case  # noqa: E402

from selfheal.knowledge.schema import RepairCase  # noqa: E402
from selfheal.knowledge.sqlite_store import SqliteKnowledgeStore  # noqa: E402

pytestmark = pytest.mark.unit


def _seed(db_path: Path) -> None:
    store = SqliteKnowledgeStore(str(db_path))
    try:
        store.add_repair(
            RepairCase(
                original_selector="#old",
                new_selector="#new",
                strategy="heuristic",
                confidence=0.9,
                page_url="u",
                repair_key="rk-1",
            )
        )
    finally:
        store.close()


def _is_verified(db_path: Path, key: str) -> bool:
    store = SqliteKnowledgeStore(str(db_path))
    try:
        return bool(store.find_by_repair_key(key).is_verified)
    finally:
        store.close()


def test_list_shows_cases(tmp_path, capsys):
    db = tmp_path / "k.db"
    _seed(db)
    assert verify_case.main(["--db", str(db), "--list"]) == 0
    out = capsys.readouterr().out
    assert "rk-1" in out and "#old" in out


def test_mark_by_repair_key(tmp_path, capsys):
    db = tmp_path / "k.db"
    _seed(db)
    assert _is_verified(db, "rk-1") is False
    assert verify_case.main(["--db", str(db), "--repair-key", "rk-1"]) == 0
    assert _is_verified(db, "rk-1") is True
    assert "标记为 is_verified=True" in capsys.readouterr().out


def test_unverify(tmp_path):
    db = tmp_path / "k.db"
    _seed(db)
    verify_case.main(["--db", str(db), "--repair-key", "rk-1"])
    assert verify_case.main(["--db", str(db), "--repair-key", "rk-1", "--unverify"]) == 0
    assert _is_verified(db, "rk-1") is False


def test_mark_by_selector(tmp_path):
    db = tmp_path / "k.db"
    _seed(db)
    assert verify_case.main(["--db", str(db), "--selector", "#old"]) == 0
    assert _is_verified(db, "rk-1") is True


def test_unknown_key_reports_and_fails(tmp_path, capsys):
    db = tmp_path / "k.db"
    _seed(db)
    assert verify_case.main(["--db", str(db), "--repair-key", ""]) == 1
    assert "没有命中任何案例" in capsys.readouterr().out


def test_missing_db_reports_and_fails(tmp_path, capsys):
    assert verify_case.main(["--db", str(tmp_path / "absent.db"), "--list"]) == 1
    assert "知识库不存在" in capsys.readouterr().out
