"""启发式匹配策略（Phase 1 主力，零模型依赖）。

不调用 LLM：解析失败现场的 DOM，抽取可交互候选元素的多维属性
（data-testid / id / aria-label / 文本 / name / placeholder），
与「原选择器 + 自然语言描述」的意图做相似度打分，取最优候选生成稳定新定位器。

打分规则（见 _score）：
- 描述与候选文本/aria-label 互为子串 → 强信号（置信度 ~0.85 起）。
- 否则按意图词元在最佳字段的重叠比例计分（封顶 ~0.8）。

新定位器优先取稳定属性：data-testid > id > 唯一文本 > aria-label。
TODO：属性权重可配置；候选过多时结合可见性/位置二次筛选。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from selfheal.agent.strategies.base import RepairCandidate, RepairStrategy
from selfheal.collect.collector import Scene

# 视为可交互候选的标签（另含 role=button / 带 data-testid 的任意元素）
_INTERACTIVE_TAGS = {"button", "input", "a", "select", "textarea"}
# 词元：连续字母数字 或 连续中文（CJK）
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]+")
# 参与打分的字段及（仅用于可读性，实际计分见 _score）
_FIELDS = ("data-testid", "id", "aria-label", "text", "name", "placeholder")

_CONTAIN_BASE = 0.85  # 描述与文本互为子串的基础置信度
_TOKEN_CAP = 0.80  # 纯词元匹配的置信度上限


def _tokenize(text: str | None) -> set[str]:
    """切成小写词元集合（英文按词、中文按连续片段）。"""
    if not text:
        return set()
    return {t.lower() for t in _TOKEN_RE.findall(text)}


class _Element:
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
        self.elements: list[_Element] = []
        self._stack: list[_Element] = []

    def handle_starttag(self, tag, attrs):
        el = _Element(tag, attrs)
        self.elements.append(el)
        self._stack.append(el)

    def handle_startendtag(self, tag, attrs):
        self.elements.append(_Element(tag, attrs))

    def handle_endtag(self, tag):
        if self._stack:
            el = self._stack.pop()
            if self._stack:  # 把子元素文本向上汇聚，兼容 <button><span>x</span></button>
                self._stack[-1].text += el.text

    def handle_data(self, data):
        if self._stack:
            self._stack[-1].text += data


def _candidates(dom: str) -> list[_Element]:
    parser = _DOMParser()
    parser.feed(dom)
    return [
        el
        for el in parser.elements
        if el.tag in _INTERACTIVE_TAGS or el.attr("role") == "button" or el.attr("data-testid")
    ]


def _score(el: _Element, intent_tokens: set[str], description: str | None) -> float:
    """计算候选元素与意图的相似度，归一化到 [0, 1]。"""
    # 1) 强信号：描述与候选文本/aria-label 互为子串
    contained = False
    if description:
        for value in (el.field("text"), el.attr("aria-label")):
            if value and (description in value or value in description):
                contained = True
                break
    # 2) 词元重叠：取所有字段中的最佳重叠比例
    ratio = 0.0
    if intent_tokens:
        for name in _FIELDS:
            tokens = _tokenize(el.field(name))
            if tokens:
                ratio = max(ratio, len(intent_tokens & tokens) / len(intent_tokens))
    if contained:
        return min(_CONTAIN_BASE + (0.10 if ratio > 0 else 0.0), 1.0)
    return min(ratio * _TOKEN_CAP, 1.0)


def _build_selector(el: _Element) -> str | None:
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


class HeuristicStrategy(RepairStrategy):
    name = "heuristic"

    def repair(self, scene: Scene, original_selector: str, description: str | None = None):
        if not scene.dom_snapshot:
            return None
        intent_tokens = _tokenize(original_selector) | _tokenize(description)
        best_el, best_score = None, 0.0
        for el in _candidates(scene.dom_snapshot):
            score = _score(el, intent_tokens, description)
            if score > best_score:
                best_el, best_score = el, score
        if best_el is None or best_score <= 0:
            return None
        selector = _build_selector(best_el)
        if not selector:
            return None
        return RepairCandidate(selector=selector, confidence=round(best_score, 3), strategy=self.name)
