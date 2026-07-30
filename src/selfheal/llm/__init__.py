"""LLM / VLM 抽象层。

业务代码只依赖本模块定义的抽象接口，通过 registry 获取具体 provider，
从而与任何具体模型 SDK 解耦，支持后续无痛切换（provider 目前待定）。
"""

from selfheal.llm.base import ChatMessage, LLMClient, VisionClient
from selfheal.llm.registry import get_llm, get_vision, register_llm, register_vision

__all__ = [
    "ChatMessage",
    "LLMClient",
    "VisionClient",
    "get_llm",
    "get_vision",
    "register_llm",
    "register_vision",
]
