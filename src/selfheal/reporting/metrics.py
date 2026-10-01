"""自愈指标聚合（T3）。

从 HealingRecord 列表计算量化指标：自愈总数 / 成功数 / 成功率、
策略命中分布、根因分布。为"提升通过率"的价值叙事提供数据支撑，
供看板渲染与后续 A/B 对比（T3b）使用。纯函数、零依赖、可单测。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from selfheal.reporting.hooks import HealingRecord


@dataclass(frozen=True)
class MetricsSnapshot:
    """自愈指标快照（A6）：类型化字段替代魔法字符串字典。

    看板/审计以点属性访问（metrics.total），pydantic/dataclass 提供智能提示，
    杜绝 `metrics["total"]` 拼写错误导致看板空白。除零防护在 compute_metrics 内收敛。

    H6 口径：verified / flaky / verified_rate 的**分母是成功数 success**（不是 total）。
    未成功的记录（高风险页豁免、dry_run、on_uncertain=fail 等）既不算真自愈，
    也不算 flaky 侥幸通过——它们没有"通过"这回事。
    """

    total: int
    success: int
    success_rate: float
    strategy_distribution: dict[str, int] = field(default_factory=dict)
    root_cause_distribution: dict[str, int] = field(default_factory=dict)
    verified: int = 0
    flaky: int = 0
    verified_rate: float = 0.0


def compute_metrics(records: list[HealingRecord]) -> MetricsSnapshot:
    """聚合自愈记录为指标快照。

    - total / success / success_rate: 自愈总数 / 成功数 / 成功率 [0,1]（total=0 时为 0.0）
    - strategy_distribution / root_cause_distribution: 策略 / 根因 → 次数
    - verified / flaky / verified_rate: T16 真自愈数 / flaky 侥幸通过数 / 真自愈率，
      **分母为成功数 success**（H6）：只有 success=True 的记录才参与"真自愈 vs flaky"判定，
      未成功的记录（豁免 / dry_run / on_uncertain=fail）两个计数都不进、也不进分母；
      verified_rate 恒在 [0,1]（success=0 时为 0.0）。
    """
    total = len(records)
    success = sum(1 for r in records if r.success)
    # H6：与 success 取交集——"侥幸通过"的前提是先通过，失败记录不计入 verified/flaky
    verified = sum(1 for r in records if r.success and r.verified)
    flaky = sum(1 for r in records if r.success and not r.verified)
    strategy_dist: dict[str, int] = {}
    rootcause_dist: dict[str, int] = {}
    for r in records:
        if r.strategy:
            strategy_dist[r.strategy] = strategy_dist.get(r.strategy, 0) + 1
        if r.root_cause:
            rootcause_dist[r.root_cause] = rootcause_dist.get(r.root_cause, 0) + 1
    return MetricsSnapshot(
        total=total,
        success=success,
        success_rate=(success / total) if total else 0.0,
        strategy_distribution=strategy_dist,
        root_cause_distribution=rootcause_dist,
        verified=verified,
        flaky=flaky,
        # H6：min(1.0, ...) 兜底——分子已与 success 取交集，理论上不会 >1，防未来口径漂移
        verified_rate=min(1.0, verified / success) if success else 0.0,
    )
