"""跨层共享的中立契约（M10：分层倒置修复的落点）。

**为什么单独成模块**：`engine/`（能力建设层）与 `collect/`（数据采集层）在定位失败时
要产出 / 消费「失败上下文」与「自愈结果」；这些 DTO 此前定义在上层
（`agent/diagnose.py` / `agent/context.py`），迫使下层反向 import 上层——分层倒置
（评审 M10 / docs/backlog/low-priority.md §二）。把 DTO 放到不依赖任何业务层的本模块后，
engine / collect 的 import 闭包不再拉起 agent / knowledge / reporting
（分层护栏见 `tests/unit/test_layering.py`）。

**内容**：
- `FailureContext`：定位失败的上下文（闭环输入）。
- `HealOutcome`：一次自愈的最终结果（闭环输出，engine 侧据此决定重试/兜底）。
- `PopupFeature`：弹窗特征 DTO——`engine/popup_guard` 产出、`knowledge/` 持久化，双方共用；
  定义留在此处（而非 knowledge）是为了让 engine 不反向依赖 knowledge。
- `TraceActiveProvider`：trace 录制状态的 provider 类型（L4）——由外层（conftest / 夹具）
  注入 `collect.SceneCollector(trace_active=…)`，并经 agent / plugin 逐层透传，
  使"是否在录制"成为显式状态而非异常探测。

**零破坏**：原位置保留 re-export，既有 import 路径全部继续可用——
`from selfheal.agent.diagnose import FailureContext`、`from selfheal.agent.context import HealOutcome`
（亦经 `selfheal.agent.orchestrator` 透出）、`from selfheal.knowledge.schema import PopupFeature`。

**约束**：本模块只允许依赖标准库——任何业务层 import 都会让分层护栏失去意义。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# trace 录制状态 provider（L4）：返回"当前是否正在录制 trace"。
# 与 `collect/collector.py::SceneCollector(trace_active=…)` 的形参同形（调用处按关键字传参），
# 由 conftest 等外层按 CLI 覆盖（--trace-healing / --no-trace-healing）与配置取值构造。
TraceActiveProvider = Callable[[], bool]


@dataclass
class FailureContext:
    """定位失败的上下文（供 LLM 诊断参考，可空）。"""

    failure_type: str | None = None  # 异常类型名，如 TimeoutError
    message: str | None = None  # 异常信息


@dataclass
class HealOutcome:
    """一次自愈的最终结果。

    - proposed_selector（T14 dry_run）: 仅报告模式下建议采用的定位器（不实际应用）。
    - attempt_id（B1）: 策略修复成功后生成的暂存标识；引擎层重试成功后以此 commit_pending。
    """

    success: bool
    new_selector: str | None = None
    confidence: float = 0.0
    strategy: str | None = None
    root_cause: str | None = None
    proposed_selector: str | None = None
    attempt_id: str | None = None


@dataclass
class PopupFeature:
    """一条弹窗特征：如何识别 + 如何关闭。"""

    signature: str  # 识别特征（文本 / 结构指纹）
    dismiss_selector: str  # 关闭按钮定位器
    category: str = "generic"  # permission / ad / system ...


__all__ = ["FailureContext", "HealOutcome", "PopupFeature", "TraceActiveProvider"]
