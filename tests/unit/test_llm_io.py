"""单元测试：llm_io 基础设施（compact_dom / extract_json / safe_*）。"""

import pytest

from selfheal.agent.dom import build_compact_dom  # m1：自 llm.io 归位（V5 复核）
from selfheal.llm.io import extract_json, safe_float, safe_str

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


# --- L7：extract_json 逐个候选 + safe_float 非有限值过滤 ---


def test_extract_json_picks_first_object_among_multiple():
    """L7：多对象回复取**首个**可用对象，且不再被贪婪片段吞并。"""
    text = '{"reason": "先给一个"}\n{"root_cause": "covered", "confidence": 0.9}'
    assert extract_json(text) == {"reason": "先给一个"}


def test_extract_json_skips_broken_object_for_later_valid_one():
    """L7 核心：贪婪正则会把两个对象整体截成一段废串 → 返回 None 丢掉可用候选。"""
    text = '{"broken": }\n{"root_cause": "covered"}'
    assert extract_json(text) == {"root_cause": "covered"}


def test_extract_json_handles_nested_object():
    """L7：嵌套对象不被贪婪片段破坏。"""
    text = '分析如下：{"a": {"b": 1}, "c": [1, 2]} 请参考。'
    assert extract_json(text) == {"a": {"b": 1}, "c": [1, 2]}


def test_extract_json_fence_with_trailing_text():
    """L7：围栏 JSON 后还有多余文字时仍能取到对象（围栏候选优先于整串扫描）。"""
    text = '```json\n{"root_cause": "covered"}\n```\n补充说明：无。'
    assert extract_json(text) == {"root_cause": "covered"}


def test_extract_json_skips_non_object_and_garbage():
    """L7：数组 / 数字 / 完全非 JSON 内容仍返回 None（只接受 JSON 对象）。"""
    assert extract_json("[1,2,3]") is None
    assert extract_json("完全不像是 JSON 的内容") is None
    assert extract_json("{a: 1}") is None  # 键无引号：非合法 JSON，不猜
    assert extract_json("   ") is None


def test_extract_json_still_accepts_plain_object():
    """L7：严格整串 JSON 的快路径不受影响（无回归）。"""
    assert extract_json('  {"a": 1}  ') == {"a": 1}
    assert extract_json('{"nested": {"x": "}"}}') == {"nested": {"x": "}"}}


def test_safe_float_rejects_non_finite_values():
    """L7：nan / inf 字符串与浮点值一律按无效处理（nan 比较恒 False、inf 恒 True，均静默失真）。"""
    for value in ("nan", "NaN", " inf ", "-inf", "Infinity", "-Infinity", "1e400"):
        assert safe_float({"c": value}, "c", default=0.0) == 0.0
    for value in (float("nan"), float("inf"), float("-inf")):
        assert safe_float({"c": value}, "c", default=0.0) == 0.0
    assert safe_float({"c": "nan"}, "c", default=0.5) == 0.5


def test_safe_float_keeps_finite_boundary_values():
    """L7：过滤只针对非有限值，正常数值（含 0 与负值、极大有限值）照常返回。"""
    assert safe_float({"c": 0}, "c") == 0.0
    assert safe_float({"c": -0.25}, "c") == -0.25
    assert safe_float({"c": " 0.75 "}, "c") == 0.75
    assert safe_float({"c": 1e308}, "c") == 1e308
