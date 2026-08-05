"""OpenAI 兼容视觉模型（VLM）客户端。

qwen3-vl-flash 等多模态模型走 OpenAI 兼容端点；analyze_image 把 base64 图片经
chat completions（content 数组含 image_url）发给模型，返回文本分析。
openai SDK 惰性导入，缺失时抛 UnavailableError，由上层优雅降级（决策 D7）。
"""

from __future__ import annotations

import base64
from typing import Any

from selfheal.llm._exceptions import UnavailableError
from selfheal.llm.base import VisionClient
from selfheal.llm.registry import register_vision


class OpenAICompatibleVLM(VisionClient):
    """基于 openai SDK 的视觉模型客户端（惰性初始化底层 client）。"""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str | None = None,
        timeout_s: float = 20.0,
        max_tokens: int = 500,
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._max_tokens = max_tokens
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        """惰性创建底层 OpenAI client；SDK 缺失时抛 UnavailableError。"""
        if self._client is None:
            try:
                from openai import OpenAI  # 惰性导入：避免顶层强依赖
            except ImportError as exc:
                raise UnavailableError("未安装 openai 包，无法使用 VLM 能力") from exc
            kwargs: dict[str, Any] = {"api_key": self._api_key, "timeout": self._timeout_s}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def analyze_image(self, image: bytes, prompt: str, **kwargs: Any) -> str:
        """发送截图 + 提示词，返回模型文本分析。

        #11 降级契约收敛：SDK 异常统一转抛 UnavailableError（from exc），调用方捕获即可降级。
        """
        client = self._ensure_client()
        b64 = base64.b64encode(image).decode("utf-8")
        try:
            resp = client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        ],
                    }
                ],
                max_tokens=self._max_tokens,
            )
        except UnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - SDK 网络/鉴权/限流异常归一化
            raise UnavailableError(f"视觉模型调用失败: {type(exc).__name__}") from exc
        if not resp.choices:
            raise UnavailableError("模型返回了空 choices")
        content = resp.choices[0].message.content
        if content is None:
            raise UnavailableError("模型返回了空内容")
        return content


@register_vision("openai")
def _openai_vision_factory(api_key: str, model: str, **kwargs: Any) -> OpenAICompatibleVLM:
    """openai provider 的视觉客户端注册工厂。"""
    return OpenAICompatibleVLM(api_key, model, **kwargs)
