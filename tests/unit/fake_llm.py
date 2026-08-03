"""单元测试辅助：可注入响应的 Fake LLM 客户端（不触网）。"""

from __future__ import annotations

from selfheal.llm.base import ChatMessage, LLMClient


class FakeLLMClient(LLMClient):
    """三种模式：按序返回 canned 回复 / 抛异常 / 返回垃圾串。记录每次调用参数。"""

    def __init__(self, responses=None, raise_on_call=None):
        self._responses = list(responses) if responses else []
        self._raise_on_call = raise_on_call
        self.calls: list[list[ChatMessage]] = []

    def chat(self, messages: list[ChatMessage], **kwargs) -> str:
        self.calls.append(messages)
        if self._raise_on_call is not None:
            raise self._raise_on_call
        if self._responses:
            return self._responses.pop(0)
        return ""
