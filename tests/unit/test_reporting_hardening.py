"""单元测试：代码审查修复项 —— H6 指标口径 / M8 静默失败日志 / M16 交付物口径与格式。

- H6：verified / flaky / verified_rate 分母 = 成功数；失败记录不计入 flaky；verified_rate ≤ 1。
- M8：allure_bridge 三个捕获分支记 warning（R4 不静默、返回值语义不变）；
  collector 网络事件处理失败记 debug（高频路径不刷屏）。
- M16：write_environment 的 `Browser.Trace` 记录实际生效值（trace_enabled 优先）；
  fix-proposals.md 首次写入补表头 + 分隔行、二次不重复、strategy 字段转义。

不依赖浏览器 / 不触网。
"""

import logging
import types

import pytest

from selfheal.collect.collector import SceneCollector
from selfheal.reporting import allure_bridge as bridge
from selfheal.reporting import fix_proposals
from selfheal.reporting.hooks import HealingRecord
from selfheal.reporting.metrics import compute_metrics

pytestmark = pytest.mark.unit

_BRIDGE_LOGGER = "selfheal.reporting.allure_bridge"
_COLLECTOR_LOGGER = "selfheal.collect.collector"


def _settings(trace: bool = False):
    """最小 fake settings（write_environment 只读这几个字段）。"""
    return types.SimpleNamespace(
        browser=types.SimpleNamespace(channel="chrome", trace=trace),
        healing=types.SimpleNamespace(
            enabled=True, confidence_threshold=0.6, early_accept_threshold=0.85
        ),
    )


# --- H6：verified / flaky / verified_rate 口径（分母 = 成功数） ---


def test_verified_rate_never_exceeds_one_when_unsuccessful_record_verified():
    """成功 1 条 + 失败但 verified=True 1 条 → 真自愈率 1.0（旧口径会算出 2.0）。"""
    records = [
        HealingRecord("#a", "#an", "heuristic", 0.9, "not_found", True, True),
        HealingRecord("#b", None, None, 0.0, "high_risk_page_excluded", False, True),
    ]
    m = compute_metrics(records)
    assert m.success == 1
    assert m.verified == 1  # 只有成功那条算真自愈
    assert m.flaky == 0
    assert m.verified_rate == 1.0
    assert 0.0 <= m.verified_rate <= 1.0


def test_verified_rate_zero_when_no_success_record():
    """全为失败记录（豁免 / dry_run）→ 真自愈率 0（不给"未成功"编造侥幸通过）。"""
    records = [
        HealingRecord("#a", None, None, 0.0, "high_risk_page_excluded", False, True),
        HealingRecord("#b", None, None, 0.0, "dry_run", False, False),
    ]
    m = compute_metrics(records)
    assert m.success == 0
    assert m.verified == 0
    assert m.flaky == 0
    assert m.verified_rate == 0.0


# --- M8：allure_bridge 失败分支记 warning（返回值语义不变） ---


def test_write_environment_failure_logs_warning(tmp_path, caplog):
    blocker = tmp_path / "not-a-dir"  # 目标是文件 → mkdir 失败 → best-effort False
    blocker.write_text("x", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=_BRIDGE_LOGGER):
        assert bridge.write_environment(blocker, _settings()) is False
    assert "环境页写入失败" in caplog.text
    assert caplog.records and caplog.records[-1].exc_info  # exc_info=True 保留堆栈


def test_attach_json_failure_logs_warning(monkeypatch, caplog):
    def _boom(*args, **kwargs):
        raise RuntimeError("allure attach failed")

    monkeypatch.setattr(bridge.allure, "attach", _boom)
    with caplog.at_level(logging.WARNING, logger=_BRIDGE_LOGGER):
        assert bridge.attach_json({"a": 1}, name="自愈记录") is False
    assert "JSON 附件失败" in caplog.text
    assert caplog.records and caplog.records[-1].exc_info


def test_attach_file_failure_logs_warning(tmp_path, monkeypatch, caplog):
    real = tmp_path / "trace.zip"
    real.write_bytes(b"PK\x03\x04")

    def _boom(*args, **kwargs):
        raise RuntimeError("attach file failed")

    monkeypatch.setattr(bridge.allure.attach, "file", _boom)
    with caplog.at_level(logging.WARNING, logger=_BRIDGE_LOGGER):
        assert bridge.attach_file(real, name="Playwright Trace（回放）") is False
    assert "文件证据附件失败" in caplog.text
    assert str(real) in caplog.text  # 日志里带路径，便于定位缺哪个证据
    assert caplog.records and caplog.records[-1].exc_info


# --- M16-1：环境页 Browser.Trace 记录实际生效值 ---


def test_write_environment_trace_enabled_overrides_config(tmp_path):
    """CLI --trace-healing 生效（trace_enabled=True）时，环境页须写 True 而非配置值 False。"""
    results_dir = tmp_path / "allure-results"
    assert bridge.write_environment(results_dir, _settings(trace=False), trace_enabled=True) is True
    content = (results_dir / "environment.properties").read_text(encoding="utf-8")
    assert "Browser.Trace=True" in content


