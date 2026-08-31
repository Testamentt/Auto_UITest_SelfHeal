"""自愈价值 A/B 对比（T20）：同一组失效场景 ×（关闭/开启自愈）双轮运行 → 对比报告。

核心卖点「自愈提升 UI 自动化稳定性」的量化实证（TODO T3b）：
- A 组（disabled）：强制关闭自愈——失效定位器超时失败 = 「需人工修复」；
- B 组（healing）：开启自愈——自动修复后通过；
- 对比产物：`reports/ab-compare.md`（通过率 / 需人工修复数 / 耗时 / 自愈成本）。

设计：
- 标准库零第三方依赖（subprocess / xml.etree / json）；
- pytest 解析与对比统计均为**纯函数**（parse_junit / build_rows / render_markdown，
  tests/unit/test_ab_compare.py 快照覆盖）；
- 两轮定向运行以 `-k <variant>` 过滤变体（同一份用例代码，见 tests/e2e/test_ab_scenarios.py）。

用法：
    python scripts/ab_compare.py            # 两轮运行 + 产出 reports/ab-compare.md
    python scripts/ab_compare.py --skip-run # 仅用既有 junitxml 重渲染（调试用）
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_FILE = "tests/e2e/test_ab_scenarios.py"
REPORT_PATH = ROOT / "reports" / "ab-compare.md"
HEALING_RECORDS = ROOT / "reports" / "healing-records.json"

# 变体 → (过滤关键字, 报告列名)；顺序即运行顺序
VARIANTS: dict[str, str] = {"disabled": "无自愈", "healing": "开启自愈"}


def build_round_cmd(variant: str, junitxml: Path) -> list[str]:
    """组装单轮 pytest 命令（-k 定向变体 + junitxml 收集）。"""
    return [
        sys.executable,
        "-m",
        "pytest",
        SCENARIO_FILE,
        "-m",
        "e2e",
        "-k",
        variant,
        f"--junitxml={junitxml}",
        "-q",
        "--no-header",
    ]


def run_round(variant: str, junitxml: Path) -> int:
    """运行单轮（A 组预期有失败/xfail，不据此判定脚本失败）。"""
    print(f"\n>>> 运行 {VARIANTS[variant]} 组（-k {variant}）...")
    return subprocess.call(build_round_cmd(variant, junitxml), cwd=str(ROOT))


def parse_junit(path: str | Path) -> dict[str, dict[str, float | str]]:
    """解析 junitxml 文件 → {用例名: {result, time}}。"""
    return _parse_tree(ET.parse(path).getroot())


def parse_junit_from_str(text: str) -> dict[str, dict[str, float | str]]:
    """解析 junitxml 字符串（单测便捷入口），行为同 parse_junit。"""
    from io import StringIO

    return _parse_tree(ET.parse(StringIO(text)).getroot())


def _parse_tree(root: ET.Element) -> dict[str, dict[str, float | str]]:
    """junit 树 → {用例名: {result, time}}。

    result 归类：passed（无失败子元素）/ failed（<failure> 或 <error>）/ skipped（<skipped>）。
    """
    cases: dict[str, dict[str, float | str]] = {}
    for tc in root.iter("testcase"):
        status = "passed"
        if tc.find("failure") is not None or tc.find("error") is not None:
            status = "failed"
        elif tc.find("skipped") is not None:
            status = "skipped"
        cases[tc.get("name", "")] = {"result": status, "time": float(tc.get("time", 0))}
    return cases


def _base_name(case_name: str) -> str:
    """用例名去参数后缀（test_ab_s1_login_button[disabled] → test_ab_s1_login_button）。"""
    return case_name.split("[", 1)[0]


def build_rows(
    junit_disabled: dict, junit_healing: dict
) -> tuple[list[dict[str, str | float]], dict[str, str]]:
    """按场景基名对齐两轮结果 → 对比行 + 汇总统计（纯函数）。

    价值判定：
    - 无自愈 failed / 开启自愈 passed → 「自愈修复 ✓」
    - 双 failed → 「自愈未救回（需人工）」
    - 双 passed → 「原定位器仍有效（非失效场景）」
    """
    rows: list[dict[str, str | float]] = []
    for base in sorted(
        {_base_name(n) for n in junit_disabled} | {_base_name(n) for n in junit_healing}
    ):
        a = junit_disabled.get(f"{base}[disabled]", {"result": "未运行", "time": 0.0})
        b = junit_healing.get(f"{base}[healing]", {"result": "未运行", "time": 0.0})
        a_res, b_res = str(a["result"]), str(b["result"])
        if a_res == "failed" and b_res == "passed":
            value = "自愈修复 ✓"
        elif a_res == "failed":
            value = "自愈未救回（需人工）"
        elif a_res == "passed":
            value = "原定位器仍有效（非失效场景）"
        else:
            value = "—"
        rows.append(
            {
                "scenario": base.replace("test_ab_", ""),
                "no_heal": a_res,
                "no_heal_time": float(a["time"]),
                "with_heal": b_res,
                "with_heal_time": float(b["time"]),
                "value": value,
            }
        )
    healed = sum(1 for r in rows if r["value"] == "自愈修复 ✓")
    summary = {
        "no_heal_pass": str(sum(1 for r in rows if r["no_heal"] == "passed")),
        "with_heal_pass": str(sum(1 for r in rows if r["with_heal"] == "passed")),
        "manual_fixes": str(sum(1 for r in rows if r["no_heal"] == "failed")),
        "auto_healed": str(healed),
        "no_heal_time": f"{sum(float(r['no_heal_time']) for r in rows):.2f}s",
        "with_heal_time": f"{sum(float(r['with_heal_time']) for r in rows):.2f}s",
    }
    return rows, summary


def render_markdown(rows: list[dict], summary: dict[str, str], cost: dict | None) -> str:
    """对比结果 → Markdown 报告（纯函数；cost 存在时附自愈成本行）。"""
    total = len(rows) or 1
    lines = [
        "# 自愈价值 A/B 对比报告（T20）",
        "",
        "> 同一组失效场景 ×（关闭/开启自愈）双轮运行 · 生成于 CI/本地，数据源：junitxml",
        "",
        "| 场景 | 无自愈 | 开启自愈 | 结论 |",
        "| --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(f"| {r['scenario']} | {r['no_heal']} | {r['with_heal']} | {r['value']} |")
    lines += [
        "",
        "| 指标 | 关闭自愈 | 开启自愈 |",
        "| --- | --- | --- |",
        f"| 用例通过 | {summary['no_heal_pass']}/{total} | {summary['with_heal_pass']}/{total} |",
        f"| 需人工修复的失效 | **{summary['manual_fixes']}** | **{0 if summary['with_heal_pass'] == total else total - int(summary['with_heal_pass'])}** |",
        f"| 总耗时 | {summary['no_heal_time']} | {summary['with_heal_time']} |",
    ]
    if cost:
        lines.append(
            f"\n> 自愈成本：LLM {cost.get('llm_calls', 0)} 次 / VLM {cost.get('vlm_calls', 0)} 次，"
            f"估算费用约 ¥{cost.get('total_cost', 0.0):.4f}（T17 成本看板口径）"
        )
    lines.append("\n> 复现：`python scripts/ab_compare.py`")
    return "\n".join(lines) + "\n"


def _load_cost() -> dict | None:
    """读 healing 轮自愈成本（healing-records.json）；缺失返回 None（不阻塞报告）。"""
    if not HEALING_RECORDS.exists():
        return None
    try:
        return json.loads(HEALING_RECORDS.read_text(encoding="utf-8")).get("cost")
    except Exception:  # noqa: BLE001 - 成本缺失不影响对比报告
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="自愈价值 A/B 对比（T20）")
    parser.add_argument("--skip-run", action="store_true", help="跳过运行，仅重渲染既有 junitxml")
    parser.add_argument("--junit-disabled", default=str(ROOT / "reports" / "ab-junit-disabled.xml"))
    parser.add_argument("--junit-healing", default=str(ROOT / "reports" / "ab-junit-healing.xml"))
    args = parser.parse_args(argv)

    exit_code = 0
    if not args.skip_run:
        for variant, xml in (("disabled", args.junit_disabled), ("healing", args.junit_healing)):
            code = run_round(variant, Path(xml))
            exit_code = max(exit_code, code if variant == "healing" else 0)
            # A 组预期失败（xfail 记绿），脚本退出码只看 B 组

    junit_disabled = parse_junit(args.junit_disabled)
    junit_healing = parse_junit(args.junit_healing)
    if not junit_healing:
        print("B 组结果为空（用例未运行？），中止报告生成")
        return 1

    rows, summary = build_rows(junit_disabled, junit_healing)
    report = render_markdown(rows, summary, _load_cost())
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report)
    print(f"报告已写入 {REPORT_PATH}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
