"""单元测试：OpenAI 兼容客户端（不触网，openai 缺失时验证降级）。"""

import pytest

from selfheal.llm._exceptions import UnavailableError
from selfheal.llm.base import ChatMessage
from selfheal.llm.openai_client import OpenAICompatibleLLM, get_api_key

pytestmark = pytest.mark.unit


def test_get_api_key_blank_is_none(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "  ")
    assert get_api_key("OPENAI_API_KEY") is None


def test_chat_raises_unavailable_when_openai_missing(monkeypatch):
    import sys

    # 强制 openai 导入失败（即使环境已装 openai，行为也等价于缺失）
    monkeypatch.setitem(sys.modules, "openai", None)
    client = OpenAICompatibleLLM(api_key="sk-test", model="gpt-4o-mini")
    with pytest.raises(UnavailableError):
        client.chat([ChatMessage("user", "hi")])


def _inject_fake_openai(monkeypatch, create_fn):
    """注入一个 create() 行为可定制的 fake openai，验证异常归一化（不触网）。

    结构对齐真实 SDK：client.chat.completions.create(...) 中
    .completions 是带 .create 方法的对象。
    """
    import sys
    import types

    class _FakeOpenAI:
        def __init__(self, **kw):
            completions = types.SimpleNamespace(create=create_fn)
            self.chat = types.SimpleNamespace(completions=completions)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))


def test_chat_normalizes_sdk_exception(monkeypatch):
    """#11：SDK 异常（网络/鉴权）应归一化为 UnavailableError，而非原生异常穿透。"""

    def _create(**kw):
        raise ConnectionError("network down")

    _inject_fake_openai(monkeypatch, _create)
    client = OpenAICompatibleLLM(api_key="sk-test", model="m")
    with pytest.raises(UnavailableError) as excinfo:
        client.chat([ChatMessage("user", "hi")])
    assert "ConnectionError" in str(excinfo.value)


def test_chat_raises_on_empty_choices(monkeypatch):
    """#11：API 返回空 choices 抛 UnavailableError 而非 IndexError。"""
    import types

    def _create(**kw):
        return types.SimpleNamespace(choices=[])

    _inject_fake_openai(monkeypatch, _create)
    client = OpenAICompatibleLLM(api_key="sk-test", model="m")
    with pytest.raises(UnavailableError):
        client.chat([ChatMessage("user", "hi")])


def test_factory_registered():
    from selfheal.llm.registry import get_llm

    client = get_llm("openai", api_key="sk-test", model="m")
    assert isinstance(client, OpenAICompatibleLLM)


# --- M1（2026-10-08 审查）：异常分级 + 显式重试 ---


def test_client_passes_explicit_max_retries(monkeypatch):
    """M1：重试次数显式传给 SDK，不再静默继承默认值。"""
    captured = {}

    class _FakeOpenAI:
        def __init__(self, **kw):
            captured.update(kw)

    import sys
    import types

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))
    OpenAICompatibleLLM(api_key="sk-test", model="m", max_retries=0, timeout_s=9.0)._ensure_client()
    assert captured["max_retries"] == 0
    assert captured["timeout"] == 9.0


def test_auth_error_is_fatal_and_reports_where(monkeypatch):
    """M1：401 归为 Fatal（重试无用），信息带 provider/model/端点主机且不含密钥。"""
    from selfheal.llm._exceptions import FatalUnavailableError

    class AuthenticationError(Exception):
        status_code = 401

    def _create(**kw):
        raise AuthenticationError("bad key")

    _inject_fake_openai(monkeypatch, _create)
    client = OpenAICompatibleLLM(
        api_key="sk-secret", model="m", base_url="https://api.example.com/v1", provider="openai"
    )
    with pytest.raises(FatalUnavailableError) as excinfo:
        client.chat([ChatMessage("user", "hi")])
    message = str(excinfo.value)
    assert "api.example.com" in message and "model='m'" in message
    assert "sk-secret" not in message


def test_timeout_error_is_transient(monkeypatch):
    from selfheal.llm._exceptions import TransientUnavailableError

    class APITimeoutError(Exception):
        status_code = 408

    _inject_fake_openai(monkeypatch, lambda **kw: (_ for _ in ()).throw(APITimeoutError("slow")))
    client = OpenAICompatibleLLM(api_key="sk-test", model="m")
    with pytest.raises(TransientUnavailableError):
        client.chat([ChatMessage("user", "hi")])


def test_unknown_error_stays_base_class(monkeypatch):
    """未归类异常仍抛 UnavailableError 基类：既有 `except UnavailableError` 契约不破。"""
    _inject_fake_openai(monkeypatch, lambda **kw: (_ for _ in ()).throw(KeyError("odd")))
    client = OpenAICompatibleLLM(api_key="sk-test", model="m")
    with pytest.raises(UnavailableError):
        client.chat([ChatMessage("user", "hi")])
