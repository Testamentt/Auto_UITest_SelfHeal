"""单元测试：LLM/VLM 韧性层（T24：指数退避重试 + 三态熔断）。

覆盖口径：
- 退避计算（指数 / 上限 / 抖动边界 / 非法参数）；
- 重试语义（仅瞬态重试、Fatal 与其它异常不重试、耗尽抛最后一次、回调与 sleep 序列）；
- 熔断状态机（打开 / 短路 / 冷却半开 / 半开单探针 / 探针成功与失败 / reset）；
- 包装器（chat / complete / analyze_image、stats 计数、close 委托、禁用时零开销）；
- 工厂接线（SDK 重试固定为 0、包装类型、stats 注入）；
- 配置校验（默认值、越界、未知键拒绝）；
- 成本口径（按 attempts 计费，缺失时回退 calls）。

所有时间与 sleep 均注入 fake，**不真睡**；不触碰网络与浏览器。
"""

from __future__ import annotations

import random

import pytest
from pydantic import ValidationError

from selfheal.config import Settings
from selfheal.llm._exceptions import (
    CircuitOpenError,
    FatalUnavailableError,
    TransientUnavailableError,
    UnavailableError,
)
from selfheal.llm.base import ChatMessage, LLMClient, VisionClient
from selfheal.llm.factory import get_llm_for_settings, get_vision_for_settings
from selfheal.llm.resilience import (
    CircuitBreaker,
    CircuitState,
    ResilientLLMClient,
    ResilientVisionClient,
    RetryPolicy,
    call_with_retry,
    wrap_llm_client,
    wrap_vision_client,
)
from selfheal.reporting.hooks import HealingReporter

pytestmark = pytest.mark.unit


def _transient(msg: str = "429") -> TransientUnavailableError:
    return TransientUnavailableError(msg)


class _ScriptedLLM(LLMClient):
    """按脚本返回/抛异常的 LLM 假客户端（脚本耗尽后返回 "ok"）。"""

    def __init__(self, script: list[BaseException] | None = None, reply: str = "reply"):
        self.script = list(script or [])
        self.reply = reply
        self.calls = 0

    def chat(self, messages, **kwargs):  # noqa: ANN001 - 测试假实现
        self.calls += 1
        if self.script:
            raise self.script.pop(0)
        return self.reply


class _ScriptedVision(VisionClient):
    """按脚本返回/抛异常的 VLM 假客户端。"""

    def __init__(self, script: list[BaseException] | None = None, reply: str = "vision-reply"):
        self.script = list(script or [])
        self.reply = reply
        self.calls = 0

    def analyze_image(self, image, prompt, **kwargs):  # noqa: ANN001 - 测试假实现
        self.calls += 1
        if self.script:
            raise self.script.pop(0)
        return self.reply


def _no_sleep(_: float) -> None:
    """测试用 sleep：不真睡。"""


# ---------------------------------------------------------------------------
# RetryPolicy / call_with_retry
# ---------------------------------------------------------------------------


def test_delay_is_exponential_without_jitter():
    policy = RetryPolicy(max_retries=4, base_delay_s=0.5, max_delay_s=100.0, jitter=False)
    assert [policy.delay_for(i) for i in range(4)] == [0.5, 1.0, 2.0, 4.0]


def test_delay_is_capped_by_max_delay():
    policy = RetryPolicy(max_retries=6, base_delay_s=1.0, max_delay_s=3.0, jitter=False)
    assert policy.delay_for(5) == 3.0
    assert policy.delay_for(2) == 3.0  # min(3, 4)


def test_delay_jitter_stays_within_zero_and_cap():
    rng = random.Random(20261110)
    policy = RetryPolicy(max_retries=4, base_delay_s=1.0, max_delay_s=8.0, jitter=True, rng=rng)
    for attempt in range(4):
        upper = min(8.0, 1.0 * 2**attempt)
        for _ in range(20):
            delay = policy.delay_for(attempt)
            assert 0.0 <= delay <= upper


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_retries": -1},
        {"base_delay_s": -0.1},
        {"base_delay_s": 2.0, "max_delay_s": 1.0},
    ],
)
def test_retry_policy_rejects_invalid_values(kwargs):
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)


