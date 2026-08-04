"""报告钩子：把自愈事件记录到 Allure / HTML，并聚合指标。

record() 逐条记录并附 Allure；metrics() 聚合自愈指标（T3 指标看板）；
看板渲染见 dashboard.py（write_dashboard 可导出 HTML）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class HealingRecord:
    original_selector: str
    new_selector: str | None
    strategy: str | None
    confidence: float
    root_cause: str | None
    success: bool


class HealingReporter:
    def __init__(self) -> None:
        self.records: list[HealingRecord] = []

    def record(self, rec: HealingRecord) -> None:
        self.records.append(rec)
        self._attach_allure(rec)

    def metrics(self) -> dict:
        """聚合自愈记录指标（T3）：总数/成功率/策略分布/根因分布。"""
        from selfheal.reporting.metrics import compute_metrics  # 惰性导入避免循环

        return compute_metrics(self.records)

    def _attach_allure(self, rec: HealingRecord) -> None:
        try:
            import allure

            allure.attach(
                str(asdict(rec)), name="自愈记录", attachment_type=allure.attachment_type.JSON
            )
        except ImportError:
            pass  # 未安装 allure 时静默降级
