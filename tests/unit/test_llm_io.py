"""单元测试：llm_io 基础设施（compact_dom / extract_json / safe_*）。"""

import pytest

from selfheal.llm.io import build_compact_dom, extract_json, safe_float, safe_str

pytestmark = pytest.mark.unit

DOM = """
<html><body>
  <input data-testid="username" name="username" aria-label="用户名" />
  <button id="submit-btn-v2" data-testid="submit-btn" aria-label="登录按钮">登录</button>
  <button id="ghost-btn">Go</button>
</body></html>
"""


def test_compact_dom_extracts_stable_elements():
    rows = build_compact_dom(DOM)
    assert any('data-testid="submit-btn"' in r for r in rows)
    assert any('data-testid="username"' in r for r in rows)
    # 稳定属性齐全的元素优先
    assert rows[0].startswith("button") or "data-testid" in rows[0]


def test_compact_dom_empty():
    assert build_compact_dom(None) == []
    assert build_compact_dom("") == []


def test_compact_dom_truncates_text():
    dom = f"<button data-testid='t'>{'x' * 100}</button>"
    rows = build_compact_dom(dom, max_text=24)
    assert "x" * 30 not in rows[0]


def test_extract_json_from_fence():
    text = '```json\n{"a": 1}\n```'
    assert extract_json(text) == {"a": 1}


def test_extract_json_with_prefix_and_suffix():
    text = '这里是一些说明{"root_cause": "covered"}，请参考。'
    assert extract_json(text) == {"root_cause": "covered"}


def test_extract_json_garbage_returns_none():
    assert extract_json("完全不像是 JSON 的内容") is None
    assert extract_json(None) is None
    assert extract_json("[1,2,3]") is None


def test_safe_fields():
    assert safe_str({"a": " x "}, "a") == "x"
    assert safe_str({"a": 1}, "a") == ""
    assert safe_float({"c": "0.5"}, "c") == 0.5
    assert safe_float({"c": 95}, "c") == 95.0
    assert safe_float({"c": "abc"}, "c", default=0.0) == 0.0


def test_safe_float_rejects_bool():
    """#11：bool 是 int 子类，模型返回 true 不应被当作 1.0 穿透护栏。"""
    assert safe_float({"c": True}, "c", default=0.0) == 0.0
    assert safe_float({"c": False}, "c", default=0.0) == 0.0
