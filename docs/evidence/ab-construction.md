<!-- 本项目统一使用 UTF-8（无 BOM）编码。 -->
# A/B 证据：构造场景（10 个 / 5 类失效形态）

> **数据源**：`tests/e2e/test_ab_scenarios.py`（自建 demo 页，零外部依赖）
> **测量日期**：2026-11-17 · **机器**：本机 Windows + 系统 Chrome（单次测量，非分布统计）
> **性质**：构造基线，0 次模型调用（确定性启发式），**不是生产指标**
> **复现**：`python scripts/ab_compare.py`（单轮）/ `--rounds 2`（稳定性）

## 单轮：关闭 0/10 vs 开启 10/10

| 场景 | 无自愈 | 开启自愈 | 结论 |
| --- | --- | --- | --- |
| s1_login_button（按钮 id 改版） | xfail | passed | 自愈修复 ✓ |
| s2_username_input（输入框 id 改版） | xfail | passed | 自愈修复 ✓ |
| s3_password_input（输入框 id 改版） | xfail | passed | 自愈修复 ✓ |
| s4_chain_leaf（容器稳定 / 叶子失效） | xfail | passed | 自愈修复 ✓ |
| s5_hash_class_regen（hash class 重生成） | xfail | passed | 自愈修复 ✓ |
| s6_text_changed（文案微调） | xfail | passed | 自愈修复 ✓ |
| s7_dom_wrapped（多包一层） | xfail | passed | 自愈修复 ✓ |
| s8_dom_moved（元素跨容器搬家） | xfail | passed | 自愈修复 ✓ |
| s9_dialog_button_renamed（弹层内 id 改版） | xfail | passed | 自愈修复 ✓ |
| s10_placeholder_changed（placeholder 微调） | xfail | passed | 自愈修复 ✓ |

| 指标 | 关闭自愈 | 开启自愈 |
| --- | --- | --- |
| 用例通过 | 0/10 | 10/10 |
| 需人工修复的失效 | **10** | **0** |
| 自愈成本 | — | LLM 0 次 / VLM 0 次 / ¥0 |

> 两组耗时不可横向对比（无自愈组用 800ms 短超时快速失败），故本表只列通过数。

## 2 轮稳定性

| 指标 | 值 |
| --- | --- |
| 轮次 | 2 |
| 开启自愈通过率（轮次口径） | 20/20 |
| 全轮稳定修复场景 | 10 |
| 不稳定场景（部分轮次通过） | **0** |

> 口径：通过率 = 各场景各轮通过数之和 /（场景数 × 轮次）；「不稳定」= 同一场景 N 轮里时过时不过。
> 这部分必须单列，不能并进「自愈成功」——它才是真实稳定性风险。
