"""单元测试：知识库 SQLite 持久化 + DOM 指纹 + 工厂（不依赖浏览器）。"""

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
