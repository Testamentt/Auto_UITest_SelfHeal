"""LLM / VLM 可用性判定与构建（agent 层唯一入口）。

get_llm_for_settings / get_vision_for_settings 集中做四级检查
（enabled → API key → provider 已注册 → SDK 可导入），全部通过才实例化客户端；
任一步不满足返回 None，使 agent 层完全不感知 provider 细节。

2026-10-08 审查修复（H4/G1）：
- 每个 `return None` 都带**可区分原因**的 warning（此前五类故障静默收敛成同一个 None，
  唯一线索是报告里 llm_calls=0）；只打印环境变量**名**，绝不打印值。
- provider 未注册改为先查注册表再构建（不再用 KeyError 做控制流，避免吞掉内部 KeyError）。
- H3/H5：timeout_s / max_tokens / max_retries / max_image_bytes 全部由配置透传。
"""

from __future__ import annotations

import logging

from selfheal.config import Settings
from selfheal.llm._exceptions import UnavailableError
from selfheal.llm.base import LLMClient, VisionClient
from selfheal.llm.openai_client import get_api_key
from selfheal.llm.registry import (
    get_llm,
    get_vision,
    llm_provider_names,
    vision_provider_names,
)

logger = logging.getLogger(__name__)


def get_llm_for_settings(settings: Settings) -> LLMClient | None:
    """按配置构建可用 LLM 客户端；不可用返回 None（调用方优雅降级）。

    不可用时记 warning 说明**具体原因**（enabled 关闭 / 缺 key / provider 未注册 / SDK 不可用）。
    """
    llm_cfg = settings.llm
    if not llm_cfg.enabled:
        logger.warning("LLM 不可用：llm.enabled=false（诊断退规则式、语义策略被跳过）")
        return None
    api_key = get_api_key(llm_cfg.api_key_env)
    if not api_key:
        logger.warning(
            "LLM 不可用：环境变量 %s 未设置或为空（只报变量名，不打印值）", llm_cfg.api_key_env
        )
        return None
    if llm_cfg.provider not in llm_provider_names():
        logger.warning(
            "LLM 不可用：provider %r 未注册（已注册：%s）——检查配置拼写",
            llm_cfg.provider,
            llm_provider_names(),
        )
        return None
    try:
        return get_llm(
            llm_cfg.provider,
            api_key=api_key,
            model=llm_cfg.model,
            base_url=llm_cfg.base_url,
            temperature=llm_cfg.temperature,
            timeout_s=llm_cfg.timeout_s,  # H3：超时/上限/重试均可配置
            max_tokens=llm_cfg.max_tokens,
            max_retries=llm_cfg.max_retries,
            provider=llm_cfg.provider,  # M1：异常信息带 provider，便于定位 key/端点错配
        )
    except UnavailableError as exc:  # SDK 缺失 / 不可用
        logger.warning("LLM 不可用：%s", exc)
        return None


def get_vision_for_settings(settings: Settings) -> VisionClient | None:
    """按配置构建可用 VLM 客户端；不可用返回 None（视觉策略被跳过）。"""
    vcfg = settings.vision
    if not vcfg.enabled:
        logger.warning("VLM 不可用：vision.enabled=false（视觉策略被跳过）")
        return None
    api_key = get_api_key(vcfg.api_key_env)
    if not api_key:
        logger.warning(
            "VLM 不可用：环境变量 %s 未设置或为空（只报变量名，不打印值）", vcfg.api_key_env
        )
        return None
    if vcfg.provider not in vision_provider_names():
        logger.warning(
            "VLM 不可用：provider %r 未注册（已注册：%s）——检查配置拼写",
            vcfg.provider,
            vision_provider_names(),
        )
        return None
    try:
        return get_vision(
            vcfg.provider,
            api_key=api_key,
            model=vcfg.model,
            base_url=vcfg.base_url,
            timeout_s=vcfg.timeout_s,  # T23：plus 级模型响应慢，超时/上限可配置
            max_tokens=vcfg.max_tokens,
            max_retries=vcfg.max_retries,  # M1：显式重试
            max_image_bytes=vcfg.max_image_bytes,  # H5：图片体积护栏
            provider=vcfg.provider,
        )
    except UnavailableError as exc:  # SDK 缺失 / 不可用
        logger.warning("VLM 不可用：%s", exc)
        return None
