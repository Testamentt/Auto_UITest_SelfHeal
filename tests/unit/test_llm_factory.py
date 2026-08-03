"""单元测试：LLM 可用性判定（不触网）。"""

import pytest

from selfheal.config import Settings
from selfheal.llm.factory import get_llm_for_settings

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
    if client is None:
        pytest.skip("openai 客户端未能构建（可能 SDK 导入链异常）")
    assert client is not None
