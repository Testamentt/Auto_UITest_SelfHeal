"""单元测试：自愈回归通知（T19）——payload 组装 / 摘要统计 / 发送与降级（不触网）。"""

import json
import sys
from pathlib import Path

import pytest

# scripts/ 非 pythonpath 成员（pyproject pythonpath=["src"]），显式注入后按脚本模块导入
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import notify  # noqa: E402

pytestmark = pytest.mark.unit


def _record(**overrides):
    base = {
        "original_selector": "#old",
        "new_selector": "#new",
        "strategy": "heuristic",
        "confidence": 0.95,
        "root_cause": "not_found",
        "success": True,
        "verified": True,
    }
    base.update(overrides)
    return base


# --- build_summary：统计口径（与 metrics 一致） ---


def test_summary_counts_success_verified_and_strategy():
    records = [
        _record(),
        _record(strategy="semantic", success=True, verified=True),
        _record(strategy="semantic", success=False, verified=False),
    ]
    s = notify.build_summary(records)
    assert s["healing_total"] == 3
    assert s["healing_success"] == 2 and s["healing_success_rate"] == 0.667
    assert s["healing_verified"] == 2
    assert s["healing_strategy_dist"] == {"heuristic": 1, "semantic": 2}


def test_summary_empty_records_is_legal():
    s = notify.build_summary([], conclusion="failure", failed_jobs="unit, e2e")
    assert s["healing_total"] == 0 and s["healing_success_rate"] == 0.0
    assert s["conclusion"] == "failure"
    assert s["failed_jobs"] == ["unit", "e2e"]  # 逗号串解析


def test_summary_includes_cost_when_given():
    s = notify.build_summary([_record()], cost={"llm_calls": 2, "vlm_calls": 1, "total_cost": 0.5})
    assert s["cost"]["total_cost"] == 0.5


# --- build_payload：四 provider 消息体 ---


def _summary(conclusion="success"):
    return notify.build_summary(
        [_record()],
        {"llm_calls": 1, "vlm_calls": 0, "total_cost": 0.1},
        conclusion=conclusion,
        run_url="https://ci/run/1",
    )


def test_payload_dingtalk_and_wecom_markdown():
    for provider, key in (("dingtalk", "text"), ("wecom", "content")):
        p = notify.build_payload(provider, _summary("failure"))
        assert p["msgtype"] == "markdown" and key in p["markdown"]
        assert "⛔" in p["markdown"][key] and "告警" in p["markdown"][key]  # 失败样式


def test_payload_slack_text_and_success_style():
    p = notify.build_payload("slack", _summary("success"))
    assert "✅" in p["text"] and "摘要" in p["text"] and "自愈摘要" in p["text"]


def test_payload_generic_keeps_structured_summary():
    p = notify.build_payload("generic", _summary())
    assert p["title"].startswith("✅") and p["healing_total"] == 1
    assert p["run_url"] == "https://ci/run/1"


def test_payload_unknown_provider_raises():
    with pytest.raises(ValueError, match="provider"):
        notify.build_payload("sms", _summary())


# --- send：best-effort HTTP（mock，不触网） ---


class _FakeResp:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_send_posts_json_and_returns_true(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["data"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    payload = notify.build_payload("generic", _summary())
    assert notify.send("https://hook.example/x", payload) is True
    assert captured["url"] == "https://hook.example/x"
    assert captured["data"]["healing_total"] == 1


def test_send_swallows_network_error(monkeypatch):
    def boom(req, timeout):
        raise OSError("network down")

    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    assert notify.send("https://hook.example/x", {}) is False  # 不抛、返回 False


# --- main：webhook 未配置跳过 / 摘要缺失降级 ---


def test_main_skips_without_webhook_url(monkeypatch, capsys):
    monkeypatch.delenv("WEBHOOK_URL", raising=False)
    assert notify.main([]) == 0  # exit 0：不炸 CI
    assert "跳过" in capsys.readouterr().out


def test_main_sends_with_missing_summary_file(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBHOOK_URL", "https://hook.example/x")
    sent = {}
    monkeypatch.setattr(notify, "send", lambda url, payload: sent.update(url=url) or True)
    missing = tmp_path / "nope.json"
    assert notify.main(["--summary-file", str(missing)]) == 0  # 缺摘要仍告警
    assert sent["url"] == "https://hook.example/x"


def test_main_reads_summary_file(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBHOOK_URL", "https://hook.example/x")
    summary_file = tmp_path / "healing-records.json"
    summary_file.write_text(
        json.dumps({"records": [_record(success=False, verified=False)], "cost": {"llm_calls": 1}}),
        encoding="utf-8",
    )
    captured = {}

    def fake_send(url, payload):
        captured["payload"] = payload
        return True

    monkeypatch.setattr(notify, "send", fake_send)
    assert (
        notify.main(
            [
                "--summary-file",
                str(summary_file),
                "--conclusion",
                "failure",
                "--provider",
                "dingtalk",
            ]
        )
        == 0
    )
    assert captured["payload"]["markdown"]["title"].startswith("⛔")