def test_call_with_retry_succeeds_after_transients():
    calls = []
    sleeps = []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise _transient()
        return "ok"

    result = call_with_retry(
        fn,
        RetryPolicy(max_retries=3, base_delay_s=0.5, max_delay_s=8.0, jitter=False),
        sleep=sleeps.append,
    )
    assert result == "ok"
    assert len(calls) == 3
    assert sleeps == [0.5, 1.0]


def test_call_with_retry_raises_last_transient_when_exhausted():
    def fn():
        raise _transient("still down")

    with pytest.raises(TransientUnavailableError, match="still down"):
        call_with_retry(fn, RetryPolicy(max_retries=2, jitter=False), sleep=_no_sleep)


def test_call_with_retry_does_not_retry_fatal():
    calls = []

    def fn():
        calls.append(1)
        raise FatalUnavailableError("401")

    with pytest.raises(FatalUnavailableError):
        call_with_retry(fn, RetryPolicy(max_retries=5, jitter=False), sleep=_no_sleep)
    assert len(calls) == 1


def test_call_with_retry_does_not_retry_plain_unavailable():
    calls = []

    def fn():
        calls.append(1)
        raise UnavailableError("unclassified")

    with pytest.raises(UnavailableError):
        call_with_retry(fn, RetryPolicy(max_retries=3, jitter=False), sleep=_no_sleep)
    assert len(calls) == 1


def test_call_with_retry_does_not_retry_programming_errors():
    calls = []

    def fn():
        calls.append(1)
        raise TypeError("bad kwargs")

    with pytest.raises(TypeError):
        call_with_retry(fn, RetryPolicy(max_retries=3, jitter=False), sleep=_no_sleep)
    assert len(calls) == 1


def test_call_with_retry_zero_retries_calls_once():
    calls = []

    def fn():
        calls.append(1)
        raise _transient()

    with pytest.raises(TransientUnavailableError):
        call_with_retry(fn, RetryPolicy(max_retries=0, jitter=False), sleep=_no_sleep)
    assert len(calls) == 1


def test_call_with_retry_invokes_on_retry_callback():
    events = []

    def fn():
        raise _transient("boom")

    def on_retry(retry_no, delay, exc):
        events.append((retry_no, delay, type(exc).__name__))

    with pytest.raises(TransientUnavailableError):
        call_with_retry(
            fn,
            RetryPolicy(max_retries=2, base_delay_s=1.0, max_delay_s=8.0, jitter=False),
            sleep=_no_sleep,
            on_retry=on_retry,
        )
    assert events == [
        (1, 1.0, "TransientUnavailableError"),
        (2, 2.0, "TransientUnavailableError"),
    ]


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


def _breaker(**kwargs) -> tuple[CircuitBreaker, list[float]]:
    now = [0.0]

    def clock() -> float:
        return now[0]

    params = {"failure_threshold": 2, "cooldown_s": 10.0, "clock": clock, "name": "test"}
    params.update(kwargs)
    return CircuitBreaker(**params), now


def test_breaker_opens_after_threshold_failures():
    breaker, _ = _breaker()
    assert breaker.state is CircuitState.CLOSED
    breaker.record_failure()
    assert breaker.state is CircuitState.CLOSED
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    assert breaker.failure_count == 2


def test_breaker_short_circuits_while_open():
    breaker, now = _breaker()
    breaker.record_failure()
    breaker.record_failure()
    with pytest.raises(CircuitOpenError, match="熔断中"):
        breaker.before_call()
    now[0] = 9.999
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_breaker_half_open_after_cooldown_and_closes_on_success():
    breaker, now = _breaker()
    breaker.record_failure()
    breaker.record_failure()
    now[0] = 10.0
    breaker.before_call()  # 冷却到期：放行一个半开探针
    assert breaker.state is CircuitState.HALF_OPEN
    breaker.record_success()
    assert breaker.state is CircuitState.CLOSED
    assert breaker.failure_count == 0


