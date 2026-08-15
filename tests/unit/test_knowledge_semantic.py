"""单元测试：知识语义化 A2——repair_key、find_semantic、防污染（不触网）。"""

import pytest

from selfheal.agent.dom import compute_page_fingerprint, compute_repair_key
from selfheal.knowledge.schema import RepairCase, RepairQuery
from selfheal.knowledge.sqlite_store import SqliteKnowledgeStore
from selfheal.knowledge.store import KnowledgeStore
from selfheal.llm.embedding import NgramEmbedding

pytestmark = pytest.mark.unit

# 版本号动态获取（含 dim，见审查 C3）；硬编码会在调整默认 dim 时静默失配
VER = NgramEmbedding().embedding_version


def _vec(text: str) -> bytes:
    return NgramEmbedding().embed(text)


def _case(*, original="#a", new="#a-new", page_fp="pg1", text="确定", path="html>body>button", embedding=None):
    return RepairCase(
        original_selector=original,
        new_selector=new,
        strategy="heuristic",
        confidence=0.9,
        page_url="https://x/pg1",
        page_fingerprint=page_fp,
        repair_key=compute_repair_key(page_fp, path),
        embedding=embedding or _vec(text),
        embedding_version=VER,
    )


# --- repair_key 确定性（v2：不含文本，含 nth-of-type 索引）---


def test_repair_key_deterministic():
    a = compute_repair_key("pg1", "html>body>button")
    b = compute_repair_key("pg1", "html>body>button")
    assert a == b
    assert a != compute_repair_key("pg2", "html>body>button")  # 跨页不同
    assert a.startswith("v2:")  # 版本前缀，防旧库（v1 含 text 键）残留误命中


def test_repair_key_sibling_index_distinguishes():
    """v2：tag_path 含 nth-of-type 索引，同路径兄弟键不同（防 L1 碰撞）。"""
    a = compute_repair_key("pg1", "html>body>div>button:nth-of-type(1)")
    b = compute_repair_key("pg1", "html>body>div>button:nth-of-type(2)")
    assert a != b


def test_query_facade_l1_then_legacy(tmp_path):
    """A3 门面：query 按 L1 精确 → 旧式检索优先级返回候选；同一案例不重复。"""
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    store.add_repair(_case(original="#a", new="#l1-new"))
    q = RepairQuery(original_selector="#a", repair_key=compute_repair_key("pg1", "html>body>button"))
    results = store.query(q)
    assert [(c.new_selector, s) for c, s in results] == [("#l1-new", "l1")]  # 同一案例去重
    # 无 L1 命中 → 仅旧式检索
    q2 = RepairQuery(original_selector="#a", repair_key=compute_repair_key("pg9", "x"))
    results2 = store.query(q2)
    assert [(c.new_selector, s) for c, s in results2] == [("#l1-new", "legacy")]


def test_page_fingerprint_distinct():
    dom = '<button data-testid="a">确定</button>'
    assert compute_page_fingerprint("https://x/login", dom) != compute_page_fingerprint("https://x/pay", dom)
    assert compute_page_fingerprint("https://x/login", dom) == compute_page_fingerprint("https://x/login", dom)


# --- find_by_repair_key（L1）---


def test_find_by_key_sqlite(tmp_path):
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    store.add_repair(_case())
    key = compute_repair_key("pg1", "html>body>button")
    found = store.find_by_repair_key(key)
    assert found is not None and found.new_selector == "#a-new"


def test_find_by_key_memory():
    store = KnowledgeStore()
    store.add_repair(_case())
    assert store.find_by_repair_key(compute_repair_key("pg1", "html>body>button")) is not None
    assert store.find_by_repair_key(compute_repair_key("pg9", "y")) is None


# --- find_semantic（L3）：page 分桶 / 版本 / 阈值 ---


def test_semantic_hits_same_page_sqlite(tmp_path):
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    store.add_repair(_case(text="登录", new="#login"))
    store.add_repair(_case(text="取消", new="#cancel", original="#b"))
    results = store.find_semantic(_vec("登录"), "pg1", VER, k=1, threshold=0.7)
    assert results and results[0][0].new_selector == "#login"
    assert results[0][1] > 0.7


def test_semantic_cross_page_bucketed_sqlite(tmp_path):
    """跨页不误匹配：pg2 无候选 → 空。"""
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    store.add_repair(_case(page_fp="pg1", text="确定"))
    assert store.find_semantic(_vec("确定"), "pg2", VER, k=1, threshold=0.7) == []


def test_semantic_version_filter(tmp_path):
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    store.add_repair(_case())
    assert store.find_semantic(_vec("确定"), "pg1", "v9-future", k=1, threshold=0.7) == []


def test_semantic_threshold(tmp_path):
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    store.add_repair(_case(text="登录"))
    # 查询相似但不同（"登录 提交" vs "登录"）→ sim<1；阈值设得极高 → 拒绝
    assert store.find_semantic(_vec("登录 提交"), "pg1", VER, k=1, threshold=0.999) == []


def test_semantic_dim_mismatch_skipped_not_crash(tmp_path):
    """审查 C3 回归：库中混入异维向量（旧库/手改）→ 跳过该行，不崩溃、不误命中。"""
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    store.add_repair(_case(text="登录", new="#login"))  # 正常 512 维
    # 手工注入一条同版本但异维（256 维）的脏向量
    import sqlite3

    with sqlite3.connect(str(tmp_path / "kb.db")) as conn:
        conn.execute(
            "INSERT INTO repairs (original_selector, new_selector, strategy, confidence,"
            " page_url, page_fingerprint, embedding, embedding_version)"
            " VALUES ('#x', '#y', 'heuristic', 0.9, 'u', 'pg1', ?, ?)",
            (b"\x00" * 256 * 4, VER),
        )
    results = store.find_semantic(_vec("登录"), "pg1", VER, k=5, threshold=0.7)
    # 只命中正常向量，脏向量被跳过（修复前 np.stack 会 ValueError 崩溃）
    assert [c.new_selector for c, _ in results] == ["#login"]


# --- 防污染衰减 ---


def test_bump_hit_and_verified_sqlite(tmp_path):
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    key = compute_repair_key("pg1", "html>body>button")
    store.add_repair(_case())
    store.bump_hit(key)
    store.set_verified(key, True)
    found = store.find_by_repair_key(key)
    assert found is not None and found.hit_count >= 1 and found.is_verified


def test_bump_hit_memory():
    store = KnowledgeStore()
    store.add_repair(_case())
    key = compute_repair_key("pg1", "html>body>button")
    store.bump_hit(key)
    assert store.find_by_repair_key(key).hit_count == 1
