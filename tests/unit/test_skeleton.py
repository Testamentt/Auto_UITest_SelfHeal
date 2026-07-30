"""单元测试：不依赖浏览器与网络。"""

from selfheal.config import Settings, load_settings
from selfheal.knowledge.schema import RepairCase
from selfheal.knowledge.store import KnowledgeStore


def test_default_settings():
    settings = load_settings("/nonexistent/path.yaml")
    assert isinstance(settings, Settings)
    assert settings.healing.strategy_order[0] == "heuristic"
    assert settings.healing.knowledge_first is True


def test_knowledge_store_roundtrip():
    store = KnowledgeStore()
    case = RepairCase(
        original_selector="#old-btn",
        new_selector="button.submit",
        strategy="heuristic",
        confidence=0.9,
        page_url="https://example.com",
    )
    store.add_repair(case)
    found = store.find_repair("#old-btn")
    assert found is not None
    assert found.new_selector == "button.submit"
