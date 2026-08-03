"""单元测试辅助：可注入响应的 Fake 视觉客户端（不触网）。"""

from __future__ import annotations

from selfheal.llm.base import VisionClient


class FakeVisionClient(VisionClient):
    """按序返回 canned 回复或抛异常，记录每次调用（image, prompt）。"""

    def __init__(self, responses=None, raise_on_call=None):
        self._responses = list(responses) if responses else []
        self._raise_on_call = raise_on_call
        self.calls: list[tuple[bytes, str]] = []

    def analyze_image(self, image: bytes, prompt: str, **kwargs) -> str:
        self.calls.append((image, prompt))
        if self._raise_on_call is not None:
            raise self._raise_on_call
        if self._responses:
            return self._responses.pop(0)
        return ""
