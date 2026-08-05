"""DOM 解析子模块（A5 拆包）——HTMLParser 解析 + 可交互候选元素抽取。

原 agent/dom.py 按职责拆分而来（见 dom/__init__.py 导出）。
"""

from __future__ import annotations

from html.parser import HTMLParser

# 视为可交互候选的标签（另含 role=button / 带 data-testid 的任意元素）
_INTERACTIVE_TAGS = {"button", "input", "a", "select", "textarea"}


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
        self._stack.append(el)

    def handle_startendtag(self, tag, attrs):
        self.elements.append(Element(tag, attrs))

    def handle_endtag(self, tag):
        if self._stack:
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
