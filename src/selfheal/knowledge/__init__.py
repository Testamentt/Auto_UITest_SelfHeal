"""知识库 —— 弹窗特征库 + 修复案例库。

自愈闭环的"记忆"：命中已有案例可跳过 LLM 调用，降本增效；成功修复回写沉淀。
后端：内存（store.KnowledgeStore）/ SQLite 持久化（sqlite_store.SqliteKnowledgeStore），
由 factory.build_knowledge_store 按 settings.knowledge.backend 选择（决策 D4）。
"""

from selfheal.knowledge.base import KnowledgeBackend
from selfheal.knowledge.factory import build_knowledge_store
from selfheal.knowledge.schema import PopupFeature, RepairCase
from selfheal.knowledge.sqlite_store import SqliteKnowledgeStore
from selfheal.knowledge.store import KnowledgeStore

__all__ = [
    "KnowledgeBackend",
    "KnowledgeStore",
    "SqliteKnowledgeStore",
    "PopupFeature",
    "RepairCase",
    "build_knowledge_store",
]
