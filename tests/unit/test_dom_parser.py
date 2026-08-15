"""单元测试：DOM 解析器（审查 C1 回归 + 基础行为，不依赖浏览器）。

覆盖：
- void 元素（无结束标签）不入栈，不污染后续元素文本（审查 C1 实证缺陷）；
- 嵌套文本向上汇聚（<button><span>x</span></button>）；
- endtag 与栈顶不匹配时忽略（畸形 HTML 防弹错层）；
- 显式自闭合（<input .../>）与无斜杠形式行为一致；
- 候选过滤（可交互标签 / role=button / data-testid）。
"""

import pytest

from selfheal.agent.dom.parser import _VOID_TAGS, Element, parse_interactive_elements

pytestmark = pytest.mark.unit


def test_void_element_does_not_pollute_following_text():
    """审查 C1：浏览器序列化风格（void 无斜杠）下，input 不应吸收后续元素文本。"""
    dom = """<html><body>
<input data-testid="username" name="username">
<button data-testid="submit-btn" aria-label="登录按钮">登录</button>
<div><p>说明文字</p></div>
</body></html>"""
    els = parse_interactive_elements(dom)
    by_tag = {el.attr("data-testid") or el.attr("id"): el for el in els}
    # input 文本必须为空（修复前会被污染为 '\n登录\n说明文字\n'）
    assert by_tag["username"].text == ""
    assert by_tag["submit-btn"].text == "登录"


def test_void_element_self_closing_same_behavior():
    """显式自闭合（<input .../>）与无斜杠形式行为一致。"""
    dom = '<html><body><input data-testid="username" /><button data-testid="submit-btn">登录</button></body></html>'
    els = parse_interactive_elements(dom)
    by_tag = {el.attr("data-testid"): el for el in els}
    assert by_tag["username"].text == ""
    assert by_tag["submit-btn"].text == "登录"


def test_nested_text_aggregates_to_parent():
    """<button><span>登录</span></button> → 按钮文本含嵌套子元素文本。"""
    dom = '<html><body><button id="b"><span>登</span><span>录</span></button></body></html>'
    els = parse_interactive_elements(dom)
    assert els[0].text == "登录"


def test_mismatched_endtag_ignored():
    """畸形 HTML（span 未闭合就结束 button）：endtag 不匹配时忽略，不弹错层。"""
    dom = "<button><span>x</button></span>"
    els = parse_interactive_elements(dom)
    assert len(els) == 1
    assert els[0].tag == "button"
    # 收尾的 </span> 匹配栈顶 span → 文本正常汇聚给 button
    assert els[0].text == "x"


def test_void_tags_defined():
    """void 标签集合覆盖常见无结束标签元素。"""
    assert {"input", "br", "img", "hr", "meta", "link"} <= _VOID_TAGS


def test_interactive_filter():
    """候选过滤：可交互标签 / role=button / data-testid，普通 div 排除。"""
    dom = (
        '<html><body><div>纯文本</div>'
        '<button id="a">A</button><div role="button" id="b">B</div>'
        '<span data-testid="c">C</span></body></html>'
    )
    els = parse_interactive_elements(dom)
    ids = {el.attr("data-testid") or el.attr("id") for el in els}
    assert ids == {"a", "b", "c"}


def test_empty_dom_returns_empty():
    assert parse_interactive_elements(None) == []
    assert parse_interactive_elements("") == []


def test_element_attr_field():
    el = Element("button", [("data-testid", "x"), ("aria-label", None)])
    assert el.attr("data-testid") == "x"
    assert el.attr("aria-label") == ""
    el.text = "  登录  "
    assert el.field("text") == "登录"
    assert el.field("data-testid") == "x"
