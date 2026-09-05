"""DOM 精简：把完整 DOM 快照压缩为"逐行的可交互元素索引"，控制给 LLM 的 token 量
（m1 自 llm/io.py 归位，V5 复核）。

归属理由：本模块依赖 agent/dom 的解析（parse_interactive_elements）与稳定定位器
生成（build_stable_selector），属于 agent 侧的"模型输入准备"——放在 llm/ 会让
provider 无关的 LLM 抽象层反向依赖 agent 层（llm→agent 分层违规，违背 CLAUDE.md
四层架构中 llm 位于 agent 之下的约定）。llm/io.py 只保留纯 I/O 编解码工具
（extract_json / safe_str / safe_float）。
"""

from __future__ import annotations

from selfheal.agent.dom.parser import parse_interactive_elements
from selfheal.agent.dom.selector_builder import build_stable_selector


def build_compact_dom(dom: str | None, max_elems: int = 60, max_text: int = 24) -> list[str]:
    """把 DOM 快照压缩为可交互元素索引（每行一条）。

    复用 dom 的解析与稳定定位器生成；优先排稳定属性齐全的元素，
    封顶 max_elems 条、文本截断 max_text 字符，控制给 LLM 的 token 量。
    """
    if not dom:
        return []
    rows: list[str] = []
    seen: set[str] = set()
    for el in parse_interactive_elements(dom):
        selector = build_stable_selector(el)
        if not selector:
            continue
        text = _truncate(el.text.strip(), max_text)
        parts = [el.tag, f"selector={selector!r}"]
        if text:
            parts.append(f"text={text!r}")
        for attr_name in ("id", "data-testid", "aria-label", "name", "placeholder"):
            value = el.attr(attr_name)
            if value:
                parts.append(f"{attr_name}={value!r}")
        line = " ".join(parts)
        if line not in seen:  # 去重（属性可能相同）
            seen.add(line)
            rows.append(line)
    # 稳定属性齐全（data-testid/id）优先排在前面，帮助模型聚焦
    rows.sort(key=lambda r: ("data-testid" in r) + (" id=" in r), reverse=True)
    return rows[:max_elems]


def _truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"
