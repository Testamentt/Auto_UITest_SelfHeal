"""知识库后端抽象。

定义知识存储与检索的统一接口（Protocol）。内存实现（store.KnowledgeStore）与
SQLite 实现（sqlite_store.SqliteKnowledgeStore）均遵循该接口，由 factory 按配置选择，
调用方（orchestrator / PopupGuard / 测试）不感知具体后端。
"""

from __future__ import annotations

from typing import Protocol

from selfheal.knowledge.schema import PopupFeature, RepairCase


class KnowledgeBackend(Protocol):
    """知识库统一接口（内存 / SQLite 实现均遵循）。"""

    def add_repair(self, case: RepairCase) -> None:
        """沉淀一条修复案例。"""
        ...

    def add_popup(self, feature: PopupFeature) -> None:
        """沉淀一条弹窗特征。"""
        ...

    def find_repair(
        self, original_selector: str, dom_fingerprint: str | None = None
    ) -> RepairCase | None:
        """按原定位器检索修复案例；给定 dom_fingerprint 时优先同结构页面。"""
        ...

    def find_popup(self, signature: str) -> PopupFeature | None:
        """按弹窗签名检索关闭方式。"""
        ...

    def count_popups(self) -> int:
        """返回已沉淀的弹窗特征数量。"""
        ...
