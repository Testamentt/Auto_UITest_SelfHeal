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
from dataclasses import dataclass, field
from html.parser import HTMLParser

# 视为可交互候选的标签（另含 role=button / 带 data-testid 的任意元素）
_INTERACTIVE_TAGS = {"button", "input", "a", "select", "textarea"}

# live evaluate 提取失败时的 JS 兜底：取兄弟文本上限 / 标签路径深度
# 用占位符 + .replace 注入常量（避免 %-format / f-string 与 JS 花括号冲突）
# v2（2026-08-05）：路径深度 4→8 且带 nth-of-type 索引，支撑 L1 键去文本化后仍能区分同路径兄弟
_MAX_SIBLINGS = 5
_MAX_PATH_DEPTH = 8

_LIVE_JS = (
    """(sel) => {
  try {
    const el = document.querySelector(sel);
    if (!el) return null;
    const path = [];
    let node = el;
    while (node && node.tagName && path.length < _MAX_DEPTH) {
      const tag = node.tagName.toLowerCase();
      // nth-of-type 索引（同类兄弟序号，从 1 起）：区分同路径兄弟，供 L1 键防碰撞
      let index = 1;
      let sib = node.previousElementSibling;
      while (sib) {
        if (sib.tagName && sib.tagName.toLowerCase() === tag) index += 1;
        sib = sib.previousElementSibling;
      }
      path.unshift(`${tag}:nth-of-type(${index})`);
      node = node.parentElement;
    }
    const siblings = [];
    const parent = el.parentElement;
    if (parent) {
      let node = parent.firstElementChild;
      while (node && siblings.length < _MAX_SIB) {
        if (node !== el) {
          const t = (node.innerText || node.textContent || "").trim();
          if (t) siblings.push(t);
        }
        node = node.nextElementSibling;
      }
    }
    return {
      text: (el.innerText || el.textContent || "").trim(),
      path: path.join(">"),
      siblings: siblings.slice(0, _MAX_SIB),
    };
  } catch (e) {
    return null;
  }
}"""
).replace("_MAX_DEPTH", str(_MAX_PATH_DEPTH)).replace("_MAX_SIB", str(_MAX_SIBLINGS))


@dataclass
class ElementContext:
    """一次失败元素的三级提取上下文（Phase 5 A：L1/L3 查询输入源）。

    - text: 元素文本（含嵌套子元素文本，与 parse_interactive_elements 语义一致）。
    - tag_path: 标签路径摘要（html>body>...>button，用于页面内定位与 repair_key）。
    - siblings: 附近兄弟元素文本（最多 5 个，供 L3 语义向量丰富上下文）。
    - source: live / snapshot / static —— 三级回退的落点，便于审计与调试。
    """

    text: str = ""
    tag_path: str = ""
    siblings: list[str] = field(default_factory=list)
    source: str = ""

    @property
    def is_empty(self) -> bool:
        """查询上下文是否为空（为空则跳过 L1 精确键与 L3 语义检索，降级 L4）。"""
        return not self.query_text.strip()

    @property
    def query_text(self) -> str:
        """L3 查询拼接文本：元素文本 + 附近兄弟 + 标签路径。"""
        return " ".join(filter(None, [self.text, *self.siblings, self.tag_path]))


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


def _url_path(url: str) -> str:
    """取 URL 的路径部分（去 query/fragment），作为页面隔离的一部分。"""
    from urllib.parse import urlparse

    return urlparse(url).path or url or ""


def compute_page_fingerprint(url: str, dom: str | None) -> str:
    """页面指纹 = md5(URL 路径 + DOM 结构指纹)——跨页面隔离（防"张冠李戴"误修复）。

    确定性：md5（非内置 hash()，避免跨进程随机化）。
    """
    dom_fp = dom_fingerprint(dom) or ""
    raw = f"{_url_path(url)}\n{dom_fp}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def compute_repair_key(page_fingerprint: str, tag_path: str) -> str:
    """L1 精确命中键 = md5(page_fingerprint|tag_path)，带 v2 版本前缀。

    v2 变更（2026-08-05 决策）：
    - tag_path 含 nth-of-type 索引（如 html>body>div:nth-of-type(2)>button:nth-of-type(1)），
      区分同路径兄弟，防 L1 键碰撞；
    - 不再包含元素文本——文案变化（"提交订单"→"提交"）不破键；结构变动 → L1 miss 交 L2 补位。
    版本前缀（v2:）防止旧库（v1 含 text 的键）残留误命中。
    """
    raw = f"{page_fingerprint}|{tag_path}"
    return "v2:" + hashlib.md5(raw.encode("utf-8")).hexdigest()


