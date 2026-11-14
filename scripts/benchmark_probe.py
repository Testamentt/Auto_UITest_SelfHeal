# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""性能基准：自愈触发延迟（probe 开 / 关对比），产出可复现的 artifact。

**为什么需要它**：文档里写了「单条失效 30.6s → 0.6s」「e2e 全量 492s → 106s」这类数字，
但 `reports/` 被 gitignore、CI 也没有耗时断言——**裸数字不算证据**。
本脚本把这两个数字变成"任何人都能重跑出来"的产物。

用法：
    python scripts/benchmark_probe.py                # 跑 healing 闭环 e2e 两组（probe on/off）
    python scripts/benchmark_probe.py --skip-run     # 只用既有结果重渲染

产物：`reports/probe-benchmark.md`（gitignore，不入库）+ 控制台输出。

设计说明：
- 用 `tests/e2e/test_healing_flow.py::test_heal_success` 作为单条"失效定位器→自愈"基准
  （该用例内部使用已失效的 `#submit-btn-old`，必然走自愈）；
- probe off 通过环境变量 `SELFHEAL_PROBE_DISABLED=1` 注入（conftest 读取后覆盖配置），
  避免为跑基准而改仓库配置文件；
- 两组用同一台机器、同一浏览器、同一用例，唯一变量就是 probe 开关。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "reports" / "probe-benchmark.md"
TARGET = "tests/e2e/test_healing_flow.py::test_heal_success"

# conftest 识别的环境变量：置 1 时把 healing.probe.enabled 覆盖为 False
PROBE_OFF_ENV = "SELFHEAL_PROBE_DISABLED"


def build_cmd(target: str | None = TARGET) -> list[str]:
    """组装单轮 pytest 命令。

    `target` 为 None 时跑**整个 e2e 套件**（`--full` 模式，用于核验「全量耗时」口径）；
    否则只跑指定用例（默认单条失效→自愈用例，启动噪声占比最低）。
    """
    args = [sys.executable, "-m", "pytest"]
    if target is None:
        args += ["-m", "e2e and not erp and not live"]
    else:
        args.append(target)
    return args + ["-q", "--no-header", "-p", "no:cacheprovider"]


def run_once(*, probe_enabled: bool, target: str | None = TARGET) -> float:
    """跑一轮并返回耗时（秒）。失败则抛 RuntimeError（基准不能静默给出假数字）。"""
    env = os.environ.copy()
    if probe_enabled:
        env.pop(PROBE_OFF_ENV, None)
    else:
        env[PROBE_OFF_ENV] = "1"
    start = time.monotonic()
    proc = subprocess.run(build_cmd(target), cwd=str(ROOT), env=env, capture_output=True, text=True)
    elapsed = time.monotonic() - start
    if proc.returncode != 0:
        tail = (proc.stdout or "")[-800:] + (proc.stderr or "")[-400:]
        raise RuntimeError(
            f"基准用例失败（probe_enabled={probe_enabled}, rc={proc.returncode}）:\n{tail}"
        )
    return elapsed


def render(results: dict[str, float], *, full: bool = False) -> str:
    """渲染 Markdown 报告；两组都在时给出降幅。"""
    off = results.get("probe_off")
    on = results.get("probe_on")
    rows = []
    if off is not None:
        rows.append(f"| 关闭探测（`probe.enabled=false`） | {off:.2f}s |")
    if on is not None:
        rows.append(f"| 开启探测（默认 2500ms） | {on:.2f}s |")
    conclusion = ""
    if off is not None and on is not None and off > 0:
        cut = (1 - on / off) * 100
        conclusion = f"\n> 结论：探测把耗时从 **{off:.2f}s 降到 {on:.2f}s（-{cut:.0f}%）**。\n"
    scope = (
        '整个 e2e 套件（`-m "e2e and not erp and not live"`）'
        if full
        else f"单条「失效定位器 → 自愈」用例 `{TARGET}`"
    )
    return f"""# 自愈触发延迟基准（probe 开 / 关）

> 范围：{scope}。
> 唯一变量：`healing.probe.enabled`；其余（浏览器 / 机器 / 驱动版本）完全一致。

| 场景 | 单次耗时 |
| --- | --- |
{chr(10).join(rows)}
{conclusion}
> 复现：`python scripts/benchmark_probe.py{"  --full" if full else ""}`（需本机 Chrome 或 `playwright install chromium`）。
> 说明：这是**本机单次**测量，不是分布统计；结论方向（探测显著降低失败等待）稳定，
> 具体数值随机器、页面复杂度与并发度浮动。
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="自愈触发延迟基准（probe on/off）")
    parser.add_argument("--skip-run", action="store_true", help="跳过运行，仅提示（无既有结果）")
    parser.add_argument(
        "--full",
        action="store_true",
        help="跑整个 e2e 套件（核验全量耗时口径，约数分钟）；默认只跑单条用例",
    )
    args = parser.parse_args(argv)

    if args.skip_run:
        print("--skip-run 需要先跑过一次以产生结果；本脚本不做历史结果缓存。")
        return 1

    target = None if args.full else TARGET
    results: dict[str, float] = {}
    try:
        print(">>> 第一轮：关闭探测…")
        results["probe_off"] = run_once(probe_enabled=False, target=target)
        print(f"    {results['probe_off']:.2f}s")
        print(">>> 第二轮：开启探测…")
        results["probe_on"] = run_once(probe_enabled=True, target=target)
        print(f"    {results['probe_on']:.2f}s")
    except RuntimeError as exc:
        print(f"基准失败：{exc}", file=sys.stderr)
        return 1

    report = render(results, full=args.full)
    out = OUTPUT if not args.full else OUTPUT.with_name("probe-benchmark-full.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"报告已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
