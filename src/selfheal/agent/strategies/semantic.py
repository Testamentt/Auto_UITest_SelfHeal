"""语义定位策略。

把元素的自然语言描述 + DOM 交给 LLM，由模型推断最匹配的定位器。
TODO: 经 llm.get_llm 调用，约束模型输出结构化 selector + 置信度。
"""

from __future__ import annotations

from selfheal.agent.strategies.base import RepairCandidate, RepairStrategy
from selfheal.collect.collector import Scene


class SemanticStrategy(RepairStrategy):
    name = "semantic"

    def repair(self, scene, original_selector, description=None) -> RepairCandidate | None:
        if not description:
            return None
        # TODO: 构造语义定位 prompt 调用 LLM
        return None
