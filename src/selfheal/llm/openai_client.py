"""OpenAI 兼容 LLM 客户端（决策 D2 落地）。

一个实现通过 base_url + model 覆盖 OpenAI / DeepSeek / Qwen / 智谱 等全部
OpenAI 兼容接口，provider 切换只改配置、不改代码。

关键设计：openai SDK 在 chat() 内**惰性导入**——本模块顶层不依赖 openai，
未安装该包的纯逻辑环境（如 CI 单测）也能 import；SDK 缺失时抛 UnavailableError，
由上层（诊断 / 语义策略）捕获后优雅降级。
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

from selfheal.llm._exceptions import UnavailableError
from selfheal.llm.base import ChatMessage, LLMClient
from selfheal.llm.registry import register_llm


def get_api_key(env_var: str) -> str | None:
    """从环境变量读取密钥；缺失或空白返回 None。"""
    value = os.getenv(env_var, "")
    return value.strip() or None


class OpenAICompatibleLLM(LLMClient):
    """基于 openai SDK 的 OpenAI 兼容客户端（惰性初始化底层 client）。"""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str | None = None,
        temperature: float = 0.0,
        timeout_s: float = 15.0,
        max_tokens: int = 2000,
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._temperature = temperature
        self._timeout_s = timeout_s
        self._max_tokens = max_tokens
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        """惰性创建底层 OpenAI client；SDK 缺失时抛 UnavailableError。"""
        if self._client is None:
            try:
                from openai import OpenAI  # 惰性导入：避免顶层强依赖
            except ImportError as exc:
                raise UnavailableError("未安装 openai 包，无法使用 LLM 能力") from exc
            kwargs: dict[str, Any] = {"api_key": self._api_key, "timeout": self._timeout_s}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def chat(self, messages: list[ChatMessage], **kwargs: Any) -> str:
        """发送对话请求并返回文本回复。

        #11 降级契约收敛：SDK 调用统一捕获并转抛 UnavailableError（from exc 保留原因），
        调用方只需捕获 UnavailableError 即可完成降级，不依赖裸 except Exception。
        """
        client = self._ensure_client()
        try:
            resp = client.chat.completions.create(
                model=self._model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
        except UnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - SDK 网络/鉴权/限流异常归一化
            raise UnavailableError(f"模型调用失败: {type(exc).__name__}") from exc
        if not resp.choices:
            raise UnavailableError("模型返回了空 choices")
        content = resp.choices[0].message.content
        if content is None:
            raise UnavailableError("模型返回了空内容")
        return content

    def close(self) -> None:
        """幂等释放底层 client（生命周期管理用，非必须）。"""
        if self._client is not None:
            # 释放失败不掩盖业务异常（suppress 等价于 try-except-pass）
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None


@register_llm("openai")
def _openai_factory(api_key: str, model: str, **kwargs: Any) -> OpenAICompatibleLLM:
    """openai provider 注册工厂；kwargs 透传给构造函数。"""
    return OpenAICompatibleLLM(api_key, model, **kwargs)