def test_breaker_half_open_allows_only_one_probe():
    breaker, now = _breaker()
    breaker.record_failure()
    breaker.record_failure()
    now[0] = 10.0
    breaker.before_call()
    with pytest.raises(CircuitOpenError, match="半开"):
        breaker.before_call()


def test_breaker_half_open_failure_reopens_and_restarts_cooldown():
    breaker, now = _breaker()
    breaker.record_failure()
    breaker.record_failure()
    now[0] = 10.0
    breaker.before_call()
    now[0] = 11.0
    breaker.record_failure()  # 探针失败 → 重新 OPEN，计时从 11.0 起算
    assert breaker.state is CircuitState.OPEN
    now[0] = 20.5
    with pytest.raises(CircuitOpenError):
        breaker.before_call()
    now[0] = 21.0
    breaker.before_call()
    assert breaker.state is CircuitState.HALF_OPEN


def test_breaker_reset_restores_closed_state():
    breaker, _ = _breaker()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state is CircuitState.OPEN
    breaker.reset()
    assert breaker.state is CircuitState.CLOSED
    assert breaker.failure_count == 0
    breaker.before_call()  # 不应抛


@pytest.mark.parametrize(
    "kwargs",
    [{"failure_threshold": 0}, {"cooldown_s": 0}, {"cooldown_s": -1}],
)
def test_breaker_rejects_invalid_arguments(kwargs):
    with pytest.raises(ValueError):
        CircuitBreaker(**kwargs)


def test_circuit_open_error_is_unavailable_error():
    """降级契约：熔断异常必须落在 UnavailableError 家族（既有 except 才能接管）。"""
    assert issubclass(CircuitOpenError, UnavailableError)


def test_breaker_probe_failed_reopens_and_restarts_cooldown():
    breaker, now = _breaker()
    breaker.record_failure()
    breaker.record_failure()
    now[0] = 10.0
    breaker.before_call()  # 半开探针
    breaker.probe_failed()  # 非瞬态异常收场：必须释放探针
    assert breaker.state is CircuitState.OPEN
    now[0] = 19.0  # 新冷却期从 10.0 起算
    with pytest.raises(CircuitOpenError):
        breaker.before_call()
    now[0] = 20.0
    breaker.before_call()
    assert breaker.state is CircuitState.HALF_OPEN


def test_breaker_probe_failed_is_noop_when_closed():
    breaker, _ = _breaker()
    breaker.probe_failed()  # CLOSED 下不应改变状态 / 计数
    assert breaker.state is CircuitState.CLOSED
    assert breaker.failure_count == 0


# ---------------------------------------------------------------------------
# 包装器
# ---------------------------------------------------------------------------


def _llm_client(script=None, *, stats=None, breaker=None, max_retries=2) -> ResilientLLMClient:
    return ResilientLLMClient(
        _ScriptedLLM(script),
        policy=RetryPolicy(
            max_retries=max_retries, base_delay_s=0.0, max_delay_s=0.0, jitter=False
        ),
        breaker=breaker,
        stats=stats,
        sleep=_no_sleep,
    )


def test_resilient_llm_retries_transient_and_counts_stats():
    stats: dict[str, int] = {}
    client = _llm_client([_transient(), _transient()], stats=stats)
    assert client.chat([ChatMessage("user", "hi")]) == "reply"
    assert client.inner_client.calls == 3
    assert stats == {"llm_attempts": 3, "llm_retries": 2}


def test_resilient_llm_fatal_not_retried_and_not_counted_by_breaker():
    stats: dict[str, int] = {}
    breaker, _ = _breaker(failure_threshold=1)
    client = _llm_client([FatalUnavailableError("401")], stats=stats, breaker=breaker)
    with pytest.raises(FatalUnavailableError):
        client.chat([ChatMessage("user", "hi")])
    assert client.inner_client.calls == 1
    assert breaker.state is CircuitState.CLOSED
    assert stats["llm_attempts"] == 1
    assert "llm_retries" not in stats


