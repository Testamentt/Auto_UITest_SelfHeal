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
        # TODO: 相似度检索，返回置信度最高的 RepairCase
        for case in self._repairs:
            if case.original_selector == original_selector:
                return case
        return None

    def find_popup(self, signature: str):
        for feature in self._popups:
            if feature.signature == signature:
                return feature
        return None
