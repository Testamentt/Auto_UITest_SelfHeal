"""单元测试：Allure 报告桥（T18）——环境页 / 标签优先级 / 证据附件与降级（不依赖浏览器）。"""

import json
import types

import pytest

from selfheal.reporting import allure_bridge as bridge
from selfheal.reporting.hooks import HealingRecord, HealingReporter

pytestmark = pytest.mark.unit


def _item(*marker_names):
    """最小 fake item：只暴露 iter_markers（feature_label_for 唯一依赖）。"""
    markers = [types.SimpleNamespace(name=name) for name in marker_names]
    return types.SimpleNamespace(iter_markers=lambda: iter(markers))


def _settings():
    """最小 fake settings（write_environment 只读这几个字段）。"""
    return types.SimpleNamespace(
        browser=types.SimpleNamespace(channel="chrome", trace=False),
        healing=types.SimpleNamespace(
            enabled=True, confidence_threshold=0.6, early_accept_threshold=0.85
        ),
    )


# --- 标签优先级：healing > e2e > unit，多 marker 只取最高一个 ---


def test_feature_single_unit_marker():
    assert bridge.feature_label_for(_item("unit")) == "单元测试"


def test_feature_priority_order():
    assert bridge.feature_label_for(_item("unit", "healing")) == "AI 自愈"
    assert bridge.feature_label_for(_item("unit", "e2e", "healing")) == "AI 自愈"
    assert bridge.feature_label_for(_item("unit", "e2e")) == "端到端"


def test_feature_no_matching_marker_returns_none():
    assert bridge.feature_label_for(_item()) is None
    assert bridge.feature_label_for(_item("healing")) == "AI 自愈"  # 单独 healing 也命中


# --- 动态标签（测试上下文 API）---


def test_apply_dynamic_labels_invokes_allure(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge.allure.dynamic, "epic", lambda *a: calls.append(("epic", a)))
    monkeypatch.setattr(bridge.allure.dynamic, "feature", lambda *a: calls.append(("feature", a)))
    assert bridge.apply_dynamic_labels(_item("unit", "e2e")) is True
    assert ("epic", ("AutoAiSelfHeal",)) in calls
    assert ("feature", ("端到端",)) in calls


def test_apply_dynamic_labels_no_marker_no_call(monkeypatch):
    calls = []
    monkeypatch.setattr(bridge.allure.dynamic, "epic", lambda *a: calls.append(a))
    assert bridge.apply_dynamic_labels(_item()) is False
    assert calls == []


def test_apply_dynamic_labels_disabled_without_allure(monkeypatch):
    monkeypatch.setattr(bridge, "_HAS_ALLURE", False)
    assert bridge.apply_dynamic_labels(_item("unit")) is False


# --- 环境页 ---


def test_write_environment_creates_properties(tmp_path):
    results_dir = tmp_path / "allure-results"
    assert bridge.write_environment(results_dir, _settings()) is True
    content = (results_dir / "environment.properties").read_text(encoding="utf-8")
    assert "Python=" in content
    assert "Browser.Channel=chrome" in content
    assert "Healing.Enabled=True" in content
    assert "Healing.ConfidenceThreshold=0.6" in content
    assert "Healing.EarlyAcceptThreshold=0.85" in content
    assert "CI=" in content and "Git.Commit=" in content


def test_write_environment_skipped_without_results_dir(tmp_path):
    assert bridge.write_environment(None, _settings()) is False
    assert bridge.write_environment("", _settings()) is False
    assert not (tmp_path / "environment.properties").exists()


def test_write_environment_failure_is_silent(tmp_path):
    blocker = tmp_path / "not-a-dir"  # 目标路径是文件 → mkdir 失败 → best-effort False
    blocker.write_text("x", encoding="utf-8")
    assert bridge.write_environment(blocker, _settings()) is False


def test_write_environment_disabled_without_allure(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "_HAS_ALLURE", False)
    assert bridge.write_environment(tmp_path, _settings()) is False


# --- 证据附件 ---


def test_attach_json_serializes_chinese(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        bridge.allure,
        "attach",
        lambda body, name, attachment_type: captured.update(body=body, name=name),
    )
    assert bridge.attach_json({"策略": "heuristic"}, name="自愈记录") is True
    assert captured["name"] == "自愈记录"
    assert json.loads(captured["body"])["策略"] == "heuristic"  # ensure_ascii=False 正常中文


def test_attach_file_requires_existing_file(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        bridge.allure.attach, "file", lambda path, name, attachment_type: captured.update(path=path)
    )
    real = tmp_path / "trace.zip"
    real.write_bytes(b"PK\x03\x04")
    assert bridge.attach_file(real, name="Playwright Trace（回放）") is True
    assert captured["path"] == str(real)
    assert bridge.attach_file(tmp_path / "missing.zip", name="x") is False  # 文件缺失 → False


def test_attach_json_disabled_without_allure(monkeypatch):
    monkeypatch.setattr(bridge, "_HAS_ALLURE", False)
    assert bridge.attach_json({"a": 1}) is False  # no-op 不抛


# --- hooks 接线：自愈记录附件带 verified 语义字段 ---


def test_reporter_record_attaches_verified_semantics(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        bridge.allure, "attach", lambda body, name, attachment_type: captured.update(body=body)
    )
    reporter = HealingReporter()
    reporter.record(
        HealingRecord(
            original_selector="#old",
            new_selector="#new",
            strategy="heuristic",
            confidence=0.95,
            root_cause="not_found",
            success=True,
            verified=False,  # flaky 侥幸通过
        )
    )
    payload = json.loads(captured["body"])
    assert payload["verified_by_selector_exists"] is False  # 复用 verified，不额外采集
    assert payload["strategy"] == "heuristic" and payload["success"] is True
