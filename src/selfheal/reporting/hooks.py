"""报告钩子：把自愈事件记录到 Allure / HTML。

TODO: 用 allure.attach 附加截图、DOM、修复审计表；汇总生成自愈看板。
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

    def _attach_allure(self, rec: HealingRecord) -> None:
        try:
            import allure

            allure.attach(str(asdict(rec)), name="自愈记录", attachment_type=allure.attachment_type.JSON)
        except ImportError:
            pass  # 未安装 allure 时静默降级
