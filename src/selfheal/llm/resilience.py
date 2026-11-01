"""LLM/VLM 韧性层 —— 指数退避重试 + 三态熔断（T24）。

背景：`llm/_exceptions.py` 已按可恢复性把模型故障分为 Transient（429 / 超时 / 连接失败 / 5xx）
与 Fatal（401 / 403 / 400 / 404），但全仓**没有任何消费者**——分级只停留在异常类型上，
"瞬态可重试、连续失败该熔断"从未真正发生。本模块补上这两个消费者：

- `RetryPolicy` / `call_with_retry`：只对 `TransientUnavailableError` 做指数退避重试；
  `FatalUnavailableError` 与其它异常原样抛出（Fatal 重试无意义，且会掩盖 key/端点错配）。
- `CircuitBreaker`：三态（CLOSED → OPEN → HALF_OPEN）。连续 `failure_threshold` 次
  **逻辑调用**最终失败后打开；冷却期 `cooldown_s` 内直接抛 `CircuitOpenError`
  （`UnavailableError` 子类，既有降级链原样接管），冷却后放**单个**半开探针：
  成功回 CLOSED，失败回 OPEN 并重新计时。
- `ResilientLLMClient` / `ResilientVisionClient`：实现原协议，业务代码零感知；
  同时把 attempts / retries / circuit_open 计数写进 `reporter.stats`。

边界（有意为之）：

- **单进程**：熔断状态在进程内，xdist 各 worker 各持一份（不跨进程共享）。
- **失败计数按逻辑调用**：一次 `chat()` 重试 3 次仍失败只记 1 次熔断失败——熔断衡量的是
  "服务是否持续不可用"，不是"内部重试了几次"。
- **Fatal 不参与熔断**：配置错误（key 与端点不匹配）应立即暴露，不应被熔断掩盖。
- **SDK 重试必须关闭**：openai SDK 自带 `max_retries`（默认 2）。若与本层叠加，一次失败最多
  触发 3×3=9 次付费调用且彼此不可见；`llm/factory.py` 因此固定向 SDK 传 `max_retries=0`。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, TypeVar

from selfheal.llm._exceptions import CircuitOpenError, TransientUnavailableError, UnavailableError
from selfheal.llm.base import LLMClient, VisionClient

if TYPE_CHECKING:  # 仅类型检查：避免 llm 包在 import 期强依赖 config 的完整加载链
    from selfheal.config import LLMConfig, VisionConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")

# stats 的写入契约：与 reporter.stats（dict[str, int]）兼容，允许浮点以便未来扩展耗时统计
Stats = MutableMapping[str, int | float]


class CircuitState(str, Enum):
    """熔断器状态（str 枚举便于日志与报告直接展示）。"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class RetryPolicy:
    """指数退避重试策略。

    - `max_retries`：重试次数（0 = 不重试）。总调用次数上限 = `max_retries + 1`。
    - `delay_for(attempt)`：`min(max_delay_s, base_delay_s * 2**attempt)`；
      `jitter=True` 时取 `uniform(0, delay)`（full jitter，避免重试风暴同步）。
    - `rng` 可注入（测试用固定种子复现抖动序列）。
    """

    max_retries: int = 2
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0
    jitter: bool = True
    rng: random.Random = field(default_factory=random.Random, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError(f"max_retries 不能为负: {self.max_retries}")
        if self.base_delay_s < 0:
            raise ValueError(f"base_delay_s 不能为负: {self.base_delay_s}")
        if self.max_delay_s < self.base_delay_s:
            raise ValueError(
                f"max_delay_s ({self.max_delay_s}) 不能小于 base_delay_s ({self.base_delay_s})"
            )

    def delay_for(self, attempt: int) -> float:
        """第 `attempt` 次重试前的等待秒数（attempt 从 0 起，即第 1 次重试）。"""
        raw = self.base_delay_s * (2 ** max(0, attempt))
        delay = min(self.max_delay_s, raw)
        if self.jitter and delay > 0:
            delay = self.rng.uniform(0.0, delay)
        return delay


def call_with_retry(
    fn: Callable[[], T],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, float, BaseException], None] | None = None,
) -> T:
    """执行 `fn`；仅在 `TransientUnavailableError` 时按策略重试。

    Fatal / 未归类的 `UnavailableError` / 编程错误（`TypeError` 等）**不重试**：
    重试它们只会放大故障，并掩盖真正的配置问题（失败要么恢复、要么明确抛出）。

    Args:
        fn: 无参可调用对象（每次调用代表一次真实的模型请求）。
        policy: 重试策略。
        sleep: 等待实现（测试注入 fake，避免真睡）。
        on_retry: 每次重试前回调 `(第几次重试, 等待秒数, 异常)`，用于日志/指标。

    Returns:
        `fn()` 的返回值。

    Raises:
        TransientUnavailableError: 重试耗尽后抛最后一次异常。
        其它异常: 原样抛出。
    """
    attempt = 0
    while True:
        try:
            return fn()
        except TransientUnavailableError as exc:
            if attempt >= policy.max_retries:
                raise
            delay = policy.delay_for(attempt)
            if on_retry is not None:
                on_retry(attempt + 1, delay, exc)
            sleep(delay)
            attempt += 1


