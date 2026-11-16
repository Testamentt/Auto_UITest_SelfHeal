# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""单元测试：自愈价值 A/B 对比 —— junit 解析 / 对齐统计 / Markdown 渲染（纯函数）。"""

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
    # xfail 在 junit 里记 <skipped type="pytest.xfail">：归为 xfail（=无自愈预期失败），非真 skipped
    assert cases["test_ab_s2_username_input[disabled]"]["result"] == "xfail"


def test_build_rows_aligns_variants_and_judges_value():
    junit = ab_compare.parse_junit_from_str(JUNIT_SAMPLE)
    junit["test_ab_s3_password_input[disabled]"] = {"result": "failed", "time": 1.0}
    junit["test_ab_s3_password_input[healing]"] = {"result": "failed", "time": 2.0}
    rows, summary = ab_compare.build_rows(junit, junit)
    by_scenario = {r["scenario"]: r for r in rows}
    assert by_scenario["s1_login_button"]["value"] == "自愈修复 ✓"  # A xfail（预期失败）+ B 通过
    assert by_scenario["s2_username_input"]["value"] == "自愈修复 ✓"
    assert by_scenario["s3_password_input"]["value"] == "自愈未救回（需人工）"
    assert summary["manual_fixes"] == "3" and summary["auto_healed"] == "2"
    assert summary["with_heal_pass"] == "2" and summary["no_heal_pass"] == "0"


def test_build_rows_distinguishes_real_skip_from_xfail():
    """真 skipped（非 xfail）不算"需人工修复"：只标注 —，不进 manual_fixes。"""
    junit = {
        "test_ab_s1_login_button[disabled]": {"result": "skipped", "time": 0.0},
        "test_ab_s1_login_button[healing]": {"result": "passed", "time": 1.0},
    }
    rows, summary = ab_compare.build_rows(junit, junit)
    assert rows[0]["value"] == "—"
    assert summary["manual_fixes"] == "0"


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


def test_build_round_cmd_supports_custom_scenario_file():
    """真实 SUT 场景文件（票多星）可通过 --scenario-file 指定。"""
    cmd = ab_compare.build_round_cmd(
        "healing",
        ab_compare.ROOT / "reports" / "x.xml",
        "tests/e2e/test_ab_booking_scenarios.py",
    )
    joined = " ".join(cmd)
    assert "test_ab_booking_scenarios.py" in joined
    assert "-k healing" in joined


# --- A 组守卫 +「未运行」告警行（作废对比不得静默产出） ---


def test_build_rows_counts_not_run_rows():
    """任一轮缺结果的行计入 summary['not_run']（A 缺 / B 缺都算）。"""
    only_b = {"test_ab_s1_login_button[healing]": {"result": "passed", "time": 1.0}}
    only_a = {"test_ab_s1_login_button[disabled]": {"result": "failed", "time": 1.0}}
    rows, summary = ab_compare.build_rows(only_a, only_b)
    assert len(rows) == 1 and rows[0]["no_heal"] == "failed" and rows[0]["with_heal"] == "passed"
    assert summary["not_run"] == "0"

    rows, summary = ab_compare.build_rows({}, only_b)  # A 组缺
    assert rows[0]["no_heal"] == "未运行" and summary["not_run"] == "1"

    rows, summary = ab_compare.build_rows(only_a, {})  # B 组缺
    assert rows[0]["with_heal"] == "未运行" and summary["not_run"] == "1"


# --- 多轮稳定性口径（--rounds > 1） ---


def _junit(passed: bool, name: str) -> dict:
    """构造单轮 junit 解析结果：passed / failed。"""
    return {name: {"result": "passed" if passed else "failed", "time": 1.0}}


