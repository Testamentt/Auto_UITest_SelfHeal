# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""LLM / VLM 可用性判定与构建（agent 层唯一入口）。

get_llm_for_settings / get_vision_for_settings 集中做四级检查
（enabled → API key → provider 已注册 → SDK 可导入），全部通过才实例化客户端；
任一步不满足返回 None，使 agent 层完全不感知 provider 细节。

实现要点：
- 每个 `return None` 都带**可区分原因**的 warning（否则多类故障静默收敛成同一个 None，
  唯一线索是报告里 llm_calls=0）；只打印环境变量**名**，绝不打印值。
- provider 未注册先查注册表再构建（不用 KeyError 做控制流，避免吞掉内部 KeyError）。
- timeout_s / max_tokens / max_retries / max_image_bytes 全部由配置透传。
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
from selfheal.llm.resilience import wrap_llm_client, wrap_vision_client

logger = logging.getLogger(__name__)


def get_llm_for_settings(settings: Settings, *, stats: dict | None = None) -> LLMClient | None:
    """按配置构建可用 LLM 客户端；不可用返回 None（调用方优雅降级）。

    不可用时记 warning 说明**具体原因**（enabled 关闭 / 缺 key / provider 未注册 / SDK 不可用）。

    D25：底层 SDK 的 `max_retries` 固定传 0——重试由自研韧性层接管（只重试瞬态、带退避）；
    否则 SDK 自带重试与本层叠加会变成 3×3=9 次付费调用且彼此不可见。
    `stats` 传 `reporter.stats` 时，attempts / retries / circuit_open 会计入成本与看板。
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
        raw = get_llm(
            llm_cfg.provider,
            api_key=api_key,
            model=llm_cfg.model,
            base_url=llm_cfg.base_url,
            temperature=llm_cfg.temperature,
            timeout_s=llm_cfg.timeout_s,  # 超时/上限/重试均可配置
            max_tokens=llm_cfg.max_tokens,
            max_tokens_field=llm_cfg.max_tokens_field,  # 输出上限字段名可按模型族切换
            max_retries=0,  # D25：SDK 重试关闭，统一由 resilience 层接管（防双层重试）
            provider=llm_cfg.provider,  # 异常信息带 provider，便于定位 key/端点错配
        )
        return wrap_llm_client(raw, llm_cfg, stats=stats)
    except UnavailableError as exc:  # SDK 缺失 / 不可用
        logger.warning("LLM 不可用：%s", exc)
        return None


def get_vision_for_settings(
    settings: Settings, *, stats: dict | None = None
) -> VisionClient | None:
    """按配置构建可用 VLM 客户端；不可用返回 None（视觉策略被跳过）。

    D25：与 LLM 侧同款——SDK 重试关闭、由韧性层按配置做退避与熔断（VLM 独立实例）。
    """
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
        raw = get_vision(
            vcfg.provider,
            api_key=api_key,
            model=vcfg.model,
            base_url=vcfg.base_url,
            timeout_s=vcfg.timeout_s,  # plus 级模型响应慢，超时/上限可配置
            max_tokens=vcfg.max_tokens,
            max_retries=0,  # D25：SDK 重试关闭，统一由 resilience 层接管
            max_image_bytes=vcfg.max_image_bytes,  # 图片体积护栏
            provider=vcfg.provider,
        )
        return wrap_vision_client(raw, vcfg, stats=stats)
    except UnavailableError as exc:  # SDK 缺失 / 不可用
        logger.warning("VLM 不可用：%s", exc)
        return None
