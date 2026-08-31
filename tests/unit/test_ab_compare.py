"""单元测试：自愈价值 A/B 对比（T20）——junit 解析 / 对齐统计 / Markdown 渲染（纯函数）。"""

import sys
from pathlib import Path

import pytest

# scripts/ 非 pythonpath 成员，显式注入后按脚本模块导入（同 test_notify.py 模式）
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import ab_compare  # noqa: E402

pytestmark = pytest.mark.unit

JUNIT_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="4" failures="1" errors="0" skipped="1">
  <testcase classname="tests.e2e.test_ab_scenarios" name="test_ab_s1_login_button[disabled]" time="1.5">
    <failure message="TimeoutError">...</failure>
  </testcase>
  <testcase classname="tests.e2e.test_ab_scenarios" name="test_ab_s1_login_button[healing]" time="2.0"/>
  <testcase classname="tests.e2e.test_ab_scenarios" name="test_ab_s2_username_input[disabled]" time="0.9">
    <skipped type="pytest.xfail" message="reason"/>
  </testcase>
  <testcase classname="tests.e2e.test_ab_scenarios" name="test_ab_s2_username_input[healing]" time="1.8"/>
</testsuite>
"""


def test_parse_junit_classifies_results(tmp_path):
    xml = tmp_path / "junit.xml"
    xml.write_text(JUNIT_SAMPLE, encoding="utf-8")
    cases = ab_compare.parse_junit(xml)
    assert cases["test_ab_s1_login_button[disabled]"] == {"result": "failed", "time": 1.5}
    assert cases["test_ab_s1_login_button[healing]"] == {"result": "passed", "time": 2.0}
    assert cases["test_ab_s2_username_input[disabled]"]["result"] == "skipped"  # xfail 记 skipped


def test_build_rows_aligns_variants_and_judges_value():
    junit = ab_compare.parse_junit_from_str(JUNIT_SAMPLE)
    junit["test_ab_s3_password_input[disabled]"] = {"result": "failed", "time": 1.0}
    junit["test_ab_s3_password_input[healing]"] = {"result": "failed", "time": 2.0}
    rows, summary = ab_compare.build_rows(junit, junit)
    by_scenario = {r["scenario"]: r for r in rows}
    assert by_scenario["s1_login_button"]["value"] == "自愈修复 ✓"  # A 失败 + B 通过
    assert by_scenario["s3_password_input"]["value"] == "自愈未救回（需人工）"
    assert summary["manual_fixes"] == "2" and summary["auto_healed"] == "1"
    assert summary["with_heal_pass"] == "2"


def test_build_rows_handles_missing_variant():
    rows, _summary = ab_compare.build_rows(
        {"test_ab_s1_login_button[disabled]": {"result": "failed", "time": 1.0}}, {}
    )
    assert rows[0]["with_heal"] == "未运行"  # B 组缺失不炸、显式标注


def test_render_markdown_includes_table_and_totals():
    rows, summary = ab_compare.build_rows(ab_compare.parse_junit_from_str(JUNIT_SAMPLE), {})
    md = ab_compare.render_markdown(rows, summary, None)
    assert "自愈价值 A/B 对比报告" in md
    assert "| 场景 | 无自愈 | 开启自愈 | 结论 |" in md
    assert "需人工修复的失效" in md and f"**{summary['manual_fixes']}**" in md
    assert "自愈成本" not in md  # 无 cost 时不渲染成本行


def test_render_markdown_appends_cost_when_present():
    rows, summary = ab_compare.build_rows(ab_compare.parse_junit_from_str(JUNIT_SAMPLE), {})
    md = ab_compare.render_markdown(
        rows, summary, {"llm_calls": 3, "vlm_calls": 1, "total_cost": 0.42}
    )
    assert "自愈成本" in md and "0.4200" in md


def test_build_round_cmd_targets_variant():
    cmd = ab_compare.build_round_cmd("disabled", ab_compare.ROOT / "reports" / "a.xml")
    joined = " ".join(cmd)
    assert "-k disabled" in joined and "--junitxml" in joined and "test_ab_scenarios" in joined
