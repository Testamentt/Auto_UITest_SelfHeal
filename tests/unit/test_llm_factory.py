"""单元测试：LLM 可用性判定（不触网）。

2026-10-08 审查修复：
- M15：去掉 `test_ready_returns_client` 里的 `pytest.skip` 分支——原来的写法让断言恒真，
  工厂回归（例如错误地把 UnavailableError 也吞成 None）会以 skipped 收场而 CI 依然绿。
- H4：不可用的四类原因各自记 warning（此前统一静默返回 None）。
"""

import logging

import pytest

from selfheal.config import Settings
from selfheal.llm.factory import get_llm_for_settings, get_vision_for_settings

pytestmark = pytest.mark.unit


def _settings(**llm_overrides):
    settings = Settings()
    settings.llm = type(settings.llm)(**{**settings.llm.model_dump(), **llm_overrides})
    return settings


def test_missing_key_returns_none(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert get_llm_for_settings(_settings()) is None


def test_disabled_returns_none(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert get_llm_for_settings(_settings(enabled=False)) is None


def test_unregistered_provider_returns_none(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert get_llm_for_settings(_settings(provider="nope")) is None


def test_ready_returns_client(monkeypatch):
    """key 存在且 provider 已注册（openai 未安装时仍可构建实例，惰性导入不触发）。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = get_llm_for_settings(_settings())
    assert client is not None, "工厂在 key/provider 就绪时必须返回客户端，不能静默降级"


def test_config_reaches_client(monkeypatch):
    """H3：timeout_s / max_tokens / max_retries 由配置透传到客户端（此前硬编码且不可配）。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    client = get_llm_for_settings(_settings(timeout_s=42.5, max_tokens=1234, max_retries=0))
    assert (client._timeout_s, client._max_tokens, client._max_retries) == (42.5, 1234, 0)


@pytest.mark.parametrize(
    ("payload", "needle"),
    [
        ({"enabled": False}, "llm.enabled=false"),
        ({"api_key_env": "SELFHEAL_ABSENT_KEY"}, "SELFHEAL_ABSENT_KEY"),
        ({"provider": "nope"}, "未注册"),
    ],
)
def test_unavailable_reasons_are_logged(monkeypatch, caplog, payload, needle):
    """H4：每种不可用原因都要在日志里可区分（此前统一静默 None）。"""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SELFHEAL_ABSENT_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with caplog.at_level(logging.WARNING):
        assert get_llm_for_settings(_settings(**payload)) is None
    assert any(needle in record.getMessage() for record in caplog.records)


def test_vision_unavailable_reason_is_logged(monkeypatch, caplog):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with caplog.at_level(logging.WARNING):
        assert get_vision_for_settings(Settings()) is None
    assert any("DASHSCOPE_API_KEY" in record.getMessage() for record in caplog.records)


def test_vision_config_reaches_client(monkeypatch):
    """H5/M1：图片护栏与重试次数同样由配置透传。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    settings = Settings()
    settings.vision.max_image_bytes = 4096
    settings.vision.max_retries = 1
    client = get_vision_for_settings(settings)
    assert (client._max_image_bytes, client._max_retries) == (4096, 1)
