"""知识库工厂：按 settings.knowledge.backend 返回对应后端。

调用方不直接构造具体实现，面向 KnowledgeBackend 接口编程。
"""

from __future__ import annotations

import logging
import sqlite3

from selfheal.config import Settings
from selfheal.knowledge.sqlite_store import SqliteKnowledgeStore
from selfheal.knowledge.store import KnowledgeStore

logger = logging.getLogger(__name__)


def build_knowledge_store(settings: Settings) -> KnowledgeStore | SqliteKnowledgeStore:
    """按配置构建知识库后端（sqlite 持久化 / memory 进程内）。

    V5 复核：sqlite 打开/迁移失败（如库文件损坏）不再炸掉整个会话（此前
    HealingPage/conftest fixture 阶段直接报错且无降级）。
    M4：降级按 error 级记录——跨重启持久化是该后端的核心能力，丢失属功能降级而非
    warning 级噪声（此前只记 warning，容易被忽略成"正常"），消息明文写清
    "已降级 memory、持久化不可用"，不静默。
    """
    cfg = settings.knowledge
    if cfg.backend == "sqlite":
        try:
            return SqliteKnowledgeStore(cfg.path)
        except sqlite3.Error as exc:
            logger.error(
                "sqlite 知识库构建失败（path=%s）：已降级 memory 后端，跨重启持久化不可用: %s",
                cfg.path,
                exc,
            )
    return KnowledgeStore()
