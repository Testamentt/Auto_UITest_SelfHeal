"""DOM 解析子模块（A5 拆包）——HTMLParser 解析 + 可交互候选元素抽取 + T8 原生交叉校验。

原 agent/dom.py 按职责拆分而来（见 dom/__init__.py 导出）。
T8：除静态 HTMLParser 解析外，提供 Playwright 原生查询路径（parse_interactive_elements_native）
与两来源交叉校验（cross_validate_interactive）——静态解析是单测主力（无浏览器）、
原生解析在真实页面更鲁棒；策略链经 interactive_candidates 优先用原生结果、静态解析兜底。
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


# --- T8：Playwright 原生解析与交叉校验 ---

# 原生查询选择器：与静态 _INTERACTIVE_TAGS + role=button + data-testid 口径一致
_NATIVE_SELECTOR = "button, input, a, select, textarea, [role='button'], [data-testid]"

# 浏览器端一次性取回候选元素（tag / 关注的属性 / 展平文本），避免逐个 handle 交互
_NATIVE_ATTRS_JS = """els => els.map(el => {
    const get = (n) => el.getAttribute(n);
    const attrs = {};
    for (const n of ['data-testid', 'id', 'aria-label', 'name', 'placeholder', 'role']) {
        const v = get(n);
        if (v !== null && v !== '') attrs[n] = v;
    }
    return { tag: el.tagName.toLowerCase(), attrs, text: el.innerText || '' };
})"""


def parse_interactive_elements_native(page=None) -> list[Element]:
    """用 Playwright 原生查询抽取可交互候选（T8），替代/交叉校验 HTMLParser 自解析。

    页面不可用（page=None）返回 []；查询/属性读取异常**向上抛**，由采集器 best-effort
    捕获——原生解析是增强，失败必须降级回静态解析，不改变既有行为。
    产物与静态解析同构（Element：tag + attrs + 展平 text），可直接用于
    build_stable_selector / 打分 / 防幻觉护栏。
    """
    if page is None:
        return []
    rows = page.locator(_NATIVE_SELECTOR).evaluate_all(_NATIVE_ATTRS_JS)
    elements: list[Element] = []
    for row in rows:
        el = Element(row["tag"], list((row.get("attrs") or {}).items()))
        el.text = (row.get("text") or "").strip()
        elements.append(el)
    return elements


@dataclass(frozen=True)
class CrossCheck:
    """两来源（静态 HTMLParser vs Playwright 原生）交互候选对齐结果（T8）。

    对齐键 = 稳定定位器（build_stable_selector 非 None 者才参与——它们是修复候选）。
    仅参与对齐的元素才有意义：无稳定定位器的裸元素两来源都不产生候选，不参与计数。
    """

    total: int  # 两来源稳定候选并集数（0 = 无候选，视图不一致判定）
    matched: int  # 两来源都解析出的稳定定位器数
    only_static: list[str] = field(default_factory=list)  # 静态独有（原生缺失）
    only_native: list[str] = field(default_factory=list)  # 原生独有（静态缺失）
    consistent: bool = False  # total>0 且无单侧缺失 → 两来源一致

    def as_dict(self) -> dict:
        """可序列化摘要（供报告/审计展示）。"""
        return {
            "total": self.total,
            "matched": self.matched,
            "only_static": list(self.only_static),
            "only_native": list(self.only_native),
            "consistent": self.consistent,
        }


def cross_validate_interactive(static: list[Element], native: list[Element]) -> CrossCheck:
    """按稳定定位器对齐两来源的交互候选，判定是否一致（T8 交叉校验）。

    差异出现时由调用方（采集器）记 warning 并可在报告里溯源明细；
    一致的静态/原生口径说明自解析 HTMLParser 未偏离真实 DOM 结构。
    """
    # 惰性导入：selector_builder 顶层 import parser（Element），此处再顶层互导会成环
    from selfheal.agent.dom.selector_builder import build_stable_selector

    static_keys = {s for el in static if (s := build_stable_selector(el))}
    native_keys = {s for el in native if (s := build_stable_selector(el))}
    matched = static_keys & native_keys
    only_static = sorted(static_keys - native_keys)
    only_native = sorted(native_keys - static_keys)
    total = len(static_keys | native_keys)
    return CrossCheck(
        total=total,
        matched=len(matched),
        only_static=only_static,
        only_native=only_native,
        consistent=total > 0 and not only_static and not only_native,
    )


def interactive_candidates(scene) -> list[Element]:
    """策略链候选入口（T8）：有原生解析结果（page 采集时）优先，否则回退静态 DOM 快照解析。

    评审 m7（2026-09-03）：原生解析**成功即优先**（含空结果——原生对真实 DOM 更可信，
    空列表说明页面确无可交互候选，不回退静态）；仅原生不可用（字段为 None，如 page
    不可用的纯逻辑场景）才回退静态解析。
    刻意不 import Scene 类型（duck typing），避免 collect ↔ dom 包循环导入。
    返回列表直接供 heuristic / visual / semantic 复用（含 build_stable_selector 护栏逻辑）。
    """
    native = getattr(scene, "native_elements", None)
    if native is not None:
        return native
    return parse_interactive_elements(getattr(scene, "dom_snapshot", None))
