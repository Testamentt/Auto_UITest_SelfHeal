"""报告钩子：把自愈事件记录到 Allure / HTML，并聚合指标。

record() 逐条记录并附 Allure；metrics() 聚合自愈指标（T3 指标看板）；
看板渲染见 dashboard.py（write_dashboard 可导出 HTML）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from selfheal.reporting.allure_bridge import attach_json

if TYPE_CHECKING:  # 仅类型检查时导入，避免 metrics → hooks 循环
    from selfheal.reporting.metrics import MetricsSnapshot


@dataclass
class HealingRecord:
    original_selector: str
    new_selector: str | None
    strategy: str | None
    confidence: float
    root_cause: str | None
    success: bool
    # T16：真自愈 vs flaky 侥幸通过。True=原定位器确已失效（真修复）；
    # False=修复后原定位器已恢复（失败是瞬时的，勿计为真修复）。
    verified: bool = True


class HealingReporter:
    def __init__(self) -> None:
        self.records: list[HealingRecord] = []
        # T17：LLM / VLM 调用计数（估算费用用；策略短路省下的调用不计数）
        self.stats: dict[str, int] = {}

    def record(self, rec: HealingRecord) -> None:
        self.records.append(rec)
        self._attach_allure(rec)

    def metrics(self) -> MetricsSnapshot:
        """聚合自愈记录指标（T3）：总数/成功率/策略分布/根因分布（类型化快照，A6）。"""
        from selfheal.reporting.metrics import compute_metrics  # 惰性导入避免循环

        return compute_metrics(self.records)

    def cost_summary(self) -> dict:
        """T17/T24：按**真实尝试次数**估算费用（重试也计费），并附带重试/熔断计数。

        计数口径（T24）：
        - `llm_calls` / `vlm_calls`：逻辑调用次数（counting_proxy 记；策略短路省下的调用不计）；
        - `llm_attempts` / `vlm_attempts`：真实请求次数（韧性层记，含重试）；
        - 费用按 attempts 估算，缺失时回退 calls——兼容注入的原生 client（未接韧性层）。
        """
        from selfheal.reporting.fix_proposals import estimate_cost  # 惰性导入避免循环

        llm_calls = self.stats.get("llm_attempts", self.stats.get("llm_calls", 0))
        vlm_calls = self.stats.get("vlm_attempts", self.stats.get("vlm_calls", 0))
        cost = estimate_cost(llm_calls, vlm_calls)
        # T24：附带回溯信息（estimate_cost 既有字段不变，新增键对旧消费方无影响）
        cost["llm_retries"] = self.stats.get("llm_retries", 0)
        cost["vlm_retries"] = self.stats.get("vlm_retries", 0)
        cost["circuit_open"] = self.stats.get("llm_circuit_open", 0) + self.stats.get(
            "vlm_circuit_open", 0
        )
        return cost

    def _attach_allure(self, rec: HealingRecord) -> None:
        """T18：自愈记录 JSON 附件（统一经 allure_bridge，未装 allure 时静默降级）。

        verified 语义明示（复用 T16 布尔，不额外采集）：
        True = 修复后验证原定位器仍失效（真自愈）；False = 原定位器已恢复（flaky 侥幸通过）。
        """
        payload = asdict(rec)
        payload["verified_by_selector_exists"] = rec.verified
        attach_json(payload, name="自愈记录")