class CircuitBreaker:
    """三态熔断器（单进程、线程安全）。

    - CLOSED：正常放行，累计连续失败次数；达 `failure_threshold` 转 OPEN；
    - OPEN：直接抛 `CircuitOpenError`；`cooldown_s` 到期后转 HALF_OPEN；
    - HALF_OPEN：只放**一个**探针；成功回 CLOSED，失败回 OPEN 并重新计时。

    `clock` 可注入（测试用假时钟，不依赖真实等待）。失败计数按**逻辑调用**计
    （见模块 docstring），`reset()` 供测试/运维手动恢复。
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        name: str = "LLM",
    ):
        if failure_threshold < 1:
            raise ValueError(f"failure_threshold 必须 >= 1: {failure_threshold}")
        if cooldown_s <= 0:
            raise ValueError(f"cooldown_s 必须 > 0: {cooldown_s}")
        self._failure_threshold = failure_threshold
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._name = name
        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failures

    @property
    def failure_threshold(self) -> int:
        return self._failure_threshold

    @property
    def cooldown_s(self) -> float:
        return self._cooldown_s

    def before_call(self) -> None:
        """放行本次调用；OPEN（未到冷却）或 HALF_OPEN 已有探针时抛 `CircuitOpenError`。"""
        with self._lock:
            if self._state is CircuitState.OPEN:
                remaining = self._cooldown_s - (self._clock() - self._opened_at)
                if remaining > 0:
                    raise CircuitOpenError(
                        f"{self._name} 熔断中：连续 {self._failure_threshold} 次瞬态故障后打开，"
                        f"冷却剩余 {remaining:.1f}s（本次调用直接短路，走降级链）"
                    )
                self._state = CircuitState.HALF_OPEN
                self._probe_in_flight = True
                logger.info("%s 熔断冷却结束，放行单个半开探针", self._name)
                return
            if self._state is CircuitState.HALF_OPEN:
                if self._probe_in_flight:
                    raise CircuitOpenError(f"{self._name} 半开探针进行中：其余调用直接短路")
                self._probe_in_flight = True

    def record_success(self) -> None:
        """记录一次逻辑调用成功：清零失败计数并回到 CLOSED。"""
        with self._lock:
            recovered = self._state is not CircuitState.CLOSED
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._probe_in_flight = False
            self._opened_at = 0.0
            if recovered:
                logger.info("%s 熔断器恢复正常（CLOSED）", self._name)

    def record_failure(self) -> None:
        """记录一次逻辑调用失败（仅瞬态）：累计达阈值即打开；半开探针失败立即回 OPEN。"""
        with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._open_locked()
                return
            if self._state is CircuitState.OPEN:
                return
            self._failures += 1
            if self._failures >= self._failure_threshold:
                self._open_locked()

    def probe_failed(self) -> None:
        """释放半开探针并回到 OPEN。

        半开探针若以**非瞬态**异常收场（Fatal 配置错误 / 编程错误），`record_failure()` 不会
        被调用；若不显式释放 `_probe_in_flight`，熔断器会永远卡在 HALF_OPEN 并持续短路
        （既不再计时、也永不恢复）。本方法只在 HALF_OPEN 生效：探针失败即重新打开并计时。
        """
        with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._open_locked()

    def reset(self) -> None:
        """手动复位（运维/测试入口）。"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._probe_in_flight = False
            self._opened_at = 0.0

    def _open_locked(self) -> None:
        """置为 OPEN 并重新计时（调用方已持锁）。"""
        self._state = CircuitState.OPEN
        self._opened_at = self._clock()
        self._probe_in_flight = False
        logger.warning(
            "%s 熔断器打开：连续 %d 次瞬态故障（冷却 %.0fs 后半开探测）",
            self._name,
            self._failure_threshold,
            self._cooldown_s,
        )


