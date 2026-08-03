"""知识库存储与检索。

TODO: 实现持久化后端与相似度检索（DOM 指纹 / 向量）。
当前为内存占位实现，保证骨架可运行。
"""

from __future__ import annotations

from selfheal.knowledge.schema import PopupFeature, RepairCase


class KnowledgeStore:
    def __init__(self) -> None:
        self._repairs: list[RepairCase] = []
        self._popups: list[PopupFeature] = []

    def add_repair(self, case: RepairCase) -> None:
        self._repairs.append(case)

    def add_popup(self, feature: PopupFeature) -> None:
        self._popups.append(feature)

    def find_repair(self, original_selector: str, dom_fingerprint: str | None = None):
        """按原定位器检索；多条命中时优先指纹匹配者，否则取置信度最高（对齐 SQLite 语义）。"""
        matches = [c for c in self._repairs if c.original_selector == original_selector]
        if not matches:
            return None
        if dom_fingerprint:
            for case in matches:
                if case.dom_fingerprint == dom_fingerprint:
                    return case
        return max(matches, key=lambda c: c.confidence)

    def find_popup(self, signature: str):
        for feature in self._popups:
            if feature.signature == signature:
                return feature
        return None

    def count_popups(self) -> int:
        return len(self._popups)
