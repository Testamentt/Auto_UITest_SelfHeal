"""多策略修复（可插拔）。

每个策略继承 base.RepairStrategy 并实现 repair()，返回带置信度的候选定位器。
orchestrator 按配置的 strategy_order 依次调度。新增策略只需在此目录新增并在注册表登记。
"""

from selfheal.agent.strategies.base import RepairStrategy
from selfheal.agent.strategies.heuristic import HeuristicStrategy
from selfheal.agent.strategies.semantic import SemanticStrategy
from selfheal.agent.strategies.visual import VisualStrategy

__all__ = ["RepairStrategy", "HeuristicStrategy", "SemanticStrategy", "VisualStrategy"]
