"""智能诊断（v1：规则式，无 LLM）。

Phase 1 不接入大模型，用轻量规则对失败根因做粗分类，供报告与策略参考：
- not_found：选择器主键在 DOM 中完全不存在（最常见，UI 改版导致）。
- not_visible：主键存在但当前不可见（可能被遮挡/隐藏）。
- unknown：无法仅凭 DOM 判定（交后续策略尝试修复）。

Phase 2 的 LLM 诊断（diagnose_llm.LLMDiagnoser）继承本类，
本规则式保留为降级基底（LLM 不可用时回退到它）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from selfheal.collect.collector import Scene

# 从选择器中提取可辨识主键：#id、[attr="v"]、纯文本里的单词等
_SELECTOR_KEY_RE = re.compile(r"[A-Za-z][\w-]*")


@dataclass
class FailureContext:
    """定位失败的上下文（供 LLM 诊断参考，可空）。"""

    failure_type: str | None = None  # 异常类型名，如 TimeoutError
    message: str | None = None  # 异常信息


def _selector_keys(selector: str) -> list[str]:
    """抽出选择器中的候选主键（去掉 #、.、[] 等语法符号后的词）。"""
    return [t.lower() for t in _SELECTOR_KEY_RE.findall(selector or "")]


class Diagnoser:
    """规则式根因诊断器。"""

    def diagnose(
        self,
        scene: Scene,
        selector: str,
        failure: FailureContext | None = None,
    ) -> str:
        # failure 在规则式中不参与判定；保留参数以与 LLMDiagnoser 签名一致（多态）
        dom = (scene.dom_snapshot or "").lower()
        keys = _selector_keys(selector)
        if not keys:
            return "unknown"
        # 任一主键出现在 DOM 即认为"元素可能还在"，否则判定为 not_found
        if any(k in dom for k in keys):
            return "unknown"
        return "not_found"
