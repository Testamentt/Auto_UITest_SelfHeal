"""单元测试：知识库 SQLite 持久化 + DOM 指纹 + 工厂（不依赖浏览器）。"""

import logging
import sqlite3

import pytest

from selfheal.agent.dom import dom_fingerprint
from selfheal.config import Settings
from selfheal.knowledge.factory import build_knowledge_store
from selfheal.knowledge.schema import PopupFeature, RepairCase
from selfheal.knowledge.sqlite_store import SqliteKnowledgeStore
from selfheal.knowledge.store import KnowledgeStore

pytestmark = pytest.mark.unit


def _case(selector="#old", new='[data-testid="x"]', confidence=0.9, fingerprint=None):
    return RepairCase(
        original_selector=selector,
        new_selector=new,
        strategy="heuristic",
        confidence=confidence,
        page_url="file:///demo",
        dom_fingerprint=fingerprint,
    )


def test_sqlite_repair_roundtrip(tmp_path):
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    store.add_repair(_case())
    found = store.find_repair("#old")
    assert found is not None
    assert found.new_selector == '[data-testid="x"]'
    assert found.strategy == "heuristic"
    store.close()


def test_sqlite_popup_roundtrip(tmp_path):
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    store.add_popup(PopupFeature(signature="welcome-dialog", dismiss_selector="#close"))
    found = store.find_popup("welcome-dialog")
    assert found is not None
    assert found.dismiss_selector == "#close"
    assert store.find_popup("nonexistent") is None
    store.close()


def test_sqlite_persists_across_instances(tmp_path):
    db = str(tmp_path / "kb.db")
    first = SqliteKnowledgeStore(db)
    first.add_repair(_case())
    first.close()
    # 重新打开同一文件，数据仍在（持久化）
    second = SqliteKnowledgeStore(db)
    assert second.find_repair("#old") is not None
    second.close()


def test_find_repair_prefers_matching_fingerprint(tmp_path):
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    store.add_repair(_case(confidence=0.9, fingerprint="fp-A"))
    store.add_repair(_case(new="#other", confidence=0.8, fingerprint="fp-B"))
    # 查询时带 fp-B，应优先返回 fp-B 案例（即便置信度更低）
    found = store.find_repair("#old", dom_fingerprint="fp-B")
    assert found is not None and found.new_selector == "#other"
    # 不带指纹则按置信度取最高
    assert store.find_repair("#old").new_selector == '[data-testid="x"]'
    store.close()


def test_add_repair_upsert_dedup(tmp_path):
    """#10：同 (原,新,指纹) 重复沉淀 → upsert 更新，不产生重复行。"""
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    case = _case(fingerprint="fp")
    store.add_repair(case)
    store.add_repair(case)  # 重复写入
    store.add_repair(case)  # 再写一次
    assert store.count_repairs() == 1  # 去重生效
    store.close()


def test_add_repair_upsert_dedup_with_none_fingerprint(tmp_path):
    """审查 C2 回归：dom_fingerprint=None 时同样去重（SQLite UNIQUE 对 NULL 不生效的坑）。"""
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    case = _case(fingerprint=None)
    store.add_repair(case)
    store.add_repair(case)  # 重复写入（修复前会绕过冲突检测产生 2 行）
    store.add_repair(case)
    assert store.count_repairs() == 1  # 去重生效
    # 读取侧语义不变：dom_fingerprint 还原为 None
    found = store.find_repair("#old")
    assert found is not None and found.dom_fingerprint is None
    store.close()


def test_close_idempotent(tmp_path):
    """审查 M3：close 幂等（重复关闭安全，供生命周期收口复用）。"""
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    store.add_repair(_case())
    store.close()
    store.close()  # 二次 close 不抛异常


def test_factory_backend_selection(tmp_path):
    sqlite_settings = Settings()
    sqlite_settings.knowledge.backend = "sqlite"
    sqlite_settings.knowledge.path = str(tmp_path / "f.db")
    assert isinstance(build_knowledge_store(sqlite_settings), SqliteKnowledgeStore)

    memory_settings = Settings()
    memory_settings.knowledge.backend = "memory"
    assert isinstance(build_knowledge_store(memory_settings), KnowledgeStore)


