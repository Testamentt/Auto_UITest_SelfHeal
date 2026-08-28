"""失败元素上下文提取子模块（A5 拆包）——live → snapshot → static 三级回退。

原 agent/dom.py 按职责拆分而来（见 dom/__init__.py 导出）。
全程 try-catch，**绝不先炸**；提取失败返回空上下文（调用方据此跳过 L1/L3，降级 L4）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# live evaluate 提取失败时的 JS 兜底：取兄弟文本上限 / 标签路径深度
# 用占位符 + .replace 注入常量（避免 %-format / f-string 与 JS 花括号冲突）
# v2（2026-08-05）：路径深度 4→8 且带 nth-of-type 索引，支撑 L1 键去文本化后仍能区分同路径兄弟
_MAX_SIBLINGS = 5
_MAX_PATH_DEPTH = 8

_LIVE_JS = (
    (
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
    )
    .replace("_MAX_DEPTH", str(_MAX_PATH_DEPTH))
    .replace("_MAX_SIB", str(_MAX_SIBLINGS))
)


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
        target = m.group(1) if m else failed_selector.strip("#. ")
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
        "siblings": siblings[:_MAX_SIBLINGS],
    }


def extract_element_context(
    page, dom_snapshot: str | None, failed_selector: str, description: str | None = None
) -> ElementContext:
    """三级回退提取元素上下文（L1/L3 查询输入源）。

    1. **live**：page.evaluate 从活动页面取「元素文本 + 附近5兄弟 + 标签路径」；
    2. **snapshot**：DOM 快照 + BeautifulSoup 离线解析（~20ms，页面不可交互时兜底）；
    3. **static**：静态属性/描述兜底（description 或失败 selector 末段），保证查询可进行。
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