def test_write_environment_trace_falls_back_to_config(tmp_path):
    """不传 trace_enabled（既有调用方）→ 回落配置值；显式 False 亦然。"""
    from_config = tmp_path / "from-config"
    assert bridge.write_environment(from_config, _settings(trace=True)) is True
    assert "Browser.Trace=True" in (from_config / "environment.properties").read_text(
        encoding="utf-8"
    )

    explicit_off = tmp_path / "explicit-off"
    assert (
        bridge.write_environment(explicit_off, _settings(trace=True), trace_enabled=False) is True
    )
    assert "Browser.Trace=False" in (explicit_off / "environment.properties").read_text(
        encoding="utf-8"
    )


# --- M16-2：fix-proposals.md 表头 / 分隔行 / 转义 ---


def _proposal(tmp_path, monkeypatch, **overrides) -> None:
    """写一条修复建议（路径改到 tmp_path，避免污染仓库 reports/）。"""
    monkeypatch.setattr(fix_proposals, "FIX_PROPOSALS_DIR", tmp_path / "fp")
    monkeypatch.setattr(fix_proposals, "FIX_PROPOSALS_MD", tmp_path / "fix-proposals.md")
    kwargs = {
        "original_selector": "#old",
        "new_selector": "#new",
        "strategy": "heuristic",
        "confidence": 0.9,
        "page_url": "https://x/login",
        "root_cause": "not_found",
        "verified": True,
        **overrides,
    }
    fix_proposals.write_fix_proposal(**kwargs)


def test_fix_proposal_first_write_emits_header_and_separator(tmp_path, monkeypatch):
    _proposal(tmp_path, monkeypatch)
    lines = (tmp_path / "fix-proposals.md").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "| 时间 | 原定位器 | 新定位器 | 策略 | 置信度 | 校验 |"
    assert lines[1] == "|---|---|---|---|---|---|"
    assert lines[2].startswith("| ") and "| #old |" in lines[2]  # 数据行紧跟分隔行


def test_fix_proposal_header_written_only_once(tmp_path, monkeypatch):
    _proposal(tmp_path, monkeypatch)
    _proposal(tmp_path, monkeypatch, original_selector="#old2")
    text = (tmp_path / "fix-proposals.md").read_text(encoding="utf-8")
    assert text.count("| 时间 | 原定位器 |") == 1
    assert text.count("|---|---|---|---|---|---|") == 1
    assert text.count("| #old ") == 1 and text.count("| #old2 ") == 1


def test_fix_proposal_header_added_to_existing_empty_file(tmp_path, monkeypatch):
    md = tmp_path / "fix-proposals.md"
    md.write_text("", encoding="utf-8")  # 存在但为空 → 仍需补表头
    _proposal(tmp_path, monkeypatch)
    text = md.read_text(encoding="utf-8")
    assert text.startswith("| 时间 | 原定位器 |")
    assert "|---|---|---|---|---|---|" in text


def test_fix_proposal_existing_content_gets_no_header(tmp_path, monkeypatch):
    """历史文件已有内容 → 只追加数据行（不插入表头、不覆盖既有数据）。"""
    md = tmp_path / "fix-proposals.md"
    md.write_text("| 历史数据行 |\n", encoding="utf-8")
    _proposal(tmp_path, monkeypatch)
    text = md.read_text(encoding="utf-8")
    assert text.startswith("| 历史数据行 |\n")
    assert "| 时间 |" not in text


def test_fix_proposal_escapes_pipe_in_strategy(tmp_path, monkeypatch):
    _proposal(tmp_path, monkeypatch, strategy="heuristic|semantic")
    text = (tmp_path / "fix-proposals.md").read_text(encoding="utf-8")
    assert "heuristic\\|semantic" in text  # strategy 转义，未破坏列结构
    data_row = text.splitlines()[2]
    assert data_row.count("|") - data_row.count("\\|") == 7  # 6 列 → 7 个未转义竖线


# --- M8：collector 网络事件失败分支记 debug ---


class _BoomList(list):
    """append 必抛的 list 替身（模拟网络日志缓存写入失败）。"""

    def append(self, item):
        raise RuntimeError("network log append failed")


@pytest.mark.parametrize(
    ("handler_name", "event"),
    [
        ("_on_request", types.SimpleNamespace(url="https://x/api", method="GET")),
        ("_on_response", types.SimpleNamespace(url="https://x/api", status=200)),
    ],
    ids=["request", "response"],
)
def test_network_event_failure_logs_debug(handler_name, event, caplog):
    collector = SceneCollector(None)
    collector._network_logs = _BoomList()
    with caplog.at_level(logging.DEBUG, logger=_COLLECTOR_LOGGER):
        getattr(collector, handler_name)(event)  # 不抛异常（best-effort 契约不变）
    assert caplog.records, "网络事件处理失败必须留痕（R4 禁止静默失败）"
    assert caplog.records[-1].levelno == logging.DEBUG  # 高频路径用 debug，不刷屏
    assert "网络" in caplog.text and "日志记录失败" in caplog.text
