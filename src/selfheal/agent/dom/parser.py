"""DOM 解析子模块（A5 拆包）——HTMLParser 解析 + 可交互候选元素抽取。

原 agent/dom.py 按职责拆分而来（见 dom/__init__.py 导出）。
"""

from __future__ import annotations

from html.parser import HTMLParser

# 视为可交互候选的标签（另含 role=button / 带 data-testid 的任意元素）
_INTERACTIVE_TAGS = {"button", "input", "a", "select", "textarea"}

# HTML void 元素：无结束标签（浏览器 content() 序列化即为无斜杠形式）。
# 若把它们压栈，栈底会残留该元素并持续吸收后续全部文本（文本污染，审查 C1）。
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class Element:
    """一个候选 DOM 元素的精简表示（标签 + 属性 dict + 含嵌套的文本）。"""

    __slots__ = ("tag", "attrs", "text")

    def __init__(self, tag: str, attrs: list[tuple[str, str | None]]):
        self.tag = tag
        self.attrs = dict(attrs)
        self.text = ""

    def attr(self, name: str) -> str:
        return (self.attrs.get(name) or "").strip()

    def field(self, name: str) -> str:
        return self.text.strip() if name == "text" else self.attr(name)


class _DOMParser(HTMLParser):
    """把 HTML 解析为带属性与（含嵌套的）文本的元素列表。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[Element] = []
        self._stack: list[Element] = []

    def handle_starttag(self, tag, attrs):
        el = Element(tag, attrs)
        self.elements.append(el)
        # void 元素（input/br/img 等）无结束标签，不入栈：
        # 否则残留栈底、吸收后续全部文本（审查 C1 实证缺陷）
        if tag not in _VOID_TAGS:
            self._stack.append(el)

    def handle_startendtag(self, tag, attrs):
        self.elements.append(Element(tag, attrs))

    def handle_endtag(self, tag):
        # 仅当 endtag 与栈顶匹配才弹出并向上汇聚子文本（兼容 <button><span>x</span></button>）；
        # 不匹配（未闭合嵌套 / 孤立 endtag）时忽略，防"弹错层"导致文本错配汇聚（配合 void 不入栈）。
        if self._stack and self._stack[-1].tag == tag:
            el = self._stack.pop()
            if self._stack:  # 把子元素文本向上汇聚，兼容 <button><span>x</span></button>
                self._stack[-1].text += el.text

    def handle_data(self, data):
        if self._stack:
            self._stack[-1].text += data


def parse_interactive_elements(dom: str | None) -> list[Element]:
    """解析 DOM，返回可交互候选元素列表。"""
    if not dom:
        return []
    parser = _DOMParser()
    parser.feed(dom)
    return [
        el
        for el in parser.elements
        if el.tag in _INTERACTIVE_TAGS or el.attr("role") == "button" or el.attr("data-testid")
    ]
