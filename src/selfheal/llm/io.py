"""LLM / VLM 公共输入/输出编解码工具（T10 建立本模块；m1 收敛，V5 复核；L7 解析健壮性）。

归属边界（m1）：本模块只保留与 DOM 无关的**纯 I/O 编解码**——
- extract_json：从模型回复中容错抽取 JSON 对象（剥 markdown fence → raw_decode 逐个候选）。
- safe_float / safe_str：容错提取字段，避免模型格式不稳定导致崩溃。

依赖 DOM 解析的"模型输入准备"（build_compact_dom，构造精简 DOM）已上移至
`selfheal.dom.compact`（M10-③ 前为 `selfheal.agent.dom.compact`）——此前本模块
import `selfheal.agent.dom`，让 provider 无关的 LLM 抽象层反向依赖 agent 层（llm→agent
分层违规）。调用方（diagnose_llm / semantic）改从 `selfheal.dom` 导入 build_compact_dom，
本模块回归零 agent 依赖。
"""

from __future__ import annotations

import json
import math
import re

# 扫描 JSON 对象起始位置用的锚点（真正的解析交给 json.JSONDecoder().raw_decode）。
_OBJECT_START = "{"
# 剥 markdown ```json ... ``` 围栏
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def _decoder() -> json.JSONDecoder:
    """构造宽松 JSON 解码器（strict=False 容忍串内裸控制字符；模型回复常含换行）。"""
    return json.JSONDecoder(strict=False)


def _yield_json_objects(text: str):
    """按出现顺序逐个吐出文本中的 JSON **对象**（dict）；不可解析位置静默跳过。

    用 `raw_decode` 从每个 `{` 处**就地在原串上解码**，而不是正则截取 `{.*}` 片段——
    正则版本对"多个对象 / 嵌套对象 / 串内花括号"只能给出一个贪婪候选，解析失败就直接
    返回 None，丢弃本可用的候选（L7 的静默丢候选）。`raw_decode` 还顺带给出结束位置，
    便于从下一个位置继续扫描（不重复解析同一对象）。
    """
    decoder = _decoder()
    pos = 0
    length = len(text)
    while pos < length:
        start = text.find(_OBJECT_START, pos)
        if start < 0:
            return
        try:
            parsed, end = decoder.raw_decode(text, start)
        except ValueError:  # 该位置不是合法 JSON 起点（如 "{{" / "{a:1}"）→ 继续向后扫
            pos = start + 1
            continue
        if isinstance(parsed, dict):
            yield parsed
        pos = end if end > start else start + 1


def extract_json(text: str | None) -> dict | None:
    """从模型回复中抽取首个 JSON **对象**；无可用对象返回 None。

    候选优先级（L7）：
    1. 整串（模型严格输出 JSON 时的快路径）；
    2. markdown 围栏内的内容（```json ... ``` / ``` ... ```）；
    3. 整串中按出现顺序扫描到的 JSON 对象——**逐个尝试而非只取贪婪片段**，
       因此"先输出一个坏对象、再输出一个合法对象"这类回复也能取到可用结果。
    """
    if not text:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    candidates = [stripped]
    fence = _FENCE_RE.search(stripped)
    if fence:
        candidates.append(fence.group(1))
    for candidate in candidates:
        for parsed in _yield_json_objects(candidate):
            return parsed
    return None


def safe_str(data: dict, key: str) -> str:
    """从字典安全取字符串字段；缺失/非字符串返回空串。"""
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def safe_float(data: dict, key: str, default: float = 0.0) -> float:
    """从字典安全取**有限**数值字段（兼容 "0.5" 字符串）；无效返回 default。

    护栏（顺序不可调换）：
    1. bool 是 int 子类，#11 需先排除，避免模型返回 `true` 被当作 1.0 穿透护栏；
    2. 非有限值一律按无效处理（L7）：`"nan"` / `"inf"` / `"-Infinity"` / `float("inf")`
       虽能被 `float()` 吃下，却会让置信度比较全部失真（`nan >= 阈值` 恒 False、
       `inf` 恒 True）——静默错误，必须挡在解析层。
    """
    value = data.get(key)
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else default
    if isinstance(value, str):
        try:
            parsed = float(value.strip())
        except ValueError:
            return default
        return parsed if math.isfinite(parsed) else default
    return default
