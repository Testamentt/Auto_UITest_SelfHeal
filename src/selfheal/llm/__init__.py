"""LLM / VLM 抽象层。

业务代码只依赖本模块定义的抽象接口，通过 registry 获取具体 provider，
从而与任何具体模型 SDK 解耦，支持后续无痛切换。
openai_client 在顶层被导入以完成 @register_llm 注册（其内部对 openai SDK 为惰性导入）。
"""

from selfheal.llm._exceptions import UnavailableError
from selfheal.llm.base import ChatMessage, LLMClient, VisionClient
from selfheal.llm.embedding import EmbeddingClient, NgramEmbedding, get_embedding_for_settings
from selfheal.llm.factory import get_llm_for_settings, get_vision_for_settings
from selfheal.llm.openai_client import OpenAICompatibleLLM
from selfheal.llm.openai_vision import OpenAICompatibleVLM
from selfheal.llm.registry import get_llm, get_vision, register_llm, register_vision

__all__ = [
    "ChatMessage",
    "LLMClient",
    "VisionClient",
    "OpenAICompatibleLLM",
    "OpenAICompatibleVLM",
    "EmbeddingClient",
    "NgramEmbedding",
    "UnavailableError",
    "get_llm",
    "get_vision",
    "get_llm_for_settings",
    "get_vision_for_settings",
    "get_embedding_for_settings",
    "register_llm",
    "register_vision",
]
