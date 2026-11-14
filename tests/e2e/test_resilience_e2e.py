# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""端到端：模型瞬态故障下的退避重试与熔断（D25）。

验证目标（真浏览器 + 假 LLM，**不调用真实付费模型**）：

1. 瞬态故障（429 模拟）会在一次自愈内按配置重试；重试耗尽后策略链优雅降级（不抛给页面）；
2. 连续失败达到阈值后熔断打开，**第二次自愈不再触碰模型**（inner 调用次数不增），
   并在短时间内返回失败——这是"模型不可用时 CI 不被逐条用例拖死"的直接证据；
3. 重试成功时，自愈链路可以真正完成（不是只会重试到失败）。

假 LLM 注入 `SelfHealOrchestrator(llm_client=...)`；重试的 sleep 被替换为空实现，
因此用例不真睡、可确定性复现。
"""

from __future__ import annotations

import pytest

from selfheal.agent.orchestrator import SelfHealOrchestrator
from selfheal.llm._exceptions import TransientUnavailableError
from selfheal.llm.base import LLMClient
from selfheal.llm.resilience import CircuitBreaker, ResilientLLMClient, RetryPolicy
from tests.e2e.pages.demo_page import DEMO_PAGE_URL

pytestmark = [pytest.mark.e2e, pytest.mark.healing]


class _AlwaysTransient(LLMClient):
    """永远抛瞬态故障的假 LLM（模拟模型端点 429 / 超时）。"""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, **kwargs):  # noqa: ANN001 - 测试假实现
        self.calls += 1
        raise TransientUnavailableError("429 simulated transient failure")


class _FlakyThenSemantic(LLMClient):
    """第一次瞬态失败、重试后返回合法语义定位 JSON 的假 LLM。"""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, **kwargs):  # noqa: ANN001 - 测试假实现
        self.calls += 1
        if self.calls == 1:
            raise TransientUnavailableError("429 simulated transient failure")
        return '{"selector": "[data-testid=\\"submit-btn\\"]", "confidence": 0.95}'


def _no_sleep(_: float) -> None:
    """测试用 sleep：不真睡。"""


def _resilient(fake: LLMClient, *, breaker=None, stats=None, max_retries: int = 2):
    return ResilientLLMClient(
        fake,
        policy=RetryPolicy(
            max_retries=max_retries, base_delay_s=0.0, max_delay_s=0.0, jitter=False
        ),
        breaker=breaker,
        stats=stats,
        sleep=_no_sleep,
    )


def _tuned_settings(settings):
    """副本：仅 semantic 策略 + 低阈值，确保链路真的走到 LLM。"""
    tuned = settings.model_copy(deep=True)
    tuned.healing.strategy_order = ["semantic"]
    tuned.healing.confidence_threshold = 0.5
    tuned.healing.early_accept_threshold = 0.9
    return tuned


def _open_demo_page(context):
    page = context.new_page()
    page.goto(DEMO_PAGE_URL)
    page.set_default_timeout(2_000)
    return page


def _fresh_knowledge(settings):
    """每用例独立内存知识库。

    不用 conftest 的 session 级 `knowledge`：e2e 全量跑时它会被其它用例写入的修复案例污染，
    导致本用例的 `run()` 走知识库命中（strategy=knowledge）而非 semantic，断言随执行顺序漂移
    ——即 session 级知识库的跨用例串扰。
    """
    from selfheal.knowledge.factory import build_knowledge_store  # 惰性导入（e2e 专用）

    tuned = settings.model_copy(deep=True)
    tuned.knowledge.backend = "memory"
    return build_knowledge_store(tuned)


def _close_knowledge(knowledge) -> None:
    close = getattr(knowledge, "close", None)
    if callable(close):
        close()


def test_transient_failures_retry_then_circuit_breaker_skips_model(context, settings):
    page = _open_demo_page(context)
    knowledge = _fresh_knowledge(settings)
    fake = _AlwaysTransient()
    clock = [0.0]
    breaker = CircuitBreaker(
        failure_threshold=2,
        cooldown_s=60.0,
        clock=lambda: clock[0],
        name="LLM",
    )
    stats: dict[str, int] = {}
    orchestrator = SelfHealOrchestrator(
        page,
        _tuned_settings(settings),
        knowledge=knowledge,
        llm_client=_resilient(fake, breaker=breaker, stats=stats),
    )
    try:
        first = orchestrator.run("#submit-btn-old", description="登录按钮")
        assert first.success is False
        # 一次自愈内：语义策略 + 失败归因诊断各 1 次逻辑调用，各重试 2 次 → 6 次真实尝试
        assert fake.calls == 6
        assert stats["llm_attempts"] == 6
        assert stats["llm_retries"] == 4
        assert breaker.state.value == "open"

        # 熔断已打开：第二次自愈不得再触碰模型，且快速失败
        second = orchestrator.run("#submit-btn-old", description="登录按钮")
        assert second.success is False
        assert fake.calls == 6  # inner 调用次数不增
        assert stats["llm_circuit_open"] >= 1
    finally:
        orchestrator.close()
        page.close()
        _close_knowledge(knowledge)


def test_retry_recovers_and_healing_succeeds(context, settings):
    page = _open_demo_page(context)
    knowledge = _fresh_knowledge(settings)
    fake = _FlakyThenSemantic()
    stats: dict[str, int] = {}
    orchestrator = SelfHealOrchestrator(
        page,
        _tuned_settings(settings),
        knowledge=knowledge,
        llm_client=_resilient(fake, stats=stats),
    )
    try:
        outcome = orchestrator.run("#submit-btn-old", description="登录按钮")
        assert outcome.success is True
        assert outcome.strategy == "semantic"
        assert outcome.new_selector == '[data-testid="submit-btn"]'
        assert fake.calls == 2  # 第 1 次瞬态失败 → 第 2 次成功
        assert stats["llm_attempts"] == 2
        assert stats["llm_retries"] == 1
        # 修复后原定位器仍不存在 → 真自愈由页面重试阶段验证，这里只断言候选可用
        assert page.locator('[data-testid="submit-btn"]').count() == 1
    finally:
        orchestrator.close()
        page.close()
        _close_knowledge(knowledge)
