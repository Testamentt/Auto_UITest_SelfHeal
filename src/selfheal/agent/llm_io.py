"""LLM 公共输入/输出基础设施（diagnose 与 semantic 共用）。

- build_compact_dom：把完整 DOM 快照精简为"逐行的可交互元素索引"，控制 token 量。
- extract_json：从模型回复中容错抽取 JSON 对象（剥 markdown fence → json.loads → 正则兜底）。
- safe_float / safe_str：容错提取字段，避免模型格式不稳定导致崩溃。

DOM 解析与稳定定位器生成复用 agent/dom.py 公共工具（不依赖 strategies，避免循环导入）。
"""

from __future__ import annotations

import json
import re

from selfheal.agent.dom import build_stable_selector, parse_interactive_elements

# 抽取最外层 {...} 的正则（用于 json.loads 失败后的兜底）
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.S)


def build_compact_dom(dom: str | None, max_elems: int = 60, max_text: int = 24) -> list[str]:
    """把 DOM 快照压缩为可交互元素索引（每行一条）。

    复用 dom.py 的解析与稳定定位器生成；优先排稳定属性齐全的元素，
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


def extract_json(text: str | None) -> dict | None:
    """从模型回复中抽取 JSON 对象；解析失败返回 None。"""
    if not text:
        return None
    candidates: list[str] = []
    stripped = text.strip()
    candidates.append(stripped)
    # 剥掉 markdown ```json ... ``` 围栏
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, re.S)
    if fence:
        candidates.append(fence.group(1))
    match = _JSON_OBJECT_RE.search(stripped)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return None


def safe_str(data: dict, key: str) -> str:
    """从字典安全取字符串字段；缺失/非字符串返回空串。"""
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def safe_float(data: dict, key: str, default: float = 0.0) -> float:
    """从字典安全取数值字段（兼容 "0.5" 字符串）；无效返回 default。"""
    value = data.get(key)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default
