# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""自愈价值 A/B 对比：同一组失效场景 ×（关闭/开启自愈）双轮运行 → 对比报告。

核心卖点「自愈提升 UI 自动化稳定性」的量化实证：
- A 组（disabled）：强制关闭自愈——失效定位器超时失败 = 「需人工修复」；
- B 组（healing）：开启自愈——自动修复后通过；
- 对比产物：`reports/ab-compare.md`（通过率 / 需人工修复数 / 耗时 / 自愈成本）。

设计：
- 标准库零第三方依赖（subprocess / xml.etree / json）；
- pytest 解析与对比统计均为**纯函数**（parse_junit / build_rows / render_markdown，
  tests/unit/test_ab_compare.py 快照覆盖）；
- 两轮定向运行以 `-k <variant>` 过滤变体（同一份用例代码，见 tests/e2e/test_ab_scenarios.py）；
- 守卫：A 组（无自愈基线）为空 → 中止并说明原因；个别行「未运行」→ 报告显式告警。

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


def build_round_cmd(variant: str, junitxml: Path, scenario_file: str = SCENARIO_FILE) -> list[str]:
    """组装单轮 pytest 命令（-k 定向变体 + junitxml 收集）。"""
    return [
        sys.executable,
        "-m",
        "pytest",
        scenario_file,
        "-m",
        "e2e",
        "-k",
        variant,
        f"--junitxml={junitxml}",
        "-q",
        "--no-header",
    ]


def run_round(variant: str, junitxml: Path, scenario_file: str = SCENARIO_FILE) -> int:
    """运行单轮（A 组预期有失败/xfail，不据此判定脚本失败）。"""
    print(f"\n>>> 运行 {VARIANTS[variant]} 组（-k {variant}）...")
    return subprocess.call(build_round_cmd(variant, junitxml, scenario_file), cwd=str(ROOT))


def parse_junit(path: str | Path) -> dict[str, dict[str, float | str]]:
    """解析 junitxml 文件 → {用例名: {result, time}}。"""
    return _parse_tree(ET.parse(path).getroot())


def parse_junit_from_str(text: str) -> dict[str, dict[str, float | str]]:
    """解析 junitxml 字符串（单测便捷入口），行为同 parse_junit。"""
    from io import StringIO

    return _parse_tree(ET.parse(StringIO(text)).getroot())


