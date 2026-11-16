# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""单元测试：自愈修复简报渲染（纯函数，不依赖浏览器）。"""

import json
import sys
from pathlib import Path

import pytest

# scripts/ 非 pythonpath 成员，显式注入后按脚本模块导入（同 test_ab_compare / test_notify 模式）
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import heal_brief  # noqa: E402

pytestmark = pytest.mark.unit


def _rec(**overrides):
    base = {
        "original_selector": "#submit-btn-old",
        "new_selector": '[data-testid="submit-btn"]',
        "strategy": "heuristic",
        "confidence": 0.85,
        "root_cause": "not_found",
        "success": True,
        "verified": True,
        "round": 1,
        "escalate": False,
    }
    base.update(overrides)
    return base


def test_render_brief_contains_core_fields():
    out = heal_brief.render_brief([_rec()], {"llm_calls": 0, "vlm_calls": 0, "total_cost": 0.0})
    assert "# 自愈修复简报" in out
    assert "`#submit-btn-old`" in out
    assert "第 1 / 2 轮" in out
    assert "定位类（根因：not_found）" in out
    assert "已沉淀知识库" in out
    assert "LLM 0 次" in out
    assert "复跑" in out


def test_render_brief_flags_escalation_and_flaky():
    records = [
        _rec(
            success=False,
            verified=False,
            round=2,
            escalate=True,
            root_cause="rounds_exhausted",
            new_selector=None,
        ),
        _rec(original_selector="#pwd", success=True, verified=False, round=2),
    ]
    out = heal_brief.render_brief(records)
    assert "达轮次上限转人工 | 1" in out
    assert "第 2 / 2 轮" in out
    assert "升级人工" in out
    assert "flaky" in out
    assert "未写入（失败/升级不沉淀）" in out


def test_render_brief_marks_uncaptured_fields_honestly():
    """缺数据的字段必须写"未采集"，不能编造模块/阶段。"""
    out = heal_brief.render_brief([_rec()])
    assert out.count("未采集") >= 2
    assert "smoke / formal 由用例数据决定" in out


def test_render_brief_handles_empty_records():
    out = heal_brief.render_brief([])
    assert "本次会话没有自愈记录" in out
    assert "| 自愈记录数 | 0 |" in out


def test_load_records_roundtrip(tmp_path):
    path = tmp_path / "healing-records.json"
    path.write_text(
        json.dumps({"records": [_rec()], "cost": {"llm_calls": 1}}, ensure_ascii=False),
        encoding="utf-8",
    )
    records, cost = heal_brief.load_records(path)
    assert len(records) == 1
    assert cost == {"llm_calls": 1}


def test_load_records_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        heal_brief.load_records(tmp_path / "nope.json")


def test_load_records_malformed_json_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        heal_brief.load_records(path)


def test_main_writes_report(tmp_path):
    records_path = tmp_path / "healing-records.json"
    records_path.write_text(json.dumps({"records": [_rec()]}), encoding="utf-8")
    out = tmp_path / "heal-brief.md"
    code = heal_brief.main(["--records", str(records_path), "--out", str(out)])
    assert code == 0
    assert out.exists()
    assert "# 自愈修复简报" in out.read_text(encoding="utf-8")


def test_main_missing_file_returns_nonzero(tmp_path):
    code = heal_brief.main(
        ["--records", str(tmp_path / "nope.json"), "--out", str(tmp_path / "x.md")]
    )
    assert code == 1
