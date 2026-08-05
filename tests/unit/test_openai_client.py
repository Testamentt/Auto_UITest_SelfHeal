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
