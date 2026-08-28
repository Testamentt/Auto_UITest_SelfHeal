"""TODO(workaround): LLM I/O 基础设施已迁至 `selfheal.llm.io`（T10 工具模块归位）。

保留本模块仅为兼容旧导入路径（一次 import 跳转，无任何逻辑）——调用方已全部更新为
新路径（agent/diagnose_llm.py、agent/strategies/semantic.py、agent/strategies/visual.py、
tests/unit/test_llm_io.py）。**回收方式**：确认无外部引用后删除本文件即可（无迁移成本）。
"""

from __future__ import annotations

from selfheal.llm.io import build_compact_dom, extract_json, safe_float, safe_str

__all__ = ["build_compact_dom", "extract_json", "safe_float", "safe_str"]
