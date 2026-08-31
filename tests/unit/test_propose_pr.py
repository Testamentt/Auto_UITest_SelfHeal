"""单元测试：修复建议自动开草稿 PR（T22）——body 组装 / 产物加载 / 查重与降级。"""

import json
import sys
from pathlib import Path

import pytest

# scripts/ 非 pythonpath 成员，显式注入后按脚本模块导入（同 test_notify.py 模式）
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import propose_pr  # noqa: E402

pytestmark = pytest.mark.unit


def _proposal(**overrides):
    base = {
        "created_at": "2026-08-31T06:00:00+00:00",
        "original_selector": "#submit-btn-old",
        "new_selector": '[data-testid="submit-btn"]',
        "strategy": "heuristic",
        "confidence": 0.95,
        "page_url": "file:///demo",
        "root_cause": "not_found",
        "verified": True,
        "applied": False,
    }
    base.update(overrides)
    return base


# --- load_proposals ---


def test_load_proposals_reads_json_and_skips_broken(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps(_proposal()), encoding="utf-8")
    (tmp_path / "bad.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "not-a-proposal.json").write_text(json.dumps({"foo": 1}), encoding="utf-8")
    proposals = propose_pr.load_proposals(tmp_path)
    assert len(proposals) == 1  # 坏文件与无 original_selector 的文件均跳过
    assert proposals[0]["original_selector"] == "#submit-btn-old"


def test_load_proposals_missing_dir_returns_empty(tmp_path):
    assert propose_pr.load_proposals(tmp_path / "nonexistent") == []


# --- build_pr_body ---


def test_pr_body_has_checklist_and_table_row():
    body = propose_pr.build_pr_body([_proposal()])
    assert "人审清单" in body and "绝不自动合并" in body
    assert "`#submit-btn-old`" in body and '`[data-testid="submit-btn"]`' in body
    assert "✅ 真自愈" in body
    assert "共 **1** 条建议" in body and "applied=false" in body


def test_pr_body_marks_flaky_and_escapes_pipe():
    body = propose_pr.build_pr_body([_proposal(verified=False, original_selector="a|b")])
    assert "⚠️ flaky" in body
    assert "a\\|b" in body  # 表格竖线转义（T15 同款约定）


def test_pr_body_empty_list_still_valid():
    body = propose_pr.build_pr_body([])
    assert "人审清单" in body and "共 **0** 条建议" in body


# --- 查重与创建（gh 调用 mock，不触网） ---


def test_has_open_pr_true_when_listed(monkeypatch):
    monkeypatch.setattr(
        propose_pr, "_run_gh", lambda args, timeout_s=30.0: _completed('{"number":1}')
    )
    assert propose_pr.has_open_pr("selfheal-proposal") is True


def test_has_open_pr_false_when_empty_or_gh_fails(monkeypatch):
    monkeypatch.setattr(propose_pr, "_run_gh", lambda args, timeout_s=30.0: _completed(""))
    assert propose_pr.has_open_pr("selfheal-proposal") is False

    def boom(args, timeout_s=30.0):
        raise FileNotFoundError("gh not installed")

    monkeypatch.setattr(propose_pr, "_run_gh", boom)
    assert propose_pr.has_open_pr("selfheal-proposal") is False  # gh 缺失按无 open PR 处理


def test_main_skips_when_no_proposals(monkeypatch, tmp_path, capsys):
    assert propose_pr.main(["--proposals-dir", str(tmp_path / "none")]) == 0
    assert "跳过" in capsys.readouterr().out


def test_main_skips_when_open_pr_exists(monkeypatch, tmp_path, capsys):
    (tmp_path / "p.json").write_text(json.dumps(_proposal()), encoding="utf-8")
    monkeypatch.setattr(propose_pr, "has_open_pr", lambda label: True)
    created = []
    monkeypatch.setattr(propose_pr, "create_draft_pr", lambda *a, **k: created.append(a) or True)
    assert propose_pr.main(["--proposals-dir", str(tmp_path)]) == 0
    assert created == []  # 查重命中：不重复创建
    assert "跳过重复" in capsys.readouterr().out


def test_main_creates_draft_pr(monkeypatch, tmp_path):
    (tmp_path / "p.json").write_text(json.dumps(_proposal()), encoding="utf-8")
    captured = {}
    monkeypatch.setattr(propose_pr, "has_open_pr", lambda label: False)

    def fake_create(head, title, body, label):
        captured.update(head=head, title=title, body=body, label=label)
        return True

    monkeypatch.setattr(propose_pr, "create_draft_pr", fake_create)
    assert propose_pr.main(["--proposals-dir", str(tmp_path), "--run-url", "https://ci/run/9"]) == 0
    assert captured["head"] == "selfheal/proposals"
    assert captured["label"] == "selfheal-proposal"
    assert "https://ci/run/9" in captured["title"]  # run 链接进标题
    assert "人审清单" in captured["body"]


def _completed(stdout: str) -> object:
    """构造 subprocess.CompletedProcess 替身（避免真调 gh）。"""
    from subprocess import CompletedProcess

    return CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")
