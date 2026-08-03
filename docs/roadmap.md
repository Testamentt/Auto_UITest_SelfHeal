# 项目路线图（Roadmap）

> **活文档**：随进展同步更新，不是快照（见 `RULE.md` R5）。
> 最后更新：2026-08-04 · 当前阶段：**Phase 3 已完成，准备进入 Phase 4**
> 关联文档：Phase 3 评审 `docs/reviews/2026-08-04-phase3-retrospective.md` · 优化 TODO `docs/TODO.md`

## 当前目标

**Phase 4 —— 证据与指标（据 Phase 3 评审调整）**：先跑通真实模型并留存证据（T2）、做出自愈指标看板（T3）、修策略短路（T1）与二次自愈（T4），再做展示包装（视频回放 / CI / README）。详见 `docs/TODO.md`。
Phase 1（最小闭环）、Phase 2（AI 大脑）、Phase 3（沉淀与进阶）均已完成并通过全量测试。

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
| **Phase 3 · 沉淀与进阶** | 知识库持久化 + 弹窗 + 视觉 + 智能等待 | ✅ 完成 |
| **Phase 4 · 证据与指标** | 真实模型验证 + 自愈指标看板 + 加固（短路/二次自愈）+ 展示包装 | ⏳ 下一步 |

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
| D10 | 知识库后端形态 | KnowledgeBackend 接口 + 内存/SQLite 双实现 + factory 按配置选择；DOM 指纹（可交互元素稳定定位器排序哈希）参与检索择优 | 可切换、可持久化、同结构页面复用更可靠 |
| D11 | 弹窗处理 | PopupGuard 知识优先（弹窗特征库）+ 关闭按钮启发式识别，成功后沉淀特征；动作超时先清弹窗再走自愈 | 直击"被遮挡"类失败，通过率卖点 |
| D12 | 智能等待 | wait_until_stable 先可见后要求 bounding_box 连续 stable_ms 不变；POM 显式调用（可选增强） | 减少加载抖动误判，不改变默认行为 |
| D13 | 视觉定位 | OpenAICompatibleVLM 走 DashScope 兼容端点（qwen3-vl-flash）；候选集护栏（VLM 只能从真实候选中选）；key 走 `DASHSCOPE_API_KEY` 环境变量 | 复用 OpenAI 兼容机制；防幻觉；密钥参数化不入库 |

## 待解决问题

- **真实 VLM 校准**：视觉冒烟（`-k visual`）需 `pip install openai` + 设置 `DASHSCOPE_API_KEY` 环境变量后运行，校准 qwen3-vl-flash 的识别准确率与置信度。
- **真实 LLM 校准**：语义/诊断冒烟（`-k llm_smoke`）需 `OPENAI_API_KEY`（或改为 DashScope 文本模型）后运行。
- 知识库相似度检索目前为"精确 selector + 指纹择优"，向量/模糊检索可视需要增强。
- 弹窗特征签名基于文本归一化，结构指纹（DOM 结构哈希）可视需要增强。
- 智能等待目前 POM 显式调用；是否默认融入动作前置等待可视实测决定。

## 下一步计划（Phase 4 拆解，按 `docs/TODO.md` 优先级）

1. **T1 策略短路**：达到早接受阈值即停，省 LLM/VLM 调用（`orchestrator._best_candidate`）。
2. **T2 真实模型验证 + 证据留存**：配 key 跑 semantic/visual 冒烟，留存真实命中素材（🔴 需 API key）。
3. **T3 自愈指标看板**：自愈成功率 / 策略分布 / 根因分布 / 通过率对比，纯 HTML。
4. **T4 二次自愈 / 缓存验证**：修复选择器失效时重新自愈，知识缓存验证后再用。
5. 展示包装：视频回放（e2e 录 trace/视频）、CI 产物上传、README 打磨。
