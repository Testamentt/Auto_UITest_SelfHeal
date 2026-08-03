"""知识库工厂：按 settings.knowledge.backend 返回对应后端。

调用方不直接构造具体实现，面向 KnowledgeBackend 接口编程。
"""

from __future__ import annotations

from selfheal.config import Settings
from selfheal.knowledge.sqlite_store import SqliteKnowledgeStore
from selfheal.knowledge.store import KnowledgeStore


def build_knowledge_store(settings: Settings) -> KnowledgeStore | SqliteKnowledgeStore:
    """按配置构建知识库后端（sqlite 持久化 / memory 进程内）。"""
    cfg = settings.knowledge
    if cfg.backend == "sqlite":
        return SqliteKnowledgeStore(cfg.path)
    return KnowledgeStore()