def test_build_rows_multi_separates_stable_from_unstable():
    """2 轮：全轮通过=稳定修复；只过一半=不稳定（flaky 场景单列，不并入成功）。"""
    disabled = [
        {
            "test_ab_s1_login_button[disabled]": {"result": "failed", "time": 1.0},
            "test_ab_s2_username_input[disabled]": {"result": "xfail", "time": 1.0},
        },
        {
            "test_ab_s1_login_button[disabled]": {"result": "failed", "time": 1.0},
            "test_ab_s2_username_input[disabled]": {"result": "xfail", "time": 1.0},
        },
    ]
    healing = [
        {
            "test_ab_s1_login_button[healing]": {"result": "passed", "time": 1.0},
            "test_ab_s2_username_input[healing]": {"result": "passed", "time": 1.0},
        },
        {
            "test_ab_s1_login_button[healing]": {"result": "passed", "time": 1.0},
            "test_ab_s2_username_input[healing]": {"result": "failed", "time": 1.0},
        },
    ]
    rows, summary = ab_compare.build_rows_multi(disabled, healing)
    by_scenario = {r["scenario"]: r for r in rows}
    assert by_scenario["s1_login_button"]["value"] == "自愈修复 ✓"
    assert by_scenario["s1_login_button"]["with_heal"] == "2/2"
    assert by_scenario["s2_username_input"]["value"] == "不稳定（1/2 轮通过）"
    assert summary["pass_rate"] == "3/4"
    assert summary["unstable"] == "1"
    assert summary["auto_healed"] == "1"


def test_build_rows_multi_marks_missing_round():
    disabled = [{}, _junit(False, "test_ab_s1_login_button[disabled]")]
    healing = [_junit(True, "test_ab_s1_login_button[healing]"), {}]
    rows, summary = ab_compare.build_rows_multi(disabled, healing)
    assert rows[0]["value"] == "—（未运行）"
    assert summary["not_run"] == "1"


def test_render_stability_report():
    disabled = [_junit(False, "test_ab_s1_login_button[disabled]")]
    healing = [_junit(True, "test_ab_s1_login_button[healing]")]
    rows, summary = ab_compare.build_rows_multi(disabled, healing)
    md = ab_compare.render_markdown(rows, summary, None, rounds=2)
    assert "自愈稳定性 A/B 报告（2 轮）" in md
    assert "开启自愈通过率（轮次口径）" in md
    assert "不稳定场景（部分轮次通过）" in md
    assert "--rounds 2" in md


def test_round_path_only_changes_for_multi_round():
    base = Path("reports") / "ab-junit-disabled.xml"
    assert ab_compare._round_path(base, 1, 1) == base
    assert ab_compare._round_path(base, 2, 2).name == "ab-junit-disabled-r2.xml"


