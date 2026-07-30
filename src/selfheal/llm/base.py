"""模型客户端抽象接口（provider 无关）。"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class ChatMessage:
    role: str  # system / user / assistant
    content: str


class LLMClient(abc.ABC):
    """文本大模型抽象。具体 provider（openai / 其它）实现本接口并在 registry 注册。"""

    @abc.abstractmethod
    def chat(self, messages: list[ChatMessage], **kwargs) -> str:
        """发送对话请求，返回模型文本回复。"""

    def complete(self, prompt: str, **kwargs) -> str:
        return self.chat([ChatMessage("user", prompt)], **kwargs)


class VisionClient(abc.ABC):
    """多模态视觉模型抽象，用于视觉定位与控件画像。"""

    @abc.abstractmethod
    def analyze_image(self, image: bytes, prompt: str, **kwargs) -> str:
        """基于截图与提示词返回分析结果（如控件坐标 / 描述）。"""
