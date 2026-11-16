<!-- 本项目统一使用 UTF-8（无 BOM）编码。 -->
# 度量证据（docs/evidence）

> **定位**：把 `reports/`（已 gitignore，公开仓库点不开）里的**关键结论**归档成仓库内证据。
> **口径**：数字统一以 [../architecture.md](../architecture.md#可复现度量与口径唯一事实源) 的度量表为准；
> 每份文件都写明测量条件、日期与复现命令——本机单次 / 构造基线，**不是生产指标**。
> **最后更新**：2026-11-17

| 文件 | 内容 | 复现命令 |
| --- | --- | --- |
| [ab-construction.md](ab-construction.md) | 构造场景 A/B（10 场景，单轮 + 2 轮稳定性） | `python scripts/ab_compare.py [--rounds 2]` |
| [ab-booking.md](ab-booking.md) | 真实票多星登录页注入 A/B（2 场景，单轮 + 2 轮） | `python scripts/ab_compare.py --scenario-file tests/e2e/test_ab_booking_scenarios.py [--rounds 2]` |
| [probe-benchmark.md](probe-benchmark.md) | 自愈触发延迟基准（探测开 / 关，单条 + 全量） | `python scripts/benchmark_probe.py [--full]` |
| [resilience-demo.md](resilience-demo.md) | 模型端点韧性演示（退避重试 + 熔断） | `python scripts/resilience_demo.py` |
| [heal-brief-sample.md](heal-brief-sample.md) | 修复简报样例（10 字段人审视图） | `python scripts/heal_brief.py` |

> 说明：`docs/evidence/*.png` 是首屏用图（票多星管理端 / 商城）；本目录只放**脱敏**后的结论与
> 小体积产物，不归档 trace zip 与含业务数据的原始日志（隐私边界见 architecture「预期与风险」）。
