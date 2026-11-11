"""韧性演示（T24）：模型端点持续故障时，熔断把"每条用例都付重试成本"变成"快速短路"。

用法：
    python scripts/resilience_demo.py                # 默认 15 次逻辑调用、模拟端点 200ms
    python scripts/resilience_demo.py --calls 20 --latency 0.05

产物：`reports/resilience-demo.md`（已 gitignore，不入库），数字可直接引用到 README / 汇报。

设计说明（为什么这样对比）：
- 假端点每次请求先 sleep `latency` 再抛 TransientUnavailableError（模拟 429/超时）；
- "仅重试"组：每条逻辑调用 1+2 次请求 + 退避 0.5/1.0s，端点持续故障时全部空转；
- "重试 + 熔断"组：前 3 条逻辑调用照常重试，达到阈值后熔断打开，其余调用**零请求**短路；
- 两组都尝试同样的调用次数、最终都失败（错误率不变），差异只在耗时与请求数。
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

from selfheal.llm._exceptions import TransientUnavailableError, UnavailableError
from selfheal.llm.base import ChatMessage, LLMClient
from selfheal.llm.resilience import (
    CircuitBreaker,
    ResilientLLMClient,
    RetryPolicy,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reports" / "resilience-demo.md"


class DownEndpoint(LLMClient):
    """模拟"持续 429"的模型端点：每次请求先耗时 `latency_s`，再抛瞬态故障。"""

    def __init__(self, latency_s: float):
        self.latency_s = latency_s
        self.requests = 0

    def chat(self, messages, **kwargs):  # noqa: ANN001 - 演示假实现
        self.requests += 1
        time.sleep(self.latency_s)
        raise TransientUnavailableError("simulated 429 / timeout")


@dataclass
class ScenarioResult:
    name: str
    logical_calls: int
    requests: int
    retries: int
    circuit_open: int
    failures: int
    elapsed_s: float


def _run_scenario(
    name: str,
    *,
    calls: int,
    latency_s: float,
    use_breaker: bool,
    failure_threshold: int = 3,
    cooldown_s: float = 60.0,
) -> ScenarioResult:
    endpoint = DownEndpoint(latency_s)
    stats: dict[str, int] = {}
    breaker = (
        CircuitBreaker(failure_threshold=failure_threshold, cooldown_s=cooldown_s)
        if use_breaker
        else None
    )
    client = ResilientLLMClient(
        endpoint,
        policy=RetryPolicy(max_retries=2, base_delay_s=0.5, max_delay_s=8.0, jitter=False),
        breaker=breaker,
        stats=stats,
    )
    failures = 0
    start = time.monotonic()
    for _ in range(calls):
        try:
            client.chat([ChatMessage("user", "定位登录按钮")])
        except UnavailableError:
            failures += 1
    return ScenarioResult(
        name=name,
        logical_calls=calls,
        requests=endpoint.requests,
        retries=stats.get("llm_retries", 0),
        circuit_open=stats.get("llm_circuit_open", 0),
        failures=failures,
        elapsed_s=time.monotonic() - start,
    )


def _render(results: list[ScenarioResult], *, latency_s: float) -> str:
    baseline = next((r for r in results if r.circuit_open == 0), None)
    rows = "\n".join(
        f"| {r.name} | {r.logical_calls} | {r.requests} | {r.retries} | "
        f"{r.circuit_open} | {r.failures} | {r.elapsed_s:.2f}s |"
        for r in results
    )
    reduction = ""
    with_breaker = next((r for r in results if r.circuit_open > 0), None)
    if baseline and with_breaker and baseline.elapsed_s > 0:
        cut = (1 - with_breaker.elapsed_s / baseline.elapsed_s) * 100
        reduction = (
            f"\n> 结论：端点持续故障时，熔断把 {baseline.logical_calls} 次自愈的等待从 "
            f"**{baseline.elapsed_s:.2f}s 降到 {with_breaker.elapsed_s:.2f}s"
            f"（-{cut:.0f}%）**，请求数从 {baseline.requests} 降到 {with_breaker.requests}；"
            "两组最终都失败（错误率不变），差异只在「是否空转」。\n"
        )
    return f"""# 韧性演示（T24：指数退避 + 熔断）

> 生成于本地；模型端点模拟为"每次请求耗时 {latency_s * 1000:.0f}ms 后返回 429"，
> 重试 2 次（退避 0.5s / 1.0s，无抖动），熔断阈值 3 次 / 冷却 60s。数字为**模拟值**，
> 真实环境取决于模型端点延迟与故障持续时间。

| 场景 | 逻辑调用 | 真实请求 | 重试次数 | 熔断短路 | 最终失败 | 总耗时 |
| --- | --- | --- | --- | --- | --- | --- |
{rows}
{reduction}
> 复现：`python scripts/resilience_demo.py`（参数 `--calls` / `--latency` 可调）。
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="退避重试 + 熔断的耗时对比演示")
    parser.add_argument("--calls", type=int, default=15, help="逻辑调用次数（默认 15）")
    parser.add_argument("--latency", type=float, default=0.2, help="单次请求模拟耗时秒（默认 0.2）")
    parser.add_argument("--failure-threshold", type=int, default=3, help="熔断阈值（默认 3）")
    args = parser.parse_args()

    results = [
        _run_scenario(
            "仅重试（无熔断）",
            calls=args.calls,
            latency_s=args.latency,
            use_breaker=False,
        ),
        _run_scenario(
            "重试 + 熔断",
            calls=args.calls,
            latency_s=args.latency,
            use_breaker=True,
            failure_threshold=args.failure_threshold,
        ),
    ]
    report = _render(results, latency_s=args.latency)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(report, encoding="utf-8")
    print(report)
    print(f"报告已写入 {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
