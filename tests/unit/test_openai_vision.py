"""单元测试：OpenAI 兼容视觉客户端（不触网；审查 M15 补齐 VLM 全路径覆盖）。

覆盖：base64 data-url 组装、SDK 异常归一化与分级、空 choices / content=None、
SDK 缺失降级、图片体积护栏（Pillow 可用时压缩 / 不可用时记 warning 原样发送）、close 幂等。
"""

import base64
import types

import pytest

from selfheal.llm._exceptions import (
    FatalUnavailableError,
    TransientUnavailableError,
    UnavailableError,
)
from selfheal.llm.openai_vision import OpenAICompatibleVLM, shrink_image

pytestmark = pytest.mark.unit


def _inject_fake_openai(monkeypatch, create_fn, ctor_recorder=None):
    """注入 fake openai SDK（结构对齐真实 SDK：client.chat.completions.create）。"""

    class _FakeOpenAI:
        def __init__(self, **kw):
            if ctor_recorder is not None:
                ctor_recorder.update(kw)
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create_fn))

    monkeypatch.setitem(
        __import__("sys").modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI)
    )


def _reply(content="ok"):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
    )


def test_analyze_image_builds_data_url_and_returns_text(monkeypatch):
    seen = {}

    def _create(**kw):
        seen.update(kw)
        return _reply("视觉分析结果")

    _inject_fake_openai(monkeypatch, _create)
    client = OpenAICompatibleVLM(
        api_key="sk-test", model="qwen-vl", base_url="https://example.test/v1"
    )
    assert client.analyze_image(b"PNGDATA", "看图") == "视觉分析结果"

    content = seen["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "看图"}
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == b"PNGDATA"
    assert seen["max_tokens"] == 500  # 默认上限透传


def test_max_retries_is_explicit_in_client_construction(monkeypatch):
    """M1：重试次数显式传给 SDK，不再静默继承默认值。"""
    captured = {}
    _inject_fake_openai(monkeypatch, lambda **kw: _reply(), ctor_recorder=captured)
    client = OpenAICompatibleVLM(api_key="sk-test", model="m", max_retries=0, timeout_s=7.5)
    client.analyze_image(b"x", "p")
    assert captured["max_retries"] == 0
    assert captured["timeout"] == 7.5


def test_auth_error_is_fatal_and_names_endpoint(monkeypatch):
    """M1：401 归一化为 FatalUnavailableError，且错误信息带 provider/model/端点主机。"""

    class AuthenticationError(Exception):
        status_code = 401

    def _create(**kw):
        raise AuthenticationError("bad key")

    _inject_fake_openai(monkeypatch, _create)
    client = OpenAICompatibleVLM(
        api_key="sk-test", model="m", base_url="https://api.example.com/v1", provider="openai"
    )
    with pytest.raises(FatalUnavailableError) as excinfo:
        client.analyze_image(b"x", "p")
    message = str(excinfo.value)
    assert "AuthenticationError" in message
    assert "api.example.com" in message
    assert "sk-test" not in message  # 绝不回显密钥


def test_rate_limit_error_is_transient(monkeypatch):
    """M1：429 归一化为可重试的 TransientUnavailableError。"""

    class RateLimitError(Exception):
        status_code = 429

    _inject_fake_openai(
        monkeypatch, lambda **kw: (_ for _ in ()).throw(RateLimitError("slow down"))
    )
    client = OpenAICompatibleVLM(api_key="sk-test", model="m")
    with pytest.raises(TransientUnavailableError):
        client.analyze_image(b"x", "p")


def test_empty_choices_and_none_content(monkeypatch):
    _inject_fake_openai(monkeypatch, lambda **kw: types.SimpleNamespace(choices=[]))
    client = OpenAICompatibleVLM(api_key="sk-test", model="m")
    with pytest.raises(UnavailableError):
        client.analyze_image(b"x", "p")

    _inject_fake_openai(monkeypatch, lambda **kw: _reply(None))
    with pytest.raises(UnavailableError):
        client.analyze_image(b"x", "p")


def test_missing_sdk_degrades(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "openai", None)
    client = OpenAICompatibleVLM(api_key="sk-test", model="m")
    with pytest.raises(UnavailableError):
        client.analyze_image(b"x", "p")


def test_close_is_idempotent(monkeypatch):
    closed = {"n": 0}

    class _FakeClient:
        def close(self):
            closed["n"] += 1

    _inject_fake_openai(monkeypatch, lambda **kw: _reply())
    client = OpenAICompatibleVLM(api_key="sk-test", model="m")
    client._client = _FakeClient()
    client.close()
    client.close()
    assert closed["n"] == 1


# --- H5：图片体积护栏 ---


def _png(size=(700, 700)) -> bytes:
    """生成噪声 PNG（纯色图会被 PNG 压得极小，噪声才能模拟真实长截图体积）。"""
    from io import BytesIO

    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(0)
    array = rng.integers(0, 256, size=(size[1], size[0], 3), dtype=np.uint8)
    buffer = BytesIO()
    Image.fromarray(array, "RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def test_shrink_image_within_limit_keeps_png():
    small = b"tiny"
    assert shrink_image(small, 1024) == (small, "image/png")


def test_shrink_image_compresses_oversized_png():
    """H5：超限 PNG 被压成 JPEG 且落在上限内（Pillow 可用时）。"""
    pytest.importorskip("PIL")
    image = _png()
    assert len(image) > 20_000
    shrunk = shrink_image(image, 20_000)
    assert shrunk is not None
    data, media_type = shrunk
    assert media_type == "image/jpeg"
    assert len(data) <= 20_000
    assert data[:2] == b"\xff\xd8"  # JPEG SOI


def test_oversized_without_pillow_warns_once_and_sends_original(monkeypatch, caplog):
    """H5：Pillow 不可用时记一次 warning 并按原图发送（不静默、不崩）。"""
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "PIL" or name.startswith("PIL."):
            raise ImportError("no PIL")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    client = OpenAICompatibleVLM(api_key="k", model="m", max_image_bytes=4)
    with caplog.at_level("WARNING"):
        prepared, media_type = client._prepare_image(b"way-too-big")
        client._prepare_image(b"way-too-big")
    assert prepared == b"way-too-big"
    assert media_type == "image/png"
    assert sum("无法压缩" in record.getMessage() for record in caplog.records) == 1
