"""视觉定位策略。

把截图交给多模态视觉模型（VLM），做控件识别 / 坐标定位 / 控件画像。
作为兜底策略，成本最高。视觉模型待定。
TODO: 经 llm.get_vision 调用，返回坐标或可转换的定位信息。
"""

from __future__ import annotations

from selfheal.agent.strategies.base import RepairCandidate, RepairStrategy
from selfheal.collect.collector import Scene


class VisualStrategy(RepairStrategy):
    name = "visual"

    def repair(self, scene, original_selector, description=None) -> RepairCandidate | None:
        if not scene.screenshot:
            return None
        # TODO: 调用 VLM 分析截图
        return None
