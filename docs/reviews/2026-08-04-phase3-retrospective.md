# Phase 3 回顾评审：实现路线复盘与优化方向

> 日期：2026-08-04 · 评审范围：Phase 1–3 全部源码（`src/selfheal/`，2071 行 / 36 文件）+ 路线图
> 评审基线：`feat/phase3-sedimentation`（f19dcc1）· 关联 TODO：`docs/TODO.md`

## 一、总体评价

架构方向正确：**插件化自愈 + provider 无关抽象 + 知识优先 + 优雅降级**，四层分层清晰，测试金字塔完整（63 单测 + 5 e2e 全绿），文档纪律好，核心闭环真实跑通。

但存在几处**设计债**与**和价值叙事不匹配的缺口**：成本隐患会在配真实 key 后立刻暴露；项目最动人的"AI 自愈 + 提升通过率"叙事目前**既无真实模型验证、也无量化指标**；健壮性在页面持续变化时会现形。

## 二、设计问题（按影响排序）

| # | 问题 | 影响 | 位置 |
| --- | --- | --- | --- |
| P1 | **策略不做短路**：`_best_candidate` 遍历全部策略取最高置信度，即使启发式已高置信命中，仍会再跑 semantic(LLM)+visual(VLM)。无 key 时秒返回 None 无感；**配了真实 key 后每次自愈都白白多调一次 LLM+一次 VLM**（成本+延迟）。 | 高 | `agent/orchestrator.py::_best_candidate` |
| P2 | **缓存/修复后的选择器失败不再自愈**：`run()` 返回知识缓存或新定位器后，`HealingLocator` 重试一次；若该选择器也失效（页面又改），异常直接上抛、无二次自愈。知识缓存被无条件信任，但页面可能早已变化。 | 高 | `engine/healing_locator.py::_healing_action`、`orchestrator::_lookup_knowledge` |
| P3 | **AI 能力从未经真实模型验证**：diagnose/semantic/visual 全用 Fake 测试，真实 LLM/VLM 冒烟因无 key 一直 skip。核心"AI 自愈"卖点**零真实证据**。 | 高 | `tests/e2e/test_llm_smoke.py`、`test_visual_smoke.py` |
| P4 | **"提升通过率"无量化支撑**：痛点叙事是"通过率 80%→更高"，但无指标采集（自愈成功率/策略分布/前后对比）。看板 v0 只是审计表，不能证明价值。 | 高 | `reporting/` |
| P5 | **置信度不可比**：heuristic（词元重叠）、semantic（LLM 自报）、visual（VLM 自报）标定完全不同，跨策略"取最大"意义存疑——LLM 自报置信度本就不可靠。 | 中 | `agent/orchestrator.py::_best_candidate` |
| P6 | **配置不一致**：`settings.example.yaml` 有 `execution`/`reporting` 段，但 `config.py` 的 `Settings` 未建模，属**死配置**，pydantic 静默忽略。 | 中 | `config.py`、`settings.example.yaml` |
| P7 | **DOM 重新解析较脆弱**：自写 HTMLParser 解析 `page.content()` 快照，而非用 Playwright 原生查询；真实 DOM（注释/动态属性/畸形 HTML）下易碎，快照≠实时状态。 | 中 | `agent/dom.py` |
| P8 | **分层小瑕疵 / orchestrator 变重**：`dom.py`/`llm_io.py` 是纯工具却放 `agent/`；orchestrator 职责越来越多（collector+诊断+知识+报告+两类 client+策略构造），有 god object 苗头。 | 低 | `agent/` |
| P9 | **"越用越聪明"无 e2e 证明**：知识复用（修一次→二次命中缓存）、弹窗特征复用都没有"二次命中"的 e2e 用例。 | 低 | `tests/e2e/` |

## 三、优化机会（按性价比）

**高性价比**
1. **策略短路**（修 P1）：达到"早接受阈值"即停；或按 `strategy_order` 逐个尝试、首个达标即返回。立省 LLM/VLM 调用。
2. **真实模型跑通 + 录制证据**（修 P3）：配真实 key 跑 semantic/visual 冒烟，把真实命中截图/日志作为展示素材——把"AI 自愈"从口号变证据。
3. **自愈指标看板**（修 P4）：聚合自愈成功率、策略命中分布、根因分布、通过率前后对比。项目价值叙事的落点。
4. **修复失败二次自愈**（修 P2）：重试仍失败时标记"缓存失效"并重新走完整自愈（知识缓存可"验证后再用"）。

**中性价比**
5. 置信度归一化 / 按策略设阈值（P5）。
6. 智能等待默认融入动作前置（当前仅 POM 显式调用）。
7. 补"知识二次命中"e2e（P9），坐实"越用越聪明"。
8. 用 Playwright 原生定位替代/校验 HTMLParser 重解析（P7）。

**低优（清理）**
9. 对齐 config 与 example yaml（P6）：`execution`/`reporting` 建模或删除。
10. `dom.py`/`llm_io.py` 归位到 `collect/` 或独立 `utils/`（P8）。
11. collector 补 trace/network 采集（架构已承诺但 TODO）。

## 四、结论与建议

设计**骨架健康、无致命缺陷**，但需警惕三类问题：
- **成本隐患**（P1）在真实 key 下立刻暴露；
- **价值证据缺失**（P3/P4）——面试展示的最大软肋；
- **健壮性缺口**（P2）在页面持续变化时现形。

**建议**：Phase 4 从"展示包装"调整为 **"证据 + 指标 + 加固"**——先跑通真实模型（P3）、做出自愈指标看板（P4）、顺手修策略短路（P1）与二次自愈（P2）。这样 Phase 4 结束时项目才真正"有图有真相"。
