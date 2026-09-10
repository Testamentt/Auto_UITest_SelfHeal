"""OpenAI 兼容视觉模型（VLM）客户端。

qwen3-vl-flash 等多模态模型走 OpenAI 兼容端点；analyze_image 把 base64 图片经
chat completions（content 数组含 image_url）发给模型，返回文本分析。
openai SDK 惰性导入，缺失时抛 UnavailableError，由上层优雅降级（决策 D7）。

2026-10-08 审查修复：
- H5：新增 `max_image_bytes` 护栏——`full_page` 长截图可达数 MB，base64 再膨胀 1.33 倍，
  超服务端请求体上限即 400 → 视觉策略表现为"永不生效"。Pillow 可用时自动降质/缩放转 JPEG；
  Pillow 缺失时记 warning 后按原图发送（不引入硬依赖，best-effort）。
- M1：异常分级 + `max_retries` 显式设置 + 错误信息带 provider/model/端点主机。
"""

from __future__ import annotations

import base64
import contextlib
import logging
from typing import Any

from selfheal.llm._exceptions import UnavailableError, classify_sdk_error
from selfheal.llm.base import VisionClient
from selfheal.llm.registry import register_vision

logger = logging.getLogger(__name__)

# JPEG 质量阶梯与缩放阶梯（H5：先降质量、再降分辨率，取第一个达标的组合）
_QUALITY_LADDER = (85, 70, 55, 40)
_SCALE_LADDER = (1.0, 0.75, 0.5, 0.35)


def shrink_image(image: bytes, max_bytes: int) -> tuple[bytes, str] | None:
    """把图片压到 `max_bytes` 以内（JPEG）。

    返回 `(字节, media_type)`；Pillow 不可用或图片无法解码时返回 None（调用方按原图发送）。

    Args:
        image: 原始图片字节（通常为 Playwright 的 PNG 截图）。
        max_bytes: 目标上限（字节）。

    Returns:
        压缩后的 `(data, "image/jpeg")`，或 None（无法压缩）。
    """
    if max_bytes <= 0 or len(image) <= max_bytes:
        return image, "image/png"
    try:
        from io import BytesIO

        from PIL import Image  # 惰性导入：Pillow 为可选依赖
    except ImportError:
        return None
    try:
        with Image.open(BytesIO(image)) as img:
            source = img.convert("RGB") if img.mode not in ("RGB", "L") else img
            best: bytes | None = None
            for scale in _SCALE_LADDER:
                target = source
                if scale != 1.0:
                    size = (max(1, int(source.width * scale)), max(1, int(source.height * scale)))
                    target = source.resize(size)
                for quality in _QUALITY_LADDER:
                    buffer = BytesIO()
                    target.save(buffer, format="JPEG", quality=quality, optimize=True)
                    data = buffer.getvalue()
                    if best is None or len(data) < len(best):
                        best = data
                    if len(data) <= max_bytes:
                        return data, "image/jpeg"
            return best, "image/jpeg"
    except Exception:  # noqa: BLE001 - 解码/编码失败：按原图发送，由上层降级
        logger.warning("截图压缩失败（按原图发送）", exc_info=True)
        return None


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
        max_retries: int = 2,
        max_image_bytes: int = 1_500_000,
        provider: str = "openai",
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._max_image_bytes = max_image_bytes
        self._provider = provider
        self._client: Any | None = None
        self._oversize_warned = False

    def _ensure_client(self) -> Any:
        """惰性创建底层 OpenAI client；SDK 缺失时抛 UnavailableError。"""
        if self._client is None:
            try:
                from openai import OpenAI  # 惰性导入：避免顶层强依赖
            except ImportError as exc:
                raise UnavailableError("未安装 openai 包，无法使用 VLM 能力") from exc
            kwargs: dict[str, Any] = {
                "api_key": self._api_key,
                "timeout": self._timeout_s,
                "max_retries": self._max_retries,  # M1：显式重试，不再隐式继承 SDK 默认
            }
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def _prepare_image(self, image: bytes) -> tuple[bytes, str]:
        """按护栏压缩截图（H5）；无法压缩且超限时记一次 warning 后原样返回。"""
        if len(image) <= self._max_image_bytes:
            return image, "image/png"
        shrunk = shrink_image(image, self._max_image_bytes)
        if shrunk is None:
            if not self._oversize_warned:
                self._oversize_warned = True
                logger.warning(
                    "截图 %d 字节超过 max_image_bytes=%d 且无法压缩（未安装 Pillow？），"
                    "按原图发送，服务端可能拒绝",
                    len(image),
                    self._max_image_bytes,
                )
            return image, "image/png"
        logger.debug("截图压缩：%d → %d 字节（%s）", len(image), len(shrunk[0]), shrunk[1])
        return shrunk

    def analyze_image(self, image: bytes, prompt: str, **kwargs: Any) -> str:
        """发送截图 + 提示词，返回模型文本分析。

        #11 降级契约收敛：SDK 异常统一转抛 UnavailableError（from exc），调用方捕获即可降级。
        M1：转抛分级子类，信息含 provider/model/端点主机。
        """
        client = self._ensure_client()
        prepared, media_type = self._prepare_image(image)
        b64 = base64.b64encode(prepared).decode("utf-8")
        try:
            resp = client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{media_type};base64,{b64}"},
                            },
                        ],
                    }
                ],
                max_tokens=self._max_tokens,
            )
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
        """幂等释放底层 client（V5 复核：与 OpenAICompatibleLLM 对齐——orchestrator.close()
        的 _safe_close 对 VLM 不再是静默 no-op，httpx/openai 连接池随自愈收口释放）。"""
        if self._client is not None:
            # 释放失败不掩盖业务异常（suppress 等价于 try-except-pass）
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None


@register_vision("openai")
def _openai_vision_factory(api_key: str, model: str, **kwargs: Any) -> OpenAICompatibleVLM:
    """openai provider 的视觉客户端注册工厂。"""
    return OpenAICompatibleVLM(api_key, model, **kwargs)
