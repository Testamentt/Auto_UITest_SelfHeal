"""单元测试：pytest-xdist 并行兼容（T21）——worker 判定 / 分片聚合 / SQLite 并发防御。"""

from types import SimpleNamespace

import pytest

from selfheal.reporting.hooks import HealingRecord
from tests.conftest import (
    _cleanup_shards,
    _finalize_healing_reports,
    _read_shards,
    _write_shard,
    is_xdist_worker,
)

pytestmark = pytest.mark.unit


def _record(**overrides):
    base = {
        "original_selector": "#old",
        "new_selector": "#new",
        "strategy": "heuristic",
        "confidence": 0.95,
        "root_cause": "not_found",
        "success": True,
        "verified": True,
    }
    base.update(overrides)
    return HealingRecord(**base)


# --- worker 判定 ---


def test_is_xdist_worker_true_for_worker(monkeypatch):
    assert is_xdist_worker(SimpleNamespace(workerinput={"workerid": "gw0"})) is True


def test_is_xdist_worker_false_for_plain_session():
    assert is_xdist_worker(SimpleNamespace()) is False  # 单进程 / controller：无 workerinput


# --- 分片读写与清理 ---


def test_shard_roundtrip_rebuilds_healing_records(tmp_path):
    _write_shard(tmp_path, "gw0", [_record()], {"llm_calls": 2})
    records, stats = _read_shards(tmp_path)
    assert len(records) == 1 and isinstance(records[0], HealingRecord)  # 重建为 dataclass
    assert records[0].strategy == "heuristic"
    assert stats == {"llm_calls": 2}


def test_shards_merge_records_and_sum_stats(tmp_path):
    _write_shard(tmp_path, "gw0", [_record()], {"llm_calls": 1, "vlm_calls": 0})
    _write_shard(tmp_path, "gw1", [_record(success=False, verified=False)], {"llm_calls": 1})
    records, stats = _read_shards(tmp_path)
    assert len(records) == 2 and stats == {"llm_calls": 2, "vlm_calls": 0}


def test_read_shards_missing_dir_and_broken_shard(tmp_path):
    records, stats = _read_shards(tmp_path / "nonexistent")
    assert records == [] and stats == {}
    (tmp_path / ".healing-shard-gw0.json").write_text("{broken", encoding="utf-8")
    records, stats = _read_shards(tmp_path)  # 损坏分片跳过不炸
    assert records == []


def test_cleanup_shards_removes_all(tmp_path):
    _write_shard(tmp_path, "gw0", [_record()], {})
    _write_shard(tmp_path, "gw1", [_record()], {})
    _cleanup_shards(tmp_path)
    assert list(tmp_path.glob(".healing-shard-*.json")) == []


# --- 聚合出口（finalize）---


def test_finalize_worker_writes_shard_only(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # reports/ 落在 tmp
    worker_config = SimpleNamespace(workerinput={"workerid": "gw3"})
    _finalize_healing_reports(worker_config, [_record()], {"llm_calls": 1})
    assert list((tmp_path / "reports").glob(".healing-shard-gw3.json"))  # 分片已写
    assert not (tmp_path / "reports" / "dashboard.html").exists()  # worker 不写看板


def test_finalize_controller_merges_shards_and_writes_dashboard(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _write_shard(tmp_path / "reports", "gw0", [_record()], {"llm_calls": 2})
    _finalize_healing_reports(SimpleNamespace(), [], {})  # controller：本地无记录，全靠分片
    dashboard = tmp_path / "reports" / "dashboard.html"
    records_json = tmp_path / "reports" / "healing-records.json"
    assert dashboard.exists() and "自愈" in dashboard.read_text(encoding="utf-8")
    assert records_json.exists()
    assert list((tmp_path / "reports").glob(".healing-shard-*.json")) == []  # 聚合后清理


def test_finalize_plain_run_keeps_single_process_behavior(monkeypatch, tmp_path):
    """无 xdist（单进程）：无分片时行为与既有路径一致（本地记录直接写看板）。"""
    monkeypatch.chdir(tmp_path)
    _finalize_healing_reports(SimpleNamespace(), [_record()], {"llm_calls": 1})
    assert (tmp_path / "reports" / "dashboard.html").exists()
    assert list((tmp_path / "reports").glob(".healing-shard-*.json")) == []


def test_finalize_no_records_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _finalize_healing_reports(SimpleNamespace(), [], {})
    assert not (tmp_path / "reports" / "dashboard.html").exists()


# --- SQLite 并发防御（pragma 配置生效）---


def test_sqlite_busy_timeout_and_wal_enabled(tmp_path):
    from selfheal.knowledge.sqlite_store import SqliteKnowledgeStore

    store = SqliteKnowledgeStore(str(tmp_path / "knowledge.db"))
    try:
        busy = store._conn.execute("PRAGMA busy_timeout").fetchone()[0]
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert busy == 30000
        assert mode == "wal"
    finally:
        store.close()


def test_sqlite_two_connections_interleaved_writes(tmp_path):
    """两个连接交错写（冒烟级并发）：busy_timeout 下无 'database is locked'。"""
    from selfheal.knowledge.schema import RepairCase
    from selfheal.knowledge.sqlite_store import SqliteKnowledgeStore

    db = tmp_path / "shared.db"
    stores = [SqliteKnowledgeStore(str(db)) for _ in range(2)]
    try:
        for i, store in enumerate(stores):
            store.add_repair(
                RepairCase(
                    original_selector=f"#old-{i}",
                    new_selector=f"#new-{i}",
                    strategy="heuristic",
                    confidence=0.9,
                    page_url="file:///demo",
                    repair_key=f"key-{i}",
                )
            )
        assert stores[0].count_repairs() >= 1
    finally:
        for store in stores:
            store.close()
