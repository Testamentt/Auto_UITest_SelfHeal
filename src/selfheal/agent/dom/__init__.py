"""DOM 公共能力包（A5 拆包，决策 D8 落点）。

heuristic（启发式打分）、llm_io（构造精简 DOM 给 LLM）、semantic（防幻觉护栏）、
orchestrator（指纹/上下文）、popup_guard（关闭按钮稳定定位器）等共用的底层能力，
按职责拆为四个子模块（parser / selector_builder / fingerprint / extractor），
本 __init__ 统一 re-export，保持 `from selfheal.agent.dom import X` 兼容。
"""

from selfheal.agent.dom.extractor import ElementContext, extract_element_context
from selfheal.agent.dom.fingerprint import (
    compute_page_fingerprint,
    compute_repair_key,
    dom_fingerprint,
)
from selfheal.agent.dom.parser import Element, parse_interactive_elements
from selfheal.agent.dom.selector_builder import build_stable_selector

__all__ = [
    "Element",
    "ElementContext",
    "build_stable_selector",
    "parse_interactive_elements",
    "dom_fingerprint",
    "compute_page_fingerprint",
    "compute_repair_key",
    "extract_element_context",
]