def test_resilient_llm_opens_breaker_then_short_circuits():
    stats: dict[str, int] = {}
    breaker, _ = _breaker(failure_threshold=1)
    client = _llm_client([_transient(), _transient(), _transient()], stats=stats, breaker=breaker)
    with pytest.raises(TransientUnavailableError):
        client.chat([ChatMessage("user", "hi")])
    assert client.inner_client.calls == 3  # 1 次逻辑调用 = 3 次真实尝试
    assert stats == {"llm_attempts": 3, "llm_retries": 2}
    # 第二次逻辑调用：熔断已开 → 不再触碰 inner
    with pytest.raises(CircuitOpenError):
        client.chat([ChatMessage("user", "hi")])
    assert client.inner_client.calls == 3
    assert stats["llm_circuit_open"] == 1


def test_resilient_llm_releases_half_open_probe_on_fatal():
    """边界：半开探针遇 Fatal/非瞬态异常必须释放探针回 OPEN，不能永久卡在 HALF_OPEN。"""
    now = [0.0]
    breaker = CircuitBreaker(failure_threshold=1, cooldown_s=10.0, clock=lambda: now[0], name="LLM")
    client = ResilientLLMClient(
        _ScriptedLLM([_transient(), FatalUnavailableError("401")]),
        policy=RetryPolicy(max_retries=0, jitter=False),
        breaker=breaker,
        sleep=_no_sleep,
    )
    with pytest.raises(TransientUnavailableError):
        client.chat([ChatMessage("user", "hi")])
    assert breaker.state is CircuitState.OPEN
    now[0] = 10.0
    with pytest.raises(FatalUnavailableError):
        client.chat([ChatMessage("user", "hi")])
    assert breaker.state is CircuitState.OPEN  # 探针失败 → 回到 OPEN（而非卡在半开）
    with pytest.raises(CircuitOpenError):  # 按新冷却期短路，而非永久 HALF_OPEN
        client.chat([ChatMessage("user", "hi")])


def test_resilient_llm_complete_goes_through_resilience():
    stats: dict[str, int] = {}
    client = _llm_client([_transient()], stats=stats)
    assert client.complete("hi") == "reply"
    assert stats == {"llm_attempts": 2, "llm_retries": 1}


def test_resilient_llm_close_delegates_to_inner():
    closed = []

    class _Closable(_ScriptedLLM):
        def close(self):
            closed.append(True)

    client = ResilientLLMClient(
        _Closable(),
        policy=RetryPolicy(max_retries=0, jitter=False),
        sleep=_no_sleep,
    )
    client.close()
    assert closed == [True]


def test_resilient_vision_retries_and_counts_stats():
    stats: dict[str, int] = {}
    inner = _ScriptedVision([_transient()])
    client = ResilientVisionClient(
        inner,
        policy=RetryPolicy(max_retries=1, base_delay_s=0.0, max_delay_s=0.0, jitter=False),
        stats=stats,
        sleep=_no_sleep,
    )
    assert client.analyze_image(b"png", "prompt") == "vision-reply"
    assert inner.calls == 2
    assert stats == {"vlm_attempts": 2, "vlm_retries": 1}


def test_wrap_llm_client_returns_inner_when_no_retry_and_no_breaker():
    settings = Settings()
    settings.llm.max_retries = 0
    settings.llm.circuit_breaker.enabled = False
    inner = _ScriptedLLM()
    assert wrap_llm_client(inner, settings.llm) is inner


def test_wrap_llm_client_wraps_when_retry_enabled():
    settings = Settings()
    settings.llm.max_retries = 1
    settings.llm.circuit_breaker.enabled = False
    wrapped = wrap_llm_client(_ScriptedLLM(), settings.llm)
    assert isinstance(wrapped, ResilientLLMClient)
    assert wrapped.retry_policy.max_retries == 1
    assert wrapped.breaker is None


def test_wrap_vision_client_returns_inner_when_disabled():
    settings = Settings()
    settings.vision.max_retries = 0
    settings.vision.circuit_breaker.enabled = False
    inner = _ScriptedVision()
    assert wrap_vision_client(inner, settings.vision) is inner


