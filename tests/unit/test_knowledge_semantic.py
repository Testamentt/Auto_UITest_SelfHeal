"""单元测试：知识语义化 A2——repair_key、find_semantic、防污染（不触网）。"""

import pytest

from selfheal.agent.dom import compute_page_fingerprint, compute_repair_key
from selfheal.knowledge.schema import RepairCase
from selfheal.knowledge.sqlite_store import SqliteKnowledgeStore
from selfheal.knowledge.store import KnowledgeStore

pytestmark = pytest.mark.unit

VER = "v1-ngram"


def _vec(text: str) -> bytes:
    from selfheal.llm.embedding import NgramEmbedding

    return NgramEmbedding().embed(text)


def _case(*, original="#a", new="#a-new", page_fp="pg1", text="确定", path="html>body>button", embedding=None):
    return RepairCase(
        original_selector=original,
        new_selector=new,
        strategy="heuristic",
        confidence=0.9,
        page_url="https://x/pg1",
        page_fingerprint=page_fp,
        repair_key=compute_repair_key(page_fp, text, path),
        embedding=embedding or _vec(text),
        embedding_version=VER,
    )


# --- repair_key 确定性 ---


def test_repair_key_deterministic():
    a = compute_repair_key("pg1", "确定", "html>body>button")
    b = compute_repair_key("pg1", "确定", "html>body>button")
    assert a == b
    assert a != compute_repair_key("pg2", "确定", "html>body>button")  # 跨页不同
    assert a != compute_repair_key("pg1", "取消", "html>body>button")  # 文本不同


def test_page_fingerprint_distinct():
    dom = '<button data-testid="a">确定</button>'
    assert compute_page_fingerprint("https://x/login", dom) != compute_page_fingerprint("https://x/pay", dom)
    assert compute_page_fingerprint("https://x/login", dom) == compute_page_fingerprint("https://x/login", dom)


# --- find_by_repair_key（L1）---


def test_find_by_key_sqlite(tmp_path):
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    store.add_repair(_case())
    key = compute_repair_key("pg1", "确定", "html>body>button")
    found = store.find_by_repair_key(key)
    assert found is not None and found.new_selector == "#a-new"


def test_find_by_key_memory():
    store = KnowledgeStore()
    store.add_repair(_case())
    assert store.find_by_repair_key(compute_repair_key("pg1", "确定", "html>body>button")) is not None
    assert store.find_by_repair_key(compute_repair_key("pg9", "x", "y")) is None


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


# --- 防污染衰减 ---


def test_bump_hit_and_verified_sqlite(tmp_path):
    store = SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    key = compute_repair_key("pg1", "确定", "html>body>button")
    store.add_repair(_case())
    store.bump_hit(key)
    store.set_verified(key, True)
    found = store.find_by_repair_key(key)
    assert found is not None and found.hit_count >= 1 and found.is_verified


def test_bump_hit_memory():
    store = KnowledgeStore()
    store.add_repair(_case())
    key = compute_repair_key("pg1", "确定", "html>body>button")
    store.bump_hit(key)
    assert store.find_by_repair_key(key).hit_count == 1
