"""DOM 解析与稳定定位器生成的公共工具。

heuristic（启发式打分）、llm_io（构造精简 DOM 给 LLM）、semantic（防幻觉护栏）
三处共用的底层能力集中于此，避免跨模块引用彼此私有符号、消除循环导入。

- Element：候选 DOM 元素的精简表示（标签 + 属性 + 含嵌套的文本）。
- parse_interactive_elements：解析 HTML，抽取可交互候选元素。
- build_stable_selector：由候选元素生成稳定的 Playwright 定位器
  （data-testid > id > 文本 > aria-label）。
"""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser

# 视为可交互候选的标签（另含 role=button / 带 data-testid 的任意元素）
_INTERACTIVE_TAGS = {"button", "input", "a", "select", "textarea"}


class Element:
    """一个候选 DOM 元素的精简表示。"""

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


def build_stable_selector(el: Element) -> str | None:
    """由候选元素生成稳定的 Playwright 定位器。"""
    if testid := el.attr("data-testid"):
        return f'[data-testid="{testid}"]'
    if el_id := el.attr("id"):
        return f"#{el_id}"
    if text := el.field("text"):
        return f'text="{text}"'
    if aria := el.attr("aria-label"):
        return f'[aria-label="{aria}"]'
    return None


def dom_fingerprint(dom: str | None) -> str | None:
    """计算 DOM 指纹：可交互元素稳定定位器排序后的哈希。

    用于知识库相似度匹配——同一页面结构（可交互元素集合一致）指纹相同，
    使修复案例可在"同结构页面"上可靠复用。无可交互元素时返回 None。
    """
    if not dom:
        return None
    selectors = sorted(
        s for el in parse_interactive_elements(dom) if (s := build_stable_selector(el))
    )
    if not selectors:
        return None
    return hashlib.sha1("\n".join(selectors).encode("utf-8")).hexdigest()
