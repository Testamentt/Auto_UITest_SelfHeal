"""启发式匹配策略。

不调用模型，基于 DOM 做多属性组合匹配（text / aria-label / name / 相邻稳定锚点等），
成本低、速度快，作为首选策略。
TODO: 解析 scene.dom_snapshot，按相似度打分产出候选。
"""

from __future__ import annotations

from selfheal.agent.strategies.base import RepairCandidate, RepairStrategy
from selfheal.collect.collector import Scene


class HeuristicStrategy(RepairStrategy):
    name = "heuristic"

    def repair(self, scene, original_selector, description=None) -> RepairCandidate | None:
        # TODO: 多属性组合匹配 + 相似度打分
        return None
