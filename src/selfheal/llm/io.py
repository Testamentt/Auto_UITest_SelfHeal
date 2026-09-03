"""LLM / VLM 公共输入/输出编解码工具（T10 建立本模块；m1 收敛，V5 复核）。

归属边界（m1）：本模块只保留与 DOM 无关的**纯 I/O 编解码**——
- extract_json：从模型回复中容错抽取 JSON 对象（剥 markdown fence → json.loads → 正则兜底）。
- safe_float / safe_str：容错提取字段，避免模型格式不稳定导致崩溃。

依赖 DOM 解析的"模型输入准备"（build_compact_dom，构造精简 DOM）已上移至
`selfheal.agent.dom.compact`——此前本模块 import selfheal.agent.dom，让 provider
无关的 LLM 抽象层反向依赖 agent 层（llm→agent 分层违规）。调用方（diagnose_llm /
semantic）改从 agent.dom 导入 build_compact_dom，本模块回归零 agent 依赖。
"""

from __future__ import annotations

import json
import re

# 抽取最外层 {...} 的正则（用于 json.loads 失败后的兜底）
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.S)


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
    """从字典安全取数值字段（兼容 "0.5" 字符串）；无效返回 default。

    注意：bool 是 int 子类，#11 需先排除，避免模型返回 true 被当作 1.0 穿透护栏。
    """
    value = data.get(key)
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default
