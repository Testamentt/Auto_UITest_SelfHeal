"""模型 provider 注册表。

通过名字注册/获取 LLMClient 与 VisionClient 实现，实现业务与具体 SDK 解耦。
当前已注册：openai（OpenAICompatibleLLM / OpenAICompatibleVLM，见 openai_client / openai_vision）。
新 provider 在此或独立模块实现具体 client 并注册。
"""

from __future__ import annotations

from collections.abc import Callable

from selfheal.llm.base import LLMClient, VisionClient

_LLM_FACTORIES: dict[str, Callable[..., LLMClient]] = {}
_VISION_FACTORIES: dict[str, Callable[..., VisionClient]] = {}


def register_llm(name: str):
    """装饰器：注册一个 LLM client 工厂。用法：@register_llm("openai")"""

    def deco(factory: Callable[..., LLMClient]):
        _LLM_FACTORIES[name] = factory
        return factory

    return deco


def register_vision(name: str):
    def deco(factory: Callable[..., VisionClient]):
        _VISION_FACTORIES[name] = factory
        return factory

    return deco


def get_llm(name: str, **kwargs) -> LLMClient:
    if name not in _LLM_FACTORIES:
        raise KeyError(f"未注册的 LLM provider: {name}（可用: {list(_LLM_FACTORIES)}）")
    return _LLM_FACTORIES[name](**kwargs)


def get_vision(name: str, **kwargs) -> VisionClient:
    if name not in _VISION_FACTORIES:
        raise KeyError(f"未注册的 Vision provider: {name}（可用: {list(_VISION_FACTORIES)}）")
    return _VISION_FACTORIES[name](**kwargs)
