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
        """T17：按 LLM/VLM 调用计数估算费用（默认单价，可覆盖）。"""
        from selfheal.reporting.fix_proposals import estimate_cost  # 惰性导入避免循环

        return estimate_cost(self.stats.get("llm_calls", 0), self.stats.get("vlm_calls", 0))

    def _attach_allure(self, rec: HealingRecord) -> None:
        """T18：自愈记录 JSON 附件（统一经 allure_bridge，未装 allure 时静默降级）。

        verified 语义明示（复用 T16 布尔，不额外采集）：
        True = 修复后验证原定位器仍失效（真自愈）；False = 原定位器已恢复（flaky 侥幸通过）。
        """
        payload = asdict(rec)
        payload["verified_by_selector_exists"] = rec.verified
        attach_json(payload, name="自愈记录")
