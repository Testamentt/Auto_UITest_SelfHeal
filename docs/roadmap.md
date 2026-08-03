# 项目路线图（Roadmap）

> **活文档**：随进展同步更新，不是快照（见 `RULE.md` R5）。
> 最后更新：2026-08-03 · 当前阶段：**Phase 2 已完成，准备进入 Phase 3**

## 当前目标

**Phase 3 —— 沉淀与进阶**：知识库持久化（SQLite）、弹窗自动处理、视觉定位（VLM）、智能等待。
Phase 1（最小闭环）与 Phase 2（AI 大脑）均已完成并通过全量测试。

## 关键约束

- 遵循 `RULE.md` R1–R6：先计划后写入、测试双覆盖（`-m unit` + `-m e2e`）、临时方案三步管理、文档五要素、计划收敛纪律。
- 模型调用一律经 `llm/` 抽象层；配置集中在 `config.py`（pydantic）。
- **默认配置（无 API key / 未装 openai）下行为与 Phase 1 完全等价**，LLM 能力全部优雅降级（诊断退规则式、语义策略被跳过）。
- 演示与集成测试基于自建 POM 化本地测试页。

## 架构决策（插件化自愈）

自愈能力是**挂件 / 插件**，不侵入 Playwright 框架本身。三项硬性要求：

1. **POM 无缝切换**：POM 类只依赖 `page` 接口；自愈包装与原生 `Page` 接口兼容，同一份 POM 代码开/关自愈都能跑。
2. **可开关、零侵入**：自愈由 pytest fixture 按 `settings.healing.enabled` + CLI `--selfheal/--no-selfheal` 提供。关闭时透传原生行为，框架零感知、零开销。
3. **兜底机制**：`locator(sel, fallback=备用selector, description=...)`。AI 不确定（置信度 < 阈值）时按 `healing.on_uncertain` 处理。

**集成方式（决策 D5）**：Fixture 注入为骨架 + 接口兼容代理做底层拦截 + 装饰器作可选补充；不采用 import 替换 / monkeypatch 魔法。

**兜底行为（决策 D6）**：`on_uncertain` 默认 `use_fallback`（优先人工备用定位器）；无备用则 fail；`pause` 仅交互模式可用。

## 整体路线

| 阶段 | 目标 | 状态 |
| --- | --- | --- |
| **Phase 1 · 最小闭环** | 端到端自愈跑通（启发式）+ 插件化骨架 | ✅ 完成 |
| **Phase 2 · AI 大脑** | LLM 智能诊断 + 语义定位 + 自愈看板 v0 | ✅ 完成 |
| **Phase 3 · 沉淀与进阶** | 知识库持久化 + 弹窗 + 视觉 + 智能等待 | ⏳ 下一步 |
| **Phase 4 · 展示包装** | 面试级展示（看板增强 / 视频回放 / CI 增强） | 待启动 |

## 已达成结论（决策记录）

| # | 决策点 | 结论 | 理由 |
| --- | --- | --- | --- |
| D1 | 实施路径 | 纵向切片优先 | 先证明价值；不卡待定项 |
| D2 | LLM/VLM | Phase 2 接入，OpenAI 兼容优先 | 避免选型阻塞最小闭环 |
| D3 | 演示对象 | 自建本地测试页（POM 化） | 可控、可复现、可无缝切换其他 POM 用例 |
| D4 | 知识库后端 | SQLite | 零依赖、可持久化、易演示 |
| D5 | 自愈集成方式 | Fixture 骨架 + 接口代理 + 装饰器补充；不用 import 魔法 | 可开关、POM 无缝、可维护（R4） |
| D6 | 不确定时兜底 | use_fallback，无备用则 fail；pause 仅交互模式 | CI 友好 + 人工兜底 |
| D7 | LLM 客户端形态 | 单一 OpenAI 兼容客户端（base_url+model 覆盖多 provider），openai SDK 惰性导入 | 切换 provider 只改配置；CI 无 openai 也能 import |
| D8 | DOM 公共能力 | 抽 `agent/dom.py`（解析 + 稳定定位器），heuristic/llm_io/semantic 共用 | 消除循环导入、重复与私有耦合（R4） |
| D9 | LLM 降级策略 | 不依赖 response_format；extract_json 容错 + 白名单；防幻觉护栏（selector 须真实存在） | 模型不稳定时闭环不中断 |

## 待解决问题

- 知识库持久化后端落地（SQLite schema / 相似度检索）——Phase 3。
- 弹窗特征库与自动关闭策略——Phase 3。
- 视觉定位（VLM）的控件画像方案——Phase 3。
- 真实 LLM provider 选型与 API key 配置（当前走 `OPENAI_API_KEY` 环境变量）——有 key 时跑 `-k smoke` 验证。

## 下一步计划（Phase 3 拆解）

1. `knowledge/`：SQLite 持久化后端 + DOM 指纹相似度检索 + 弹窗特征库 schema。
2. `engine/popup_guard.py`：弹窗识别与自动关闭（命中知识库优先）。
3. `agent/strategies/visual.py`：VLM 截图分析定位（经 `llm/` VisionClient 抽象）。
4. `engine/smart_wait.py`：基于 DOM 稳定度 / 网络空闲的自适应等待。
5. 测试：单测 + e2e 覆盖弹窗自愈、知识库命中复用、视觉定位（可 mock VLM）。