def _live_element_context(page, failed_selector: str) -> dict | None:
    """活体提取：page.evaluate 一次拿到 {text, path, siblings}；失败返回 None（不炸）。"""
    if page is None:
        return None
    try:
        return page.evaluate(_LIVE_JS, failed_selector)
    except Exception:  # noqa: BLE001 - 活体提取失败降级到快照/静态
        return None


def _snapshot_element_context(dom_snapshot: str, failed_selector: str) -> dict | None:
    """DOM 快照离线提取（BeautifulSoup，~20ms 稳定兜底，不依赖 live 页面）。

    优先按 CSS 选择器匹配；`text="..."` 风格（Playwright 语法、非 CSS）按文本包含匹配。
    """
    if not dom_snapshot:
        return None
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover - beautifulsoup4 为核心依赖
        return None
    try:
        soup = BeautifulSoup(dom_snapshot, "html.parser")
    except Exception:  # noqa: BLE001 - 快照解析失败降级静态
        return None

    el = None
    try:
        if soup.select_one(failed_selector):
            el = soup.select_one(failed_selector)
    except Exception:  # noqa: BLE001 - 非法 CSS（如 text=）走文本匹配
        pass
    if el is None:
        # text="xxx" 风格：取引号内文本，找含该文本的可交互元素
        import re

        m = re.search(r'text\s*=\s*["\']([^"\']+)["\']', failed_selector)
        target = m.group(1) if m else failed_selector.strip('#. ')
        el = soup.find(string=lambda s: s and target and target in s)
        el = el.parent if el is not None else None
    if el is None:
        return None

    # 标签路径摘要：手动向上走，最多 _MAX_PATH_DEPTH 层（跳过 bs4 的 [document] 根）
    # 带 nth-of-type 索引，与 live JS 路径格式一致（L1 键防碰撞、两端可互换）
    parts: list[str] = []
    node = el
    while node is not None and len(parts) < _MAX_PATH_DEPTH:
        name = getattr(node, "name", None)
        if name in (None, "[document]"):
            break
        index = 1
        for sib in node.previous_siblings:  # 同类兄弟序号（从 1 起），过滤文本节点
            if getattr(sib, "name", None) == name:
                index += 1
        parts.insert(0, f"{name}:nth-of-type({index})")
        node = node.parent
    siblings: list[str] = []
    if el.parent is not None:
        for sib in el.parent.find_all(recursive=False):
            if sib is not el:
                t = (sib.get_text(" ", strip=True) or "").strip()
                if t:
                    siblings.append(t)
                if len(siblings) >= _MAX_SIBLINGS:
                    break
    return {
        "text": (el.get_text(" ", strip=True) or "").strip(),
        "path": ">".join(parts),
        "siblings": siblings[: _MAX_SIBLINGS],
    }


def extract_element_context(
    page, dom_snapshot: str | None, failed_selector: str, description: str | None = None
) -> ElementContext:
    """三级回退提取元素上下文（L1/L3 查询输入源）。

    1. **live**：page.evaluate 从活动页面取「元素文本 + 附近5兄弟 + 标签路径」；
    2. **snapshot**：DOM 快照 + BeautifulSoup 离线解析（~20ms，页面不可交互时兜底）；
    3. **static**：静态属性/描述兜底（description 或失败 selector 末段），保证查询可进行。

    全程 try-catch，**绝不先炸**；提取失败返回空上下文（调用方据此跳过 L1/L3，降级 L4）。
    """
    raw = _live_element_context(page, failed_selector)
    source = "live"
    if raw is None:
        raw = _snapshot_element_context(dom_snapshot or "", failed_selector)
        source = "snapshot"
    if raw is not None:
        return ElementContext(
            text=(raw.get("text") or "").strip(),
            tag_path=(raw.get("path") or "").strip(),
            siblings=[s for s in (raw.get("siblings") or []) if s],
            source=source,
        )
    text = (description or "").strip() or failed_selector.strip()
    return ElementContext(text=text, source="static")
