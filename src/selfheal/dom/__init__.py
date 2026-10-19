"""DOM 公共能力包（中立包；A5 拆包，M10-③ 自 agent/dom 下沉至此）。

heuristic（启发式打分）、diagnose_llm/semantic（构造精简 DOM 给 LLM，见 compact）、
semantic（防幻觉护栏）、orchestrator（指纹/上下文）、engine/popup_guard（关闭按钮稳定定位器）、
collect/collector（原生候选解析）等共用的底层能力，按职责拆为五个子模块
（parser / selector_builder / fingerprint / extractor / compact），本 __init__ 统一 re-export。

**分层位置（M10-③）**：本包是**中立底层**——只依赖标准库与自身子模块，
不得 import agent / knowledge / reporting / engine / collect（否则 engine 与 collect 会经由
本包被动拉起上层，分层倒置回流）。原 `selfheal.agent.dom` 路径已随下沉取消（无兼容 shim），
全仓 import 统一为 `from selfheal.dom import X`；公开名字与行为不变。
"""

from selfheal.dom.compact import build_compact_dom
from selfheal.dom.extractor import ElementContext, extract_element_context
from selfheal.dom.fingerprint import (
    compute_page_fingerprint,
    compute_repair_key,
    dom_fingerprint,
)
from selfheal.dom.parser import (
    CrossCheck,
    Element,
    cross_validate_interactive,
    interactive_candidates,
    parse_interactive_elements,
    parse_interactive_elements_native,
)
from selfheal.dom.selector_builder import build_stable_selector

__all__ = [
    "Element",
    "ElementContext",
    "CrossCheck",
    "build_compact_dom",
    "build_stable_selector",
    "parse_interactive_elements",
    "parse_interactive_elements_native",
    "interactive_candidates",
    "cross_validate_interactive",
    "dom_fingerprint",
    "compute_page_fingerprint",
    "compute_repair_key",
    "extract_element_context",
]
