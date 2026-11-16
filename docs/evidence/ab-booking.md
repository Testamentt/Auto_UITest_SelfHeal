<!-- 本项目统一使用 UTF-8（无 BOM）编码。 -->
# A/B 证据：真实被测系统（票多星票务平台）

> **数据源**：`tests/e2e/test_ab_booking_scenarios.py`（真实 React 页面运行时改 `data-testid`，同真实前端升级形态）
> **前置**：票多星前端 `:3001` + 后端 `:8088/api` 已启动（未启动时用例 skip）
> **测量日期**：2026-11-17 · 本机单次 · **不是生产指标**
> **复现**：`python scripts/ab_compare.py --scenario-file tests/e2e/test_ab_booking_scenarios.py [--rounds 2]`

## 单轮：关闭 0/2 vs 开启 2/2

| 场景 | 无自愈 | 开启自愈 | 结论 |
| --- | --- | --- | --- |
| b1_admin_login_button（管理端登录按钮 testid 改版） | xfail | passed | 自愈修复 ✓ |
| b2_shop_login_button（商城登录按钮 testid 改版 + 容器 testid 移除） | xfail | passed | 自愈修复 ✓ |

| 指标 | 关闭自愈 | 开启自愈 |
| --- | --- | --- |
| 用例通过 | 0/2 | 2/2 |
| 需人工修复的失效 | **2** | **0** |
| 自愈成本 | — | LLM 0 次 / VLM 0 次 / ¥0 |

## 2 轮稳定性

| 指标 | 值 |
| --- | --- |
| 轮次 | 2 |
| 通过率（轮次口径） | 4/4 |
| 不稳定场景 | **0** |

## 覆盖边界（诚实说明）

- 真实页只做「登录按钮 testid 改版」：票多星输入框没有可访问名（React 受控 input 无 innerText），
  改 testid 后启发式只剩 testid 词元约 0.32 弱置信度，硬做会引入模型依赖、污染 A/B 结论。
- 真实场景集扩到 10~20 个需要更多可观测锚点（结算流程 / 列表页 / 弹层），是下一步第一优先级。
