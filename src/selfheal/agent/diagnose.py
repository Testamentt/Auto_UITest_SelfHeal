"""智能诊断（v1：规则式，无 LLM）。

Phase 1 不接入大模型，用轻量规则对失败根因做粗分类，供报告与策略参考：
- not_found：选择器主键在 DOM 中完全不存在（最常见，UI 改版导致）。
- not_visible：主键存在但当前不可见（可能被遮挡/隐藏）。
- unknown：无法仅凭 DOM 判定（交后续策略尝试修复）。

TODO(Phase 2)：经 llm.get_llm 结合截图 + DOM 做语义级根因判定。
"""

from __future__ import annotations

import re

from selfheal.collect.collector import Scene

# 从选择器中提取可辨识主键：#id、[attr="v"]、纯文本里的单词等
_SELECTOR_KEY_RE = re.compile(r"[A-Za-z][\w-]*")


def _selector_keys(selector: str) -> list[str]:
    """抽出选择器中的候选主键（去掉 #、.、[] 等语法符号后的词）。"""
    return [t.lower() for t in _SELECTOR_KEY_RE.findall(selector or "")]


class Diagnoser:
    """规则式根因诊断器。"""

    def diagnose(self, scene: Scene, selector: str) -> str:
        dom = (scene.dom_snapshot or "").lower()
        keys = _selector_keys(selector)
        if not keys:
            return "unknown"
        # 任一主键出现在 DOM 即认为"元素可能还在"，否则判定为 not_found
        if any(k in dom for k in keys):
            return "unknown"
        return "not_found"