def _parse_tree(root: ET.Element) -> dict[str, dict[str, float | str]]:
    """junit 树 → {用例名: {result, time}}。

    result 归类：passed（无失败子元素）/ failed（<failure> 或 <error>）/
    xfail（<skipped type="pytest.xfail">，A 组预期失败 = 无自愈需人工修复）/
    skipped（其余真跳过）。
    """
    cases: dict[str, dict[str, float | str]] = {}
    for tc in root.iter("testcase"):
        status = "passed"
        if tc.find("failure") is not None or tc.find("error") is not None:
            status = "failed"
        else:
            skipped = tc.find("skipped")
            if skipped is not None:
                status = "xfail" if "xfail" in (skipped.get("type") or "") else "skipped"
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

    任一轮缺该场景结果（值为「未运行」）的行数进 `summary["not_run"]`，
    由 render_markdown 在报告里显式告警——作废行不得静默混进结论。
    """
    rows: list[dict[str, str | float]] = []
    for base in sorted(
        {_base_name(n) for n in junit_disabled} | {_base_name(n) for n in junit_healing}
    ):
        a = junit_disabled.get(f"{base}[disabled]", {"result": "未运行", "time": 0.0})
        b = junit_healing.get(f"{base}[healing]", {"result": "未运行", "time": 0.0})
        a_res, b_res = str(a["result"]), str(b["result"])
        no_heal_broken = a_res in ("failed", "xfail")  # xfail = 无自愈时预期失败（需人工修复）
        if no_heal_broken and b_res == "passed":
            value = "自愈修复 ✓"
        elif no_heal_broken:
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
    # 任一轮缺该场景结果（junit 未收集到）→ 该行结论无意义；统计出来供报告显式告警，
    # 避免"部分未运行"的作废表格被当成有效对比（render_markdown 会渲染 ⚠️ 行）。
    not_run = sum(1 for r in rows if r["no_heal"] == "未运行" or r["with_heal"] == "未运行")
    summary = {
        "no_heal_pass": str(sum(1 for r in rows if r["no_heal"] == "passed")),
        "with_heal_pass": str(sum(1 for r in rows if r["with_heal"] == "passed")),
        "manual_fixes": str(sum(1 for r in rows if r["no_heal"] in ("failed", "xfail"))),
        "auto_healed": str(healed),
        "not_run": str(not_run),
        "no_heal_time": f"{sum(float(r['no_heal_time']) for r in rows):.2f}s",
        "with_heal_time": f"{sum(float(r['with_heal_time']) for r in rows):.2f}s",
    }
    return rows, summary


def build_rows_multi(
    disabled_runs: list[dict], healing_runs: list[dict]
) -> tuple[list[dict], dict[str, str]]:
    """多轮（`--rounds N`）聚合：每场景统计 N 轮里各有几轮通过（纯函数）。

    单轮口径（build_rows）回答"能不能修"；多轮口径回答"修得稳不稳"——
    同一个构造场景跑 N 轮，开启自愈不是每轮都过 = 不稳定（flaky 场景），
    这类数据比单轮 3/3 更接近真实稳定性叙事。
    """
    rounds = max(len(disabled_runs), len(healing_runs)) or 1
    bases = sorted(
        {_base_name(n) for run in disabled_runs for n in run}
        | {_base_name(n) for run in healing_runs for n in run}
    )
    rows: list[dict] = []
    for base in bases:
        a_pass = b_pass = 0
        missing = False
        for run in disabled_runs:
            item = run.get(f"{base}[disabled]")
            if item is None:
                missing = True
            elif item["result"] == "passed":
                a_pass += 1
        for run in healing_runs:
            item = run.get(f"{base}[healing]")
            if item is None:
                missing = True
            elif item["result"] == "passed":
                b_pass += 1
        if missing:
            value = "—（未运行）"
        elif a_pass == 0 and b_pass == rounds:
            value = "自愈修复 ✓"
        elif b_pass == 0:
            value = "自愈未救回（需人工）"
        elif 0 < b_pass < rounds:
            value = f"不稳定（{b_pass}/{rounds} 轮通过）"
        else:
            value = "原定位器仍有效（非失效场景）"
        rows.append(
            {
                "scenario": base.replace("test_ab_", ""),
                "no_heal": f"{a_pass}/{rounds}",
                "with_heal": f"{b_pass}/{rounds}",
                "no_heal_pass": a_pass,
                "with_heal_pass": b_pass,
                "value": value,
            }
        )
    total_heal = rounds * len(rows) or 1
    summary = {
        "rounds": str(rounds),
        "no_heal_pass": str(sum(r["no_heal_pass"] for r in rows)),
        "with_heal_pass": str(sum(r["with_heal_pass"] for r in rows)),
        "pass_rate": f"{sum(r['with_heal_pass'] for r in rows)}/{total_heal}",
        "manual_fixes": str(sum(1 for r in rows if r["no_heal_pass"] == 0)),
        "auto_healed": str(sum(1 for r in rows if r["value"] == "自愈修复 ✓")),
        "unstable": str(sum(1 for r in rows if r["value"].startswith("不稳定"))),
        "not_run": str(sum(1 for r in rows if r["value"].startswith("—"))),
    }
    return rows, summary


def render_markdown(
    rows: list[dict], summary: dict[str, str], cost: dict | None, *, rounds: int = 1
) -> str:
    """对比结果 → Markdown 报告（纯函数；cost 存在时附自愈成本行）。

    rounds > 1 时渲染"稳定性口径"：每场景展示 N 轮里通过几轮，并把
    "部分轮次通过"单列为不稳定——单轮 3/3 不能回答"稳不稳"。
    """
    if rounds > 1:
        return _render_stability(rows, summary, cost, rounds)
    total = len(rows) or 1
    lines = [
        "# 自愈价值 A/B 对比报告",
        "",
        "> 同一组失效场景 ×（关闭/开启自愈）双轮运行 · 生成于 CI/本地，数据源：junitxml",
        "",
        "| 场景 | 无自愈 | 开启自愈 | 结论 |",
        "| --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(f"| {r['scenario']} | {r['no_heal']} | {r['with_heal']} | {r['value']} |")
    not_run = int(summary.get("not_run", 0) or 0)
    if not_run:
        # 把"未运行"从表格里的一格提升为报告级告警——否则读者会把作废对比当结论
        lines += [
            "",
            f"> ⚠️ **告警**：{not_run} 行「未运行」——A/B 至少一轮没有该场景的结果"
            "（junit 缺失或 `-k` 过滤未命中）。这些行不参与结论，本报告**不完整**，"
            "请先确认两轮都真正跑过再解读数字。",
        ]
    lines += [
        "",
        "| 指标 | 关闭自愈 | 开启自愈 |",
        "| --- | --- | --- |",
        f"| 用例通过 | {summary['no_heal_pass']}/{total} | {summary['with_heal_pass']}/{total} |",
        f"| 需人工修复的失效 | **{summary['manual_fixes']}** | **{0 if summary['with_heal_pass'] == total else total - int(summary['with_heal_pass'])}** |",
        f"| 总耗时 | {summary['no_heal_time']} | {summary['with_heal_time']} |",
        "",
        # 口径声明：两组耗时**不可直接横向对比**，避免读者误读成"自愈更快/更慢"
        "> ⚠️ **耗时口径**：两组超时设置不同——无自愈组用 `set_default_timeout(800ms)` 快速失败，"
        "开启自愈组沿用用例默认超时。**总耗时不可横向对比**，本表只用于说明"
        "「通过数」差异；单条失效的耗时收益请看 `docs/architecture.md` 的探测小节。",
    ]
    if cost:
        lines.append(
            f"\n> 自愈成本：LLM {cost.get('llm_calls', 0)} 次 / VLM {cost.get('vlm_calls', 0)} 次，"
            f"估算费用约 ¥{cost.get('total_cost', 0.0):.4f}（成本看板口径）"
        )
    lines.append("\n> 复现：`python scripts/ab_compare.py`")
    return "\n".join(lines) + "\n"


def _render_stability(
    rows: list[dict], summary: dict[str, str], cost: dict | None, rounds: int
) -> str:
    """多轮稳定性报告（rounds > 1）。"""
    lines = [
        f"# 自愈稳定性 A/B 报告（{rounds} 轮）",
        "",
        f"> 同一组失效场景 ×（关闭/开启自愈）× {rounds} 轮 · 数据源：junitxml",
        "",
        "| 场景 | 关闭自愈（通过轮次） | 开启自愈（通过轮次） | 结论 |",
        "| --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(f"| {r['scenario']} | {r['no_heal']} | {r['with_heal']} | {r['value']} |")
    not_run = int(summary.get("not_run", 0) or 0)
    if not_run:
        lines += [
            "",
            f"> ⚠️ **告警**：{not_run} 行「未运行」——至少一轮缺少该场景结果，"
            "这些行不参与结论，本报告**不完整**。",
        ]
    lines += [
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 轮次 | {rounds} |",
        f"| 开启自愈通过率（轮次口径） | {summary['pass_rate']} |",
        f"| 全轮稳定修复场景 | {summary['auto_healed']} |",
        f"| 不稳定场景（部分轮次通过） | **{summary['unstable']}** |",
        f"| 需人工修复的失效场景 | {summary['manual_fixes']} |",
        "",
        "> ⚠️ **口径**：通过率 = 各场景各轮通过数之和 /（场景数 × 轮次）；"
        "「不稳定」= 同一个构造场景 N 轮里时过时不过（模型/时序抖动），"
        "这部分必须单列，不能并进「自愈成功」。",
        "> 场景为**构造/半真实**时须在结论里注明来源，不能当生产指标。",
    ]
    if cost:
        lines.append(
            f"\n> 自愈成本：LLM {cost.get('llm_calls', 0)} 次 / VLM {cost.get('vlm_calls', 0)} 次，"
            f"估算费用约 ¥{cost.get('total_cost', 0.0):.4f}（成本看板口径）"
        )
    lines.append(f"\n> 复现：`python scripts/ab_compare.py --rounds {rounds}`")
    return "\n".join(lines) + "\n"


def _load_cost() -> dict | None:
    """读 healing 轮自愈成本（healing-records.json）；缺失返回 None（不阻塞报告）。"""
    if not HEALING_RECORDS.exists():
        return None
    try:
        return json.loads(HEALING_RECORDS.read_text(encoding="utf-8")).get("cost")
    except Exception:  # noqa: BLE001 - 成本缺失不影响对比报告
        return None


def _round_path(base: Path, round_no: int, rounds: int) -> Path:
    """多轮时为每轮生成独立 junitxml 路径；单轮沿用原路径（零回归）。"""
    if rounds <= 1:
        return base
    return base.with_name(f"{base.stem}-r{round_no}{base.suffix}")


def main(argv: list[str] | None = None) -> int:
    # Windows GBK 控制台无法编码 ¥/emoji：stdout 统一按 UTF-8 容错输出（报告文件本就是 UTF-8）
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="自愈价值 A/B 对比")
    parser.add_argument("--skip-run", action="store_true", help="跳过运行，仅重渲染既有 junitxml")
    parser.add_argument(
        "--scenario-file",
        default=SCENARIO_FILE,
        help="场景文件路径（默认自建 demo 页；真实 SUT 用 tests/e2e/test_ab_booking_scenarios.py）",
    )
    parser.add_argument("--junit-disabled", default=str(ROOT / "reports" / "ab-junit-disabled.xml"))
    parser.add_argument("--junit-healing", default=str(ROOT / "reports" / "ab-junit-healing.xml"))
    parser.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="每组运行轮数（默认 1 = 单轮能不能修；>1 = 稳定性口径，观察 flaky 场景）",
    )
    parser.add_argument(
        "--out",
        default=str(REPORT_PATH),
        help="报告输出路径（默认 reports/ab-compare.md；可指向 docs/evidence/ 归档）",
    )
    args = parser.parse_args(argv)
    if args.rounds < 1:
        print("--rounds 必须 >= 1")
        return 2

    base_disabled = Path(args.junit_disabled)
    base_healing = Path(args.junit_healing)
    exit_code = 0
    if not args.skip_run:
        for round_no in range(1, args.rounds + 1):
            for variant, base in (("disabled", base_disabled), ("healing", base_healing)):
                xml = _round_path(base, round_no, args.rounds)
                code = run_round(variant, xml, args.scenario_file)
                # A 组预期失败（xfail 记绿），脚本退出码只看 B 组
                exit_code = max(exit_code, code if variant == "healing" else 0)

    disabled_runs = [
        parse_junit(_round_path(base_disabled, r, args.rounds)) for r in range(1, args.rounds + 1)
    ]
    healing_runs = [
        parse_junit(_round_path(base_healing, r, args.rounds)) for r in range(1, args.rounds + 1)
    ]
    # A 组为空同样中止（此前只守 B 组）——A 组空 = 没有"无自愈"基线，
    # 生成的表格全是「未运行」，任何"自愈提升"结论都不成立，属作废产物。
    if not any(disabled_runs):
        print(f"A 组结果为空（{base_disabled} 无用例结果？），无对照基线，中止报告生成")
        return 1
    if not any(healing_runs):
        print(f"B 组结果为空（{base_healing} 无用例结果？），中止报告生成")
        return 1

    if args.rounds > 1:
        rows, summary = build_rows_multi(disabled_runs, healing_runs)
    else:
        rows, summary = build_rows(disabled_runs[0], healing_runs[0])
    report = render_markdown(rows, summary, _load_cost(), rounds=args.rounds)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"报告已写入 {out}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