def test_dom_fingerprint_stable_and_distinct():
    dom_a = '<html><body><button data-testid="go">Go</button></body></html>'
    dom_a_reordered = '<html><body>  <button data-testid="go">Go</button> </body></html>'
    dom_b = '<html><body><button data-testid="stop">Stop</button></body></html>'
    fa, fa2, fb = dom_fingerprint(dom_a), dom_fingerprint(dom_a_reordered), dom_fingerprint(dom_b)
    assert fa is not None and fa == fa2  # 同结构指纹一致
    assert fa != fb  # 不同结构指纹不同
    assert dom_fingerprint(None) is None
    assert dom_fingerprint("<html><body><div>text</div></body></html>") is None


# --- 2026-09-03 子代理复核（V5）修复点回归 ---


def test_add_repair_preserves_verified_on_upsert(tmp_path):
    """V5 复核：再次沉淀同一 (原,新,指纹) 不覆盖人工审核标记 is_verified。"""
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    case = RepairCase(
        original_selector="#old",
        new_selector='[data-testid="x"]',
        strategy="heuristic",
        confidence=0.9,
        page_url="file:///demo",
        dom_fingerprint="fp",
        repair_key="rk-1",
    )
    store.add_repair(case)
    store.set_verified("rk-1", True)
    store.add_repair(case)  # 再次沉淀（新案例 is_verified 默认 False）
    found = store.find_by_repair_key("rk-1")
    assert found is not None and found.is_verified is True  # 人审标记保留
    store.close()


def test_legacy_db_missing_columns_migrated(tmp_path):
    """V5 复核：Phase 5 A 前的旧库（缺语义化列）打开时自动补列，不炸、旧数据保留。"""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE repairs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " original_selector TEXT NOT NULL, new_selector TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO repairs (original_selector, new_selector) VALUES ('#old', '#new')")
    conn.execute(
        "CREATE TABLE popups (id INTEGER PRIMARY KEY AUTOINCREMENT, signature TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO popups (signature) VALUES ('sig')")
    conn.commit()
    conn.close()

    store = SqliteKnowledgeStore(str(db))
    assert store.count_repairs() == 1
    found = store.find_repair("#old")
    assert found is not None and found.new_selector == "#new"
    assert found.hit_count == 0  # 补列默认值生效
    assert store.find_popup("sig") is not None  # popups 补列后可读
    store.add_popup(PopupFeature(signature="sig", dismiss_selector="#ok"))  # upsert 不炸
    assert store.count_popups() == 1
    store.close()


def test_legacy_duplicate_rows_deduped_on_migrate(tmp_path):
    """V5 复核：旧库历史重复行在建唯一索引前清除（repairs 同键、popups 同签名）。"""
    db = tmp_path / "dup.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE repairs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " original_selector TEXT NOT NULL, new_selector TEXT NOT NULL, dom_fingerprint TEXT)"
    )
    conn.execute(
        "INSERT INTO repairs (original_selector, new_selector, dom_fingerprint)"
        " VALUES ('#a', '#n1', 'fp')"
    )
    conn.execute(
        "INSERT INTO repairs (original_selector, new_selector, dom_fingerprint)"
        " VALUES ('#a', '#n1', 'fp')"
    )
    conn.execute(
        "INSERT INTO repairs (original_selector, new_selector, dom_fingerprint)"
        " VALUES ('#a', '#n2', 'fp')"
    )
    conn.execute(
        "CREATE TABLE popups (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " signature TEXT NOT NULL, dismiss_selector TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO popups (signature, dismiss_selector) VALUES ('s', '#x1')")
    conn.execute("INSERT INTO popups (signature, dismiss_selector) VALUES ('s', '#x2')")
    conn.commit()
    conn.close()

    store = SqliteKnowledgeStore(str(db))
    assert store.count_repairs() == 2  # 同键重复行清 1，不同 new_selector 保留
    assert store.count_popups() == 1
    store.close()


def test_add_popup_upsert_latest_wins(tmp_path):
    """V5 复核：同 signature 弹窗 upsert（最新观察胜出），复现弹窗不再无界追加。"""
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    store.add_popup(PopupFeature(signature="cookie", dismiss_selector="#c1"))
    store.add_popup(PopupFeature(signature="cookie", dismiss_selector="#c2"))
    assert store.count_popups() == 1
    assert store.find_popup("cookie").dismiss_selector == "#c2"
    store.close()


def test_factory_degrades_to_memory_when_sqlite_broken(tmp_path, caplog):
    """V5 复核：sqlite 库文件损坏 → 记 warning 并降级 memory 后端，不炸 fixture。"""
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a sqlite database at all")
    settings = Settings()
    settings.knowledge.backend = "sqlite"
    settings.knowledge.path = str(bad)
    with caplog.at_level(logging.WARNING):
        store = build_knowledge_store(settings)
    assert isinstance(store, KnowledgeStore)
