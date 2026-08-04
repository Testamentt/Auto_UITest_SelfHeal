"""单元测试：自愈指标聚合 compute_metrics（T3，不依赖浏览器）。"""

import pytest

from selfheal.reporting.hooks import HealingRecord, HealingReporter
from selfheal.reporting.metrics import compute_metrics

pytestmark = pytest.mark.unit


def _rec(strategy="heuristic", root_cause="not_found", success=True):
    return HealingRecord("#old", "#new", strategy, 0.9, root_cause, success)


def test_metrics_aggregation():
    records = [
        _rec(strategy="heuristic", root_cause="not_found", success=True),
        _rec(strategy="semantic", root_cause="covered", success=True),
        _rec(strategy="heuristic", root_cause="not_found", success=False),
    ]
    m = compute_metrics(records)
    assert m["total"] == 3
    assert m["success"] == 2
    assert abs(m["success_rate"] - 2 / 3) < 1e-9
    assert m["strategy_distribution"] == {"heuristic": 2, "semantic": 1}
    assert m["root_cause_distribution"] == {"not_found": 2, "covered": 1}


def test_metrics_empty():
    m = compute_metrics([])
    assert m["total"] == 0
    assert m["success"] == 0
    assert m["success_rate"] == 0.0
    assert m["strategy_distribution"] == {}
    assert m["root_cause_distribution"] == {}


def test_reporter_metrics_convenience():
    reporter = HealingReporter()
    reporter.record(_rec(strategy="heuristic"))
    reporter.record(_rec(strategy="knowledge"))
    m = reporter.metrics()
    assert m["total"] == 2
    assert m["strategy_distribution"] == {"heuristic": 1, "knowledge": 1}
