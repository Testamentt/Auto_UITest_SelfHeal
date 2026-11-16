<!-- 本项目统一使用 UTF-8（无 BOM）编码。 -->
# 证据：自愈触发延迟基准（探测开 / 关）

> **数据源**：`reports/probe-benchmark.md` / `reports/probe-benchmark-full.md`
> **日期**：2026-11-17（两者同日复跑）· **同机单次，非分布统计**
> **复现**：`python scripts/benchmark_probe.py`（单条）/ `python scripts/benchmark_probe.py --full`（全量）

## 单条「失效定位器 → 自愈」用例

`tests/e2e/test_healing_flow.py::test_heal_success`，唯一变量 `healing.probe.enabled`：

| 场景 | 单次耗时 |
| --- | --- |
| 关闭探测（`probe.enabled=false`） | 34.72s |
| 开启探测（默认 2500ms） | 7.09s |

> 结论：**34.72s → 7.09s（-80%）**。

## 整个 e2e 套件（`--full`）

| 场景 | 单次耗时 |
| --- | --- |
| 关闭探测 | 726.53s |
| 开启探测 | 169.94s |

> 结论：**726.53s → 169.94s（-77%）**。
> 两种口径含不同固定开销（pytest / 浏览器启动），**不可互相换算**，各自与自己的闭合比才有效。
