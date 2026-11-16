# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""自愈修复简报：把 `reports/healing-records.json` 渲染成一份可交给人审的简报。

背景：单条自愈的审计字段散落在 JSON/Allure/看板里，人审时要来回翻。本脚本按
「一课一岗」的可读字段（模块/层/阶段/分类/轮次/旧→新/依据/缓存/复跑命令/残余风险）
把一次会话的自愈记录汇总成一页 Markdown。

**只读渲染，不进自愈链路**：不改 `HealingRecord`、不改 `fix_proposals`，也不回写任何
业务数据；数据缺什么就如实写「未采集」，不替调用方编造。

用法：
    python scripts/heal_brief.py
    python scripts/heal_brief.py --records reports/healing-records.json --out reports/heal-brief.md
    python scripts/heal_brief.py --rerun-cmd 'pytest tests/e2e/test_healing_flow.py -v'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECORDS = ROOT / "reports" / "healing-records.json"
DEFAULT_OUT = ROOT / "reports" / "heal-brief.md"

# 引擎的轮次上限（修根 → 修叶，两档硬编码；改动时同步本常量）
MAX_ROUNDS = 2

_UNCOLLECTED = "未采集（数据模型未包含）"


def load_records(path: str | Path) -> tuple[list[dict], dict | None]:
    """读 healing-records.json → (records, cost)；文件缺失/损坏抛 FileNotFoundError/ValueError。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到自愈记录文件：{p}")
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"自愈记录不是合法 JSON：{p}（{exc}）") from exc
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError(f"自愈记录缺少 records 数组：{p}")
    cost = payload.get("cost")
    return records, cost if isinstance(cost, dict) else None


def _classify(rec: dict) -> str:
    """失败分类：本项目只对定位超时进自愈，非定位类不会产生记录（见 architecture 失败分类）。"""
    if rec.get("escalate"):
        return "定位类（两轮闭环均未修回，已升级人工）"
    root = rec.get("root_cause") or "unknown"
    if root.startswith("heal_internal"):
        return f"自愈内部异常（{root}）"
    return f"定位类（根因：{root}）"


def _residual_risk(rec: dict) -> str:
    if rec.get("escalate"):
        return "未修回：需人工改定位后再跑（不要改断言）"
    if rec.get("success") and not rec.get("verified", True):
        return "flaky：修复后原定位器已恢复可用，可能是偶发；勿按真自愈计入指标"
    if rec.get("success"):
        return "低：修复后原定位器仍不可用（真自愈），已沉淀知识库"
    return "中：本次未成功，未沉淀"


def render_brief(
    records: list[dict],
    cost: dict | None = None,
    *,
    rerun_cmd: str = 'pytest -m "e2e and not booking and not live"',
) -> str:
    """把记录渲染为 Markdown 简报（纯函数，便于单测）。"""
    total = len(records)
    success = sum(1 for r in records if r.get("success"))
    escalated = sum(1 for r in records if r.get("escalate"))
    flaky = sum(1 for r in records if r.get("success") and not r.get("verified", True))
    lines = [
        "# 自愈修复简报",
        "",
        "> 数据源：`reports/healing-records.json`（一次会话的自愈记录）· 只读渲染，不改任何业务数据",
        "> 字段缺失处如实写「未采集」，不替记录补编造值。",
        "",
        "## 汇总",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 自愈记录数 | {total} |",
        f"| 成功 | {success} |",
        f"| 真自愈 / flaky | {success - flaky} / {flaky} |",
        f"| 达轮次上限转人工 | {escalated} |",
    ]
    if cost:
        lines.append(
            f"| 模型调用 / 估算费用 | LLM {cost.get('llm_calls', 0)} 次 / "
            f"VLM {cost.get('vlm_calls', 0)} 次 / ¥{float(cost.get('total_cost', 0.0)):.4f} |"
        )
    lines += ["", "## 明细", ""]
    if not records:
        lines.append("（本次会话没有自愈记录）")
        return "\n".join(lines) + "\n"

    for i, rec in enumerate(records, 1):
        lines += [
            f"### {i}. {rec.get('original_selector', '?')} → {rec.get('new_selector') or '（无）'}",
            "",
            "| 字段 | 值 |",
            "| --- | --- |",
            f"| 模块 / 写法 | {_UNCOLLECTED}（POM / keyword 由测试层决定，记录未带） |",
            f"| Page 或层 | {_UNCOLLECTED}（记录未带 page 对象名） |",
            "| 失败阶段 | smoke / formal 由用例数据决定，记录未带 |",
            f"| 失败分类 | {_classify(rec)} |",
            f"| 轮次 | 第 {int(rec.get('round', 1))} / {MAX_ROUNDS} 轮 |",
            f"| 旧定位 | `{rec.get('original_selector', '')}` |",
            f"| 新定位 | `{rec.get('new_selector') or '（无）'}` |",
            f"| 依据 | 策略 `{rec.get('strategy') or '—'}`，置信度 {float(rec.get('confidence', 0.0)):.2f} |",
            f"| 缓存是否已写入 | {'已沉淀知识库' if rec.get('success') and not rec.get('escalate') else '未写入（失败/升级不沉淀）'} |",
            f"| 复跑命令 | `{rerun_cmd}` |",
            f"| 残余风险 | {_residual_risk(rec)} |",
            "",
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="把自愈记录渲染成一页修复简报")
    parser.add_argument("--records", default=str(DEFAULT_RECORDS), help="healing-records.json 路径")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="简报输出路径")
    parser.add_argument(
        "--rerun-cmd",
        default='pytest -m "e2e and not booking and not live"',
        help="简报里展示的复跑命令",
    )
    args = parser.parse_args(argv)

    try:
        records, cost = load_records(args.records)
    except (FileNotFoundError, ValueError) as exc:
        print(f"{exc}")
        return 1

    report = render_brief(records, cost, rerun_cmd=args.rerun_cmd)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"简报已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
