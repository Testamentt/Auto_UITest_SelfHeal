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


# --- L8：未知调用期参数显式拒绝（不再静默丢弃） ---


def test_chat_rejects_unknown_kwargs(monkeypatch):
    """L8：`chat(..., 未知参数)` 抛 TypeError（编程错误语义），不静默丢弃也不误报不可用。"""
    called = {"n": 0}

    def _create(**kw):
        called["n"] += 1
        raise AssertionError("参数校验失败时不得发起 SDK 调用")

    _inject_fake_openai(monkeypatch, _create)
    client = OpenAICompatibleLLM(api_key="sk-test", model="m")
    with pytest.raises(TypeError) as excinfo:
        client.chat([ChatMessage("user", "hi")], max_new_tokens=10)
    message = str(excinfo.value)
    assert "max_new_tokens" in message  # 报出被拒的键名，便于定位
    assert "OpenAICompatibleLLM.chat" in message
    assert called["n"] == 0  # 拒绝发生在建连/请求之前

    # 未支持参数逐个报出（含多个键时按键名排序，报错稳定可断言）
    with pytest.raises(TypeError) as excinfo2:
        client.chat([ChatMessage("user", "hi")], timeout=1, stop=["x"])
    assert "stop" in str(excinfo2.value) and "timeout" in str(excinfo2.value)


def test_chat_without_kwargs_still_works(monkeypatch):
    """L8：不传 kwargs 的正常路径不受影响（拒绝逻辑只在有未支持键时触发）。"""
    _inject_fake_openai(monkeypatch, lambda **kw: _reply_dict("ok"))
    client = OpenAICompatibleLLM(api_key="sk-test", model="m")
    assert client.chat([ChatMessage("user", "hi")]) == "ok"


# --- L9：输出上限字段名（max_tokens / max_completion_tokens 互斥 + temperature 兼容性） ---


def _reply_dict(content="ok"):
    import types

    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))]
    )


def test_wire_payload_default_uses_max_tokens_with_temperature(monkeypatch):
    """L9：默认字段族只发 `max_tokens`（与 `max_completion_tokens` 互斥），temperature 照发。"""
    seen: dict = {}

    def _create(**kw):
        seen.update(kw)
        return _reply_dict()

    _inject_fake_openai(monkeypatch, _create)
    client = OpenAICompatibleLLM(api_key="sk-test", model="m", temperature=0.0, max_tokens=1234)
    client.chat([ChatMessage("user", "hi")])
    assert seen["max_tokens"] == 1234
    assert "max_completion_tokens" not in seen  # 互斥：绝不两个字段同时出现
    assert seen["temperature"] == 0.0
    assert seen["model"] == "m"
    assert seen["messages"] == [{"role": "user", "content": "hi"}]


def test_wire_payload_max_completion_tokens_omits_temperature(monkeypatch):
    """L9：切到新模型族字段时**省略** temperature（该族只接受默认温度，发 0 会 400）。"""
    seen: dict = {}

    def _create(**kw):
        seen.update(kw)
        return _reply_dict()

    _inject_fake_openai(monkeypatch, _create)
    client = OpenAICompatibleLLM(
        api_key="sk-test",
        model="m",
        temperature=0.0,
        max_tokens=999,
        max_tokens_field="max_completion_tokens",
    )
    client.chat([ChatMessage("user", "hi")])
    assert seen["max_completion_tokens"] == 999
    assert "max_tokens" not in seen  # 互斥：旧字段不出现
    assert "temperature" not in seen  # 不兼容自定义温度 → 省略而非发送非法值


def test_max_completion_tokens_warns_once_when_temperature_ignored(monkeypatch, caplog):
    """L9：配置的 temperature 被省略时记一次 warning（不静默丢弃用户意图）。"""
    _inject_fake_openai(monkeypatch, lambda **kw: _reply_dict())
    client = OpenAICompatibleLLM(
        api_key="sk-test", model="m", temperature=0.3, max_tokens_field="max_completion_tokens"
    )
    with caplog.at_level("WARNING", logger="selfheal.llm.openai_client"):
        client.chat([ChatMessage("user", "hi")])
        client.chat([ChatMessage("user", "hi")])
    hits = [r for r in caplog.records if "不支持自定义 temperature" in r.getMessage()]
    assert len(hits) == 1  # 每次调用都刷屏会淹没有效告警 → 只记一次


def test_max_completion_tokens_no_warning_when_temperature_is_default(monkeypatch, caplog):
    """L9：temperature 本就等于该族默认值 1.0 时无需告警（没有"被忽略的意图"）。"""
    _inject_fake_openai(monkeypatch, lambda **kw: _reply_dict())
    client = OpenAICompatibleLLM(
        api_key="sk-test", model="m", temperature=1.0, max_tokens_field="max_completion_tokens"
    )
    with caplog.at_level("WARNING", logger="selfheal.llm.openai_client"):
        client.chat([ChatMessage("user", "hi")])
    assert not [r for r in caplog.records if "temperature" in r.getMessage()]


def test_factory_forwards_max_tokens_field(monkeypatch):
    """L9：provider 工厂透传字段名（配置 → 构造函数不漏字段）；未知字段名报 TypeError。"""
    from selfheal.llm.registry import get_llm

    client = get_llm(
        "openai", api_key="sk-test", model="m", max_tokens_field="max_completion_tokens"
    )
    assert client._max_tokens_field == "max_completion_tokens"
    with pytest.raises(TypeError):
        get_llm("openai", api_key="sk-test", model="m", max_new_tokens=1)
