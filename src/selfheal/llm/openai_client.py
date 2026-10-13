"""OpenAI 兼容 LLM 客户端（决策 D2 落地）。

一个实现通过 base_url + model 覆盖 OpenAI / DeepSeek / Qwen / 智谱 等全部
OpenAI 兼容接口，provider 切换只改配置、不改代码。

关键设计：openai SDK 在 chat() 内**惰性导入**——本模块顶层不依赖 openai，
未安装该包的纯逻辑环境（如 CI 单测）也能 import；SDK 缺失时抛 UnavailableError，
由上层（诊断 / 语义策略）捕获后优雅降级。

2026-10-08 审查修复：
- M1：异常按可恢复性分级（见 llm/_exceptions.py），错误信息带 provider/model/端点主机；
  `max_retries` 显式设置并可配置（此前静默继承 SDK 默认 2，一次失败最多 3 次付费调用）。
- H3：`timeout_s` / `max_tokens` / `max_retries` 由 config.LLMConfig 透传（此前写死在签名里，
  且 `extra='forbid'` 让用户无法在 llm: 段配置它们）。
- L8：`chat()` 的 `**kwargs` 改为**显式拒绝**——抽象接口（base.py）与调用方都在转发 kwargs，
  实现却完全忽略，等于"传了就丢"（如 `chat(..., max_tokens=100)` 静默按构造值发送）。
- L9：输出上限字段名可配置（`max_tokens_field`）。新模型族要求 `max_completion_tokens`，
  且不兼容自定义 temperature；切换字段名时**省略 temperature**（不发 1 之外的非法值），
  被忽略的 temperature 记 warning（不静默）。
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any, Literal

from selfheal.llm._exceptions import UnavailableError, classify_sdk_error
from selfheal.llm.base import ChatMessage, LLMClient
from selfheal.llm.registry import register_llm

logger = logging.getLogger(__name__)

# 输出上限字段名（L9）：新模型族改用 max_completion_tokens，两者互斥，只能出现一个。
MaxTokensField = Literal["max_tokens", "max_completion_tokens"]


def get_api_key(env_var: str) -> str | None:
    """从环境变量读取密钥；缺失或空白返回 None。"""
    value = os.getenv(env_var, "")
    return value.strip() or None


def reject_unsupported_kwargs(api: str, unsupported: dict[str, Any]) -> None:
    """拒绝未支持的调用期参数（L8）：显式抛 ``TypeError``，列出参数名与去路。

    为什么抛 TypeError 而非 UnavailableError：传错的 kwargs 是**编程错误**（拼错 / 误以为
    支持调用级覆盖），不是模型服务不可用。抛 UnavailableError 会被上层
    `except UnavailableError` 静默降级（策略跳过、llm_calls=0），故障反而更难发现；
    TypeError 是调用方 bug 的自然语义，且在策略层的兜底 `except Exception` 中仍被记为
    warning（不中断流水线）。要调温度 / 上限请改 `config/settings.yaml` 的 llm:/vision: 段。
    """
    if not unsupported:
        return
    names = "、".join(sorted(unsupported))
    raise TypeError(
        f"{api}() 不支持参数 [{names}]：本实现只接受签名内声明的参数，"
        f"不再静默丢弃（L8）。温度 / 上限 / 超时请改 config/settings.yaml 的 llm:/vision: 段。"
    )


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
        max_tokens_field: MaxTokensField = "max_tokens",
        max_retries: int = 2,
        provider: str = "openai",
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._temperature = temperature
        self._timeout_s = timeout_s
        self._max_tokens = max_tokens
        self._max_tokens_field = max_tokens_field
        self._max_retries = max_retries
        self._provider = provider
        self._client: Any | None = None
        self._temperature_ignored_warned = False

    def _ensure_client(self) -> Any:
        """惰性创建底层 OpenAI client；SDK 缺失时抛 UnavailableError。"""
        if self._client is None:
            try:
                from openai import OpenAI  # 惰性导入：避免顶层强依赖
            except ImportError as exc:
                raise UnavailableError("未安装 openai 包，无法使用 LLM 能力") from exc
            kwargs: dict[str, Any] = {
                "api_key": self._api_key,
                "timeout": self._timeout_s,
                # 显式重试策略：不写死依赖 SDK 默认值（审查 M1：默认重试不可见也不可控）
                "max_retries": self._max_retries,
            }
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def _request_payload(self, messages: list[ChatMessage]) -> dict[str, Any]:
        """构造 chat.completions.create 的关键字参数（L9：字段名与 temperature 兼容性）。

        - 输出上限**只用一种字段名**：`max_tokens` 或 `max_completion_tokens`（互斥，
          同时发送会被服务端按未知/非法参数 400 拒绝）。
        - `max_completion_tokens` 族（推理模型）不接受自定义 temperature，故**省略该键**，
          由服务端用自身默认值；配置里的 temperature 若与默认不同则记一次 warning，
          不静默丢弃用户意图（R4）。
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            self._max_tokens_field: self._max_tokens,
        }
        if self._max_tokens_field == "max_completion_tokens":
            if self._temperature != 1.0 and not self._temperature_ignored_warned:
                self._temperature_ignored_warned = True
                logger.warning(
                    "max_tokens_field=max_completion_tokens：该模型族不支持自定义 temperature，"
                    "已省略（配置值 %.2f 不生效；需要固定温度请改用 max_tokens 字段族）",
                    self._temperature,
                )
        else:
            payload["temperature"] = self._temperature
        return payload

    def chat(self, messages: list[ChatMessage], **kwargs: Any) -> str:
        """发送对话请求并返回文本回复。

        #11 降级契约收敛：SDK 调用统一捕获并转抛 UnavailableError（from exc 保留原因），
        调用方只需捕获 UnavailableError 即可完成降级，不依赖裸 except Exception。
        M1：转抛的是**分级**子类（Fatal / Transient），信息含 provider/model/端点主机。
        L8：未支持的调用期参数显式抛 ``TypeError``（见 reject_unsupported_kwargs）。
        """
        reject_unsupported_kwargs("OpenAICompatibleLLM.chat", kwargs)
        client = self._ensure_client()
        try:
            resp = client.chat.completions.create(**self._request_payload(messages))
        except UnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 - SDK 网络/鉴权/限流异常归一化
            raise classify_sdk_error(
                exc, provider=self._provider, model=self._model, base_url=self._base_url
            ) from exc
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