class _Resilience:
    """包装器共享逻辑：熔断前置检查 → 带退避重试的调用 → 计数与熔断记账。"""

    def __init__(
        self,
        *,
        name: str,
        prefix: str,
        policy: RetryPolicy,
        breaker: CircuitBreaker | None,
        stats: Stats | None,
        sleep: Callable[[float], None],
    ):
        self.name = name
        self.prefix = prefix
        self.policy = policy
        self.breaker = breaker
        self._stats = stats
        self._sleep = sleep

    def run(self, fn: Callable[[], T]) -> T:
        """执行一次逻辑调用（内部可能重试多次）。"""
        if self.breaker is not None:
            try:
                self.breaker.before_call()
            except CircuitOpenError:
                # 熔断短路也计入可观测计数；不增加 attempts（没有真实请求发出）
                self._bump(f"{self.prefix}_circuit_open")
                raise
        attempts = 0

        def _attempt() -> T:
            nonlocal attempts
            attempts += 1
            return fn()

        def _on_retry(retry_no: int, delay: float, exc: BaseException) -> None:
            logger.warning(
                "%s 瞬态故障（第 %d/%d 次重试，等待 %.2fs 后重试）：%s",
                self.name,
                retry_no,
                self.policy.max_retries,
                delay,
                exc,
            )

        try:
            result = call_with_retry(_attempt, self.policy, sleep=self._sleep, on_retry=_on_retry)
        except TransientUnavailableError:
            if self.breaker is not None:
                self.breaker.record_failure()
            raise
        except UnavailableError:
            # Fatal / 未归类：不重试、不计入 CLOSED 的失败次数；但若正处在半开探针，
            # 必须释放探针并回 OPEN（否则熔断器永久卡在 HALF_OPEN）。
            if self.breaker is not None:
                self.breaker.probe_failed()
            raise
        except Exception:
            # 编程错误等非 Unavailable 异常：同样释放半开探针后原样上抛（不吞异常）。
            if self.breaker is not None:
                self.breaker.probe_failed()
            raise
        else:
            if self.breaker is not None:
                self.breaker.record_success()
            return result
        finally:
            if attempts:
                self._bump(f"{self.prefix}_attempts", attempts)
                if attempts > 1:
                    self._bump(f"{self.prefix}_retries", attempts - 1)

    def _bump(self, key: str, amount: int = 1) -> None:
        if self._stats is None:
            return
        self._stats[key] = int(self._stats.get(key, 0)) + amount


