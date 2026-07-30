# 项目路线图（Roadmap）

> **活文档**：随进展同步更新，不是快照（见 `RULE.md` R5）。
> 最后更新：2026-07-30 · 当前阶段：Phase 1 启动前

## 当前目标

**Phase 1 —— 用最小代价跑通一条端到端自愈闭环**，产出首个"自愈成功"可演示成果。

范围：定位失败捕获 → 现场采集（截图 + DOM）→ 启发式修复 → 重试成功 → 记录审计。
**不依赖任何待定项**（无需 LLM、无需最终知识库后端），可立即开工。

## 关键约束

- 遵循 `RULE.md` R1–R6：先计划后写入、测试双覆盖（`-m unit` + `-m e2e`）、临时方案三步管理、文档五要素、计划收敛纪律。
- Phase 1 **不引入 LLM/VLM**（provider 决策后置到 Phase 2）。
- 演示与集成测试基于**自建本地测试页**，且该页须 **POM 化**，保证后续可无缝切换到其他 POM 用例。
- 模型调用一律经 `llm/` 抽象层；配置集中在 `config.py`（pydantic）。

## 架构决策（插件化自愈）

自愈能力是**挂件 / 插件**，不侵入 Playwright 框架本身。三项硬性要求：

1. **POM 无缝切换**：POM 类只依赖 `page` 接口；自愈包装与原生 `Page` 接口兼容，同一份 POM 代码开/关自愈都能跑。演示页即普通 POM，换用例 = 换 fixture 注入的 POM。
2. **可开关、零侵入**：自愈由 pytest fixture 按 `settings.healing.enabled` + CLI `--selfheal/--no-selfheal` 提供。关闭时返回原生 Page，框架零感知、零开销。
3. **兜底机制**：`locator(sel, fallback=备用selector, description=...)`。AI 不确定（置信度 < 阈值）时按 `healing.on_uncertain` 处理。

**集成方式（决策 D5）**：以 **(b) Fixture 注入为骨架** + **(a) 接口兼容代理**做底层拦截 + **(c) 装饰器**作可选细粒度补充；**不采用** import 替换 / monkeypatch 魔法（隐式、跨版本脆弱、违反 R4）。

```
conftest fixture（开关 + 交付）
  └─ 开 → HealingPage（代理，接口兼容 Playwright Page）
            └─ locator() → HealingLocator：动作失败 → 采集 → 编排修复 → 重试
     关 → 原生 Page（行为完全不变）
```

**兜底行为（决策 D6）**：`on_uncertain` 默认 **use_fallback**——优先用人工预设备用定位器；无备用则带详细报告 **fail**（CI 友好）。`pause`（暂停等人工介入）仅有头 / 交互模式可用，CI 不适用。

## 整体路线

| 阶段 | 目标 | 关键产出 |
| --- | --- | --- |
| **Phase 1 · 最小闭环** | 端到端自愈跑通（启发式）+ 插件化骨架 | HealingPage 代理、开关 fixture、自建 POM 演示页、闭环单测/集成测试 |
| **Phase 2 · AI 大脑** | 接入 LLM，智能诊断 + 语义定位 | `llm/` client、`diagnose`、`semantic`、自愈看板 v0 |
| **Phase 3 · 沉淀与进阶** | 知识库持久化 + 弹窗 + 视觉 | SQLite 知识库、`popup_guard`、`visual`、`smart_wait` |
| **Phase 4 · 展示包装** | 面试级展示 | 自愈看板 / 视频回放、README 完善、CI 增强 |

## 已达成结论（决策记录）

| # | 决策点 | 结论 | 理由 |
| --- | --- | --- | --- |
| D1 | 实施路径 | 纵向切片优先 | 先证明价值；Phase 1 不卡待定项 |
| D2 | LLM/VLM | Phase 1 不接，Phase 2 再定 | 避免选型阻塞最小闭环 |
| D3 | 演示对象 | 自建本地测试页（POM 化） | 可控、可复现、可无缝切换其他 POM 用例 |
| D4 | 知识库后端 | SQLite | 零依赖、可持久化、易演示 |
| D5 | 自愈集成方式 | (b) Fixture 骨架 + (a) 接口代理 + (c) 装饰器补充；不用 import 魔法 | 可开关、POM 无缝、可维护（R4） |
| D6 | 不确定时兜底 | use_fallback，无备用则 fail；pause 仅交互模式 | CI 友好 + 人工兜底 |

## 待解决问题

- Phase 2 的 LLM/VLM 具体 provider（国产 / OpenAI 兼容）——Phase 1 收尾时评估。
- 启发式策略的属性选择与相似度打分算法——Phase 1 实现中确定。
- 自建测试页技术形态（纯静态 HTML / 本地起服务）——Phase 1 确定。
- `HealingPage` 代理需覆盖哪些 Page/Locator API 面（按需最小集 vs 全量 `__getattr__` 透传）——Phase 1 确定。
- 视觉定位的控件画像方案——Phase 3。

## 下一步计划（Phase 1 拆解）

1. 自建本地测试页并 **POM 化**（含可切换的"正常 / 失效"定位器 + 一个可注入弹窗）。
2. `engine/`：实现 `HealingPage` / `HealingLocator` 接口兼容代理（`locator(fallback=, description=)`）。
3. `conftest.py`：page 增强 fixture + `--selfheal/--no-selfheal` 开关（关 = 原生 Page）。
4. `collect/collector.py`：截图 + DOM 采集（网络 / trace 后置）。
5. `agent/strategies/heuristic.py`：多属性组合匹配 + 相似度打分。
6. `agent/orchestrator.py`：串通闭环（采集 → 启发式 → 重试 → 记录）+ 不确定兜底（D6）。
7. 测试：单测（`-m unit`）+ 集成测试（`-m e2e`）覆盖 ① 自愈成功 ② 关闭=原生行为 ③ 兜底触发。
8. `reporting`：记录一次真实修复，Allure 可见。
