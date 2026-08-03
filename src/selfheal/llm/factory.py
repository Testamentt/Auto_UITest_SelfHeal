"""LLM 可用性判定与构建（agent 层唯一入口）。

get_llm_for_settings 集中做四级检查（enabled → API key → provider 已注册 → SDK 可导入），
全部通过才实例化客户端；任一步不满足返回 None，使 agent 层完全不感知 provider 细节。
"""

from __future__ import annotations

from selfheal.config import Settings
from selfheal.llm._exceptions import UnavailableError
from selfheal.llm.base import LLMClient
from selfheal.llm.openai_client import get_api_key
from selfheal.llm.registry import get_llm


def get_llm_for_settings(settings: Settings) -> LLMClient | None:
    """按配置构建可用 LLM 客户端；不可用返回 None（调用方优雅降级）。"""
    llm_cfg = settings.llm
    if not llm_cfg.enabled:
        return None
    api_key = get_api_key(llm_cfg.api_key_env)
    if not api_key:
        return None
    try:
        return get_llm(
            llm_cfg.provider,
            api_key=api_key,
            model=llm_cfg.model,
            base_url=llm_cfg.base_url,
            temperature=llm_cfg.temperature,
        )
    except KeyError:  # provider 未注册
        return None
    except UnavailableError:  # SDK 缺失 / 不可用
        return None