class ResilientLLMClient(LLMClient):
    """带退避重试与熔断的 LLM 客户端包装（实现 `LLMClient` 协议，业务零感知）。"""

    def __init__(
        self,
        inner: LLMClient,
        *,
        policy: RetryPolicy,
        breaker: CircuitBreaker | None = None,
        stats: Stats | None = None,
        sleep: Callable[[float], None] = time.sleep,
        name: str = "LLM",
    ):
        self._inner = inner
        self._resilience = _Resilience(
            name=name,
            prefix="llm",
            policy=policy,
            breaker=breaker,
            stats=stats,
            sleep=sleep,
        )

    @property
    def inner_client(self) -> LLMClient:
        """被包装的原生客户端（诊断/测试用）。"""
        return self._inner

    @property
    def retry_policy(self) -> RetryPolicy:
        return self._resilience.policy

    @property
    def breaker(self) -> CircuitBreaker | None:
        return self._resilience.breaker

    def chat(self, messages, **kwargs) -> str:
        return self._resilience.run(lambda: self._inner.chat(messages, **kwargs))

    def complete(self, prompt: str, **kwargs) -> str:
        return self._resilience.run(lambda: self._inner.complete(prompt, **kwargs))

    def close(self) -> None:
        """释放被包装客户端的资源（best-effort，与 orchestrator 的 _safe_close 约定一致）。"""
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()


class ResilientVisionClient(VisionClient):
    """带退避重试与熔断的 VLM 客户端包装（实现 `VisionClient` 协议）。"""

    def __init__(
        self,
        inner: VisionClient,
        *,
        policy: RetryPolicy,
        breaker: CircuitBreaker | None = None,
        stats: Stats | None = None,
        sleep: Callable[[float], None] = time.sleep,
        name: str = "VLM",
    ):
        self._inner = inner
        self._resilience = _Resilience(
            name=name,
            prefix="vlm",
            policy=policy,
            breaker=breaker,
            stats=stats,
            sleep=sleep,
        )

    @property
    def inner_client(self) -> VisionClient:
        return self._inner

    @property
    def retry_policy(self) -> RetryPolicy:
        return self._resilience.policy

    @property
    def breaker(self) -> CircuitBreaker | None:
        return self._resilience.breaker

    def analyze_image(self, image: bytes, prompt: str, **kwargs) -> str:
        return self._resilience.run(lambda: self._inner.analyze_image(image, prompt, **kwargs))

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()


def _build_policy(max_retries: int, retry_cfg) -> RetryPolicy:
    """按配置构建 RetryPolicy（`retry` 段缺省值由 pydantic 保证）。"""
    return RetryPolicy(
        max_retries=max_retries,
        base_delay_s=retry_cfg.base_delay_s,
        max_delay_s=retry_cfg.max_delay_s,
        jitter=retry_cfg.jitter,
    )


def _build_breaker(cfg, *, name: str) -> CircuitBreaker | None:
    """按配置构建熔断器；`enabled=False` 返回 None（不参与熔断）。"""
    if not cfg.enabled:
        return None
    return CircuitBreaker(
        failure_threshold=cfg.failure_threshold,
        cooldown_s=cfg.cooldown_s,
        name=name,
    )


def wrap_llm_client(
    client: LLMClient,
    cfg: LLMConfig,
    *,
    stats: Stats | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> LLMClient:
    """按配置包装 LLM 客户端；无需韧性时原样返回（零开销）。"""
    policy = _build_policy(cfg.max_retries, cfg.retry)
    breaker = _build_breaker(cfg.circuit_breaker, name="LLM")
    if policy.max_retries == 0 and breaker is None:
        return client
    return ResilientLLMClient(
        client, policy=policy, breaker=breaker, stats=stats, sleep=sleep, name="LLM"
    )


def wrap_vision_client(
    client: VisionClient,
    cfg: VisionConfig,
    *,
    stats: Stats | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> VisionClient:
    """按配置包装 VLM 客户端；无需韧性时原样返回（零开销）。"""
    policy = _build_policy(cfg.max_retries, cfg.retry)
    breaker = _build_breaker(cfg.circuit_breaker, name="VLM")
    if policy.max_retries == 0 and breaker is None:
        return client
    return ResilientVisionClient(
        client, policy=policy, breaker=breaker, stats=stats, sleep=sleep, name="VLM"
    )
