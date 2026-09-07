"""LLM 层异常定义（分级）。

不可用故障按**可恢复性**分级（2026-10-08 审查 M1）：此前全层只有一个 UnavailableError，
401（key 失效/无权限）与 429/超时（瞬态）同级，调用方无法判断"该重试还是该报警"。
现在：

- UnavailableError：基类（SDK 缺失 / 返回为空 / 未归类），既有 `except UnavailableError`
  的降级契约**完全不变**（零回归）。
- TransientUnavailableError：瞬态（限流 429 / 超时 / 连接失败 / 5xx）——可重试。
- FatalUnavailableError：不可恢复（鉴权 401/403、参数错、模型不存在）——重试无用，
  且几乎总是"配置写错"（如 key 与 base_url 不属于同一平台），需要立刻可见而非静默降级。

分类不 import openai（SDK 是可选依赖，决策 D7），按 `status_code` 属性优先、异常类名兜底。
独立成文件以避免 openai_client / factory / 调用方之间循环 import。
"""

from __future__ import annotations

import contextlib
from urllib.parse import urlsplit


class UnavailableError(Exception):
    """模型能力不可用（基类）。上层（orchestrator / 诊断 / 策略）捕获后优雅降级。"""


class TransientUnavailableError(UnavailableError):
    """瞬态不可用（限流 / 超时 / 连接失败 / 服务端 5xx）：可重试。"""


class FatalUnavailableError(UnavailableError):
    """不可恢复（鉴权 / 权限 / 参数 / 模型不存在）：重试无用，需人工核对配置与密钥。"""


# 无 status_code 时的类名兜底集合（openai SDK 异常类名）
_FATAL_NAMES = frozenset(
    {
        "AuthenticationError",
        "PermissionDeniedError",
        "NotFoundError",
        "BadRequestError",
        "UnprocessableEntityError",
    }
)
_TRANSIENT_NAMES = frozenset(
    {
        "RateLimitError",
        "APITimeoutError",
        "APIConnectionError",
        "InternalServerError",
        "ConflictError",
    }
)


def classify_sdk_error(
    exc: BaseException,
    *,
    provider: str,
    model: str,
    base_url: str | None = None,
) -> UnavailableError:
    """把 SDK 原始异常归一化为分级 UnavailableError。

    错误信息带上 provider / model / 端点主机（**不含路径与密钥**）：审查实测表明
    "key 与端点不属于同一平台"这种高频配置故障此前只表现为一句
    `模型调用失败: AuthenticationError`，无法定位——补上归属信息即可自查。

    Args:
        exc: SDK 抛出的原始异常。
        provider: 配置中的 provider 名（如 openai）。
        model: 配置中的模型名。
        base_url: 端点 URL（仅取其主机名用于诊断）。

    Returns:
        按可恢复性分级后的 UnavailableError 实例（由调用方 raise ... from exc）。
    """
    name = type(exc).__name__
    where = f"provider={provider!r} model={model!r}"
    if base_url:
        with contextlib.suppress(ValueError):
            where = f"{where} endpoint={urlsplit(base_url).netloc!r}"

    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = None

    if status in (401, 403, 404, 400, 405, 422) or name in _FATAL_NAMES:
        hint = "（配置或密钥类故障，重试无用：请核对 api_key_env 的密钥与 base_url/model 是否属同一平台）"
        return FatalUnavailableError(f"模型调用失败: {name} {where}{hint}")
    if (
        status in (408, 409, 429)
        or (status is not None and status >= 500)
        or name in _TRANSIENT_NAMES
    ):
        return TransientUnavailableError(f"模型调用失败: {name} {where}（瞬态故障，可重试）")
    return UnavailableError(f"模型调用失败: {name} {where}")