# ---------------------------------------------------------------------------
# 工厂接线
# ---------------------------------------------------------------------------


def test_factory_wraps_client_and_disables_sdk_retry(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    stats: dict[str, int] = {}
    client = get_llm_for_settings(Settings(), stats=stats)
    assert isinstance(client, ResilientLLMClient)
    # 核心防回归：SDK 重试必须为 0（否则与本层叠加成 3×3 次付费调用）
    assert client.inner_client._max_retries == 0
    assert client.retry_policy.max_retries == Settings().llm.max_retries


def test_factory_vision_wraps_and_disables_sdk_retry(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    client = get_vision_for_settings(Settings())
    assert isinstance(client, ResilientVisionClient)
    assert client.inner_client._max_retries == 0


def test_factory_returns_unwrapped_when_resilience_disabled(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings()
    settings.llm.max_retries = 0
    settings.llm.circuit_breaker.enabled = False
    client = get_llm_for_settings(settings)
    assert not isinstance(client, ResilientLLMClient)


# ---------------------------------------------------------------------------
# 配置校验
# ---------------------------------------------------------------------------


def test_config_retry_and_breaker_defaults():
    settings = Settings()
    assert settings.llm.retry.base_delay_s == 0.5
    assert settings.llm.retry.max_delay_s == 8.0
    assert settings.llm.retry.jitter is True
    assert settings.llm.circuit_breaker.enabled is True
    assert settings.llm.circuit_breaker.failure_threshold == 3
    assert settings.llm.circuit_breaker.cooldown_s == 30.0
    assert settings.vision.circuit_breaker.failure_threshold == 3


def test_config_rejects_max_delay_smaller_than_base():
    with pytest.raises(ValidationError):
        Settings.model_validate({"llm": {"retry": {"base_delay_s": 2.0, "max_delay_s": 1.0}}})


@pytest.mark.parametrize(
    "payload",
    [
        {"llm": {"circuit_breaker": {"cooldown_s": 0}}},
        {"llm": {"circuit_breaker": {"failure_threshold": 0}}},
        {"llm": {"retry": {"base_delay_s": -1}}},
        {"llm": {"retry": {"unknown_key": 1}}},
        {"llm": {"circuit_breaker": {"surprise": True}}},
    ],
)
def test_config_rejects_invalid_resilience_values(payload):
    with pytest.raises(ValidationError):
        Settings.model_validate(payload)


def test_config_accepts_resilience_overrides():
    settings = Settings.model_validate(
        {
            "llm": {
                "max_retries": 0,
                "retry": {"base_delay_s": 0.1, "max_delay_s": 0.4, "jitter": False},
                "circuit_breaker": {"enabled": False, "failure_threshold": 5, "cooldown_s": 2},
            }
        }
    )
    assert settings.llm.max_retries == 0
    assert settings.llm.retry.base_delay_s == 0.1
    assert settings.llm.retry.jitter is False
    assert settings.llm.circuit_breaker.enabled is False
    assert settings.llm.circuit_breaker.failure_threshold == 5


# ---------------------------------------------------------------------------
# 成本口径
# ---------------------------------------------------------------------------


def test_cost_summary_uses_attempts_when_available():
    reporter = HealingReporter()
    reporter.stats.update(
        {
            "llm_calls": 2,
            "llm_attempts": 5,
            "vlm_calls": 1,
            "vlm_attempts": 3,
            "llm_retries": 3,
            "vlm_circuit_open": 1,
        }
    )
    cost = reporter.cost_summary()
    assert cost["llm_calls"] == 5  # 费用口径 = 真实尝试（含重试）
    assert cost["vlm_calls"] == 3
    assert cost["llm_retries"] == 3
    assert cost["circuit_open"] == 1


def test_cost_summary_falls_back_to_calls_without_attempts():
    reporter = HealingReporter()
    reporter.stats.update({"llm_calls": 2, "vlm_calls": 1})
    cost = reporter.cost_summary()
    assert cost["llm_calls"] == 2
    assert cost["vlm_calls"] == 1
    assert cost["llm_retries"] == 0