def test_main_out_and_rounds_skip_run(tmp_path):
    """--skip-run + --rounds 2：读每轮 junitxml，写入 --out 指定路径。"""
    for r in (1, 2):
        (tmp_path / f"ab-junit-disabled-r{r}.xml").write_text(
            '<?xml version="1.0"?><testsuite><testcase name="test_ab_s1_login_button[disabled]"/>'
            "</testsuite>",
            encoding="utf-8",
        )
        (tmp_path / f"ab-junit-healing-r{r}.xml").write_text(
            '<?xml version="1.0"?><testsuite><testcase name="test_ab_s1_login_button[healing]"/>'
            "</testsuite>",
            encoding="utf-8",
        )
    out = tmp_path / "stability.md"
    code = ab_compare.main(
        [
            "--skip-run",
            "--rounds",
            "2",
            "--junit-disabled",
            str(tmp_path / "ab-junit-disabled.xml"),
            "--junit-healing",
            str(tmp_path / "ab-junit-healing.xml"),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "自愈稳定性 A/B 报告（2 轮）" in text


def test_main_single_round_out_override(tmp_path):
    (tmp_path / "a.xml").write_text(
        '<?xml version="1.0"?><testsuite><testcase name="test_ab_s1_login_button[disabled]">'
        "<failure/></testcase></testsuite>",
        encoding="utf-8",
    )
    (tmp_path / "b.xml").write_text(
        '<?xml version="1.0"?><testsuite><testcase name="test_ab_s1_login_button[healing]"/>'
        "</testsuite>",
        encoding="utf-8",
    )
    out = tmp_path / "ab.md"
    assert (
        ab_compare.main(
            [
                "--skip-run",
                "--junit-disabled",
                str(tmp_path / "a.xml"),
                "--junit-healing",
                str(tmp_path / "b.xml"),
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert "自愈价值 A/B 对比报告" in out.read_text(encoding="utf-8")


def test_render_markdown_warns_when_rows_not_run():
    """有「未运行」行 → 报告追加显式告警行（含行数与"不完整"提示）。"""
    rows, summary = ab_compare.build_rows(
        {"test_ab_s1_login_button[disabled]": {"result": "failed", "time": 1.0}}, {}
    )
    md = ab_compare.render_markdown(rows, summary, None)
    assert "⚠️ **告警**" in md and "1 行「未运行」" in md
    assert "不完整" in md


def test_render_markdown_no_warning_when_all_rows_ran():
    """两轮都有结果 → 无告警行（告警不得滥用，否则等于没有告警）。"""
    junit = ab_compare.parse_junit_from_str(JUNIT_SAMPLE)
    rows, summary = ab_compare.build_rows(junit, junit)
    md = ab_compare.render_markdown(rows, summary, None)
    assert summary["not_run"] == "0"
    assert "⚠️ **告警**" not in md


def test_render_markdown_tolerates_summary_without_not_run_key():
    """兼容手工构造的旧 summary（缺 not_run 键）：退化为不告警，而不是 KeyError。"""
    rows = [{"scenario": "s1", "no_heal": "failed", "with_heal": "passed", "value": "自愈修复 ✓"}]
    _rows, summary = ab_compare.build_rows({}, {})
    summary.pop("not_run")  # 模拟旧版 summary（无 not_run 键）
    md = ab_compare.render_markdown(rows, summary, None)
    assert "⚠️ **告警**" not in md


def test_main_aborts_when_group_a_empty(monkeypatch, tmp_path, capsys):
    """A 组（无自愈基线）为空 → 中止报告生成、退出码 1、不写报告文件。"""
    monkeypatch.setattr(ab_compare, "parse_junit", lambda path: {})
    monkeypatch.setattr(ab_compare, "REPORT_PATH", tmp_path / "ab-compare.md")
    assert ab_compare.main(["--skip-run"]) == 1
    out = capsys.readouterr().out
    assert "A 组结果为空" in out and "无对照基线" in out
    assert not (tmp_path / "ab-compare.md").exists()


def test_main_aborts_when_group_b_empty(monkeypatch, tmp_path, capsys):
    """对称守卫：B 组为空同样中止（既有行为，补用例锁定）。"""
    monkeypatch.setattr(
        ab_compare,
        "parse_junit",
        lambda path: (
            {"test_ab_s1_login_button[disabled]": {"result": "failed", "time": 1.0}}
            if "disabled" in str(path)
            else {}
        ),
    )
    monkeypatch.setattr(ab_compare, "REPORT_PATH", tmp_path / "ab-compare.md")
    assert ab_compare.main(["--skip-run"]) == 1
    assert "B 组结果为空" in capsys.readouterr().out
    assert not (tmp_path / "ab-compare.md").exists()


def test_main_writes_report_with_not_run_warning(monkeypatch, tmp_path):
    """部分未运行：仍产出报告，但报告里带告警行（非静默）。"""
    only_b = ab_compare.parse_junit_from_str(
        JUNIT_SAMPLE.replace("test_ab_s1_login_button[disabled]", "test_ab_other[disabled]")
    )
    monkeypatch.setattr(
        ab_compare,
        "parse_junit",
        lambda path: (
            only_b if "disabled" in str(path) else ab_compare.parse_junit_from_str(JUNIT_SAMPLE)
        ),
    )
    monkeypatch.setattr(ab_compare, "REPORT_PATH", tmp_path / "ab-compare.md")
    monkeypatch.setattr(ab_compare, "_load_cost", lambda: None)
    assert ab_compare.main(["--skip-run"]) == 0
    report = (tmp_path / "ab-compare.md").read_text(encoding="utf-8")
    assert "⚠️ **告警**" in report
