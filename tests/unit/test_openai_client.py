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


def test_factory_registered():
    from selfheal.llm.registry import get_llm

    client = get_llm("openai", api_key="sk-test", model="m")
    assert isinstance(client, OpenAICompatibleLLM)
