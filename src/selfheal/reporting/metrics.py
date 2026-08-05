"""自愈指标聚合（T3）。

从 HealingRecord 列表计算量化指标：自愈总数 / 成功数 / 成功率、
策略命中分布、根因分布。为"提升通过率"的价值叙事提供数据支撑，
供看板渲染与后续 A/B 对比（T3b）使用。纯函数、零依赖、可单测。
"""

from __future__ import annotations

from selfheal.reporting.hooks import HealingRecord


def compute_metrics(records: list[HealingRecord]) -> dict:
    """聚合自愈记录为指标字典。

    Returns:
        {
            "total": int,                     # 自愈总次数
            "success": int,                   # 成功次数
            "success_rate": float,            # 成功率 [0,1]（total=0 时为 0.0）
            "strategy_distribution": dict,    # 策略 -> 次数
            "root_cause_distribution": dict,  # 根因 -> 次数
            "verified": int,                  # T16 真自愈数（原定位器确已失效）
            "flaky": int,                     # T16 flaky 侥幸通过数（失败瞬时）
            "verified_rate": float,           # T16 真自愈率 [0,1]（成功中真修复占比）
        }
    """
    total = len(records)
    success = sum(1 for r in records if r.success)
    verified = sum(1 for r in records if r.verified)
    flaky = sum(1 for r in records if not r.verified)
    strategy_dist: dict[str, int] = {}
    rootcause_dist: dict[str, int] = {}
    for r in records:
        if r.strategy:
            strategy_dist[r.strategy] = strategy_dist.get(r.strategy, 0) + 1
        if r.root_cause:
            rootcause_dist[r.root_cause] = rootcause_dist.get(r.root_cause, 0) + 1
    return {
        "total": total,
        "success": success,
        "success_rate": (success / total) if total else 0.0,
        "strategy_distribution": strategy_dist,
        "root_cause_distribution": rootcause_dist,
        "verified": verified,
        "flaky": flaky,
        "verified_rate": (verified / success) if success else 0.0,
    }
