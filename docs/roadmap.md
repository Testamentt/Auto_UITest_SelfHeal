# 项目路线图（Roadmap）

> **活文档**：随进展同步更新，不是快照（见 `RULE.md` R5）。
> 最后更新：2026-08-31 · 当前阶段：TODO 优化项 **T5–T11、T18 全部完成**（置信度归一化/动作前置等待/知识命中 e2e/原生解析交叉校验/模块归位/trace 内联/Allure 报告增强）
> 关联文档：评审 `docs/reviews/2026-08-15-code-review.md`（全量审查）· 优化 TODO `docs/TODO.md`

## 当前目标

**Phase 5 —— 知识库语义化（A）+ 风险控制（D）**：均已达成（2026-08-05）。
- **A 语义化**：本地确定性 n-gram 向量（A1）→ 存储/检索（A2）→ orchestrator 接线 L1/L3（A3）。
- **D 风险控制**：T13 高风险页豁免 / T14 dry-run / T15 修复写回人审 / T16 flaky 区分 / T17 成本看板。
Phase 4 展示包装（视频回放 / CI 产物 / README）、Phase 1–3 均已完成并通过全量测试。

**2026-08-15 Code Review 加固（P0–P2）**：全量审查后修复 4 个实证缺陷（DOM 解析 void 元素文本污染 / SQLite NULL 指纹去重失效 / 向量维度错配崩溃 / 策略链无异常隔离）+ 4 项护栏（L3 候选失效验证 / 弹窗特征 text-aria 投影 / 资源生命周期 close 链 / 上下文缓存 URL 键 + LRU），unit 200 passed、e2e 7 passed。

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
| **Phase 4 · 证据与指标** | 真实模型验证 + 自愈指标看板 + 加固（短路/二次自愈）+ 展示包装 | ✅ 完成 |
| **Phase 5 · 语义化与风险控制** | 知识库向量检索（越用越聪明）+ 风险控制（豁免/dry-run/人审/flaky/成本） | ✅ 完成 |

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
| D8 | DOM 公共能力 | 抽 `agent/dom.py` 公共工具（解析 + 稳定定位器），heuristic/llm_io/semantic 共用；现为 `agent/dom/` 子模块（A5 拆包：parser / selector_builder / fingerprint / extractor） | 消除循环导入、重复与私有耦合（R4） |
| D9 | LLM 降级策略 | 不依赖 response_format；extract_json 容错 + 白名单；防幻觉护栏（selector 须真实存在） | 模型不稳定时闭环不中断 |
| D10 | 知识库后端形态 | KnowledgeBackend 接口 + 内存/SQLite 双实现 + factory 按配置选择；DOM 指纹（可交互元素稳定定位器排序哈希）参与检索择优 | 可切换、可持久化、同结构页面复用更可靠 |
| D11 | 弹窗处理 | PopupGuard 知识优先（弹窗特征库）+ 关闭按钮启发式识别，成功后沉淀特征；动作超时先清弹窗再走自愈 | 直击"被遮挡"类失败，通过率卖点 |
| D12 | 智能等待 | wait_until_stable 先可见后要求 bounding_box 连续 stable_ms 不变；POM 显式调用（可选增强） | 减少加载抖动误判，不改变默认行为 |
| D13 | 视觉定位 | OpenAICompatibleVLM 走 DashScope 兼容端点（qwen3-vl-flash）；候选集护栏（VLM 只能从真实候选中选）；key 走 `DASHSCOPE_API_KEY` 环境变量 | 复用 OpenAI 兼容机制；防幻觉；密钥参数化不入库 |
| D14 | 知识库语义化 | 本地确定性 n-gram 哈希 TF 向量（v1，零 API 费用）+ numpy 余弦；L1 `repair_key` 精确命中硬短路 → L2 启发式 → L3 语义向量检索 → L4 VLM；存储 BLOB；按 page_fingerprint 分桶防跨页误配；采纳规则（sim>0.92 且 verified / 7 天新鲜 sim>0.80 自动，其余写人审清单）；v2 可升级 fastembed 本地模型 | 热路径不调 API embedding（延迟+成本失控）；ID 变化但文本/结构不变仍可命中；防污染 + 冷启动免人审 |
| D15 | Allure 报告增强 | **轻量桥**模式（`reporting/allure_bridge.py`，零侵入核心同 D5）：`_HAS_ALLURE` 单点依赖探测（未装全 API no-op）；环境页（environment.properties）+ marker→标签（优先级 healing>e2e>unit 取唯一 feature，dynamic API 于 autouse fixture 打点）+ 证据附件（自愈记录含 `verified_by_selector_exists`=复用 T16 布尔、trace zip）；CI `publish` job 合并 unit/e2e results → GitHub Pages 发布 + 历史趋势（gh-pages 分支，仅 main 触发）；不引入 allure.step 步骤树（避免核心感知 allure） | 报告是展示层插件不该侵入 agent；标签单 feature 防爆炸；历史趋势需要持久化分支；闭环过程以结构化附件呈现够用 |

## 待解决问题

- **真实模型冒烟已跑通（2026-08-28，`-k llm_smoke or visual_smoke`）**：
  - **VLM 校准（qwen3-vl-flash）**：demo 登录按钮视觉定位一次成功 → `[data-testid="submit-btn"]`，自报置信度 **0.921**（raw²≈0.848）；护栏（selector 真实存在 / 越界拒绝 / 重试 3 次）正常。
  - **LLM 校准（deepseek-v4-flash）**：语义定位直怼成功（绕开启发式早停）→ 同上 selector，自报置信度 **1.0**；完整自愈链路用例同步通过。**观测**：LLM 高自报段倾向满分级，T5 `shrink_self_reported`（raw²）对满分自报无收敛效果，对 0.9x 段有效——多场景数据沉淀后按段标定收缩口径。
  - 证据：`reports/evidence/semantic_result.json` / `visual_result.json`（reports/ 已 gitignore，不入库）。
- 语义向量 v1 为本地 n-gram（跨语言含中文较弱）；语义更强可升级 fastembed 本地模型（v2），向量列带 `embedding_version`（含维度）平滑迁移。
- 弹窗特征签名基于文本归一化，结构指纹（DOM 结构哈希）可视需要增强。
- 智能等待目前 POM 显式调用（`healing.action_wait` 已可按需开启为动作前置，默认关闭守 D12）；是否翻转默认可视实测决定。
- DOM 解析：静态 HTMLParser 与 Playwright 原生解析（T8）已双轨并存 + 交叉校验（不一致记 warning 进 Scene）；两轨一致口径暂稳，后续若页面结构复杂化可视交叉校验报告决定是否收敛为原生单轨。
- 采集器内联 trace（T11）已落地：录制中产出 `inline-trace-<uuid>.zip` 现场文件（`Scene.trace_path` 不再占位）；与 conftest 整用例录制互补。

## 下一步计划

**Phase 4（已完成存档）**：T1 策略短路 / T2 真实模型验证+证据 / T3 指标看板 / T4 二次自愈与缓存验证 / 展示包装（trace 回放 + CI 产物 + README）。

**Phase 5（已完成 2026-08-05）**：
1. ✅ **A 知识库语义化**（A1 本地 n-gram 向量 → A2 存储/检索 → A3 orchestrator 接线）：
   L1 `repair_key` 硬短路 + L3 语义向量检索进策略链，失败上下文三级回退提取，persist 富化指纹/向量。
2. ✅ **D 风险控制（T13–T17）**：高风险页豁免 / dry-run / 修复写回人审清单 / flaky 区分 / 多模态成本看板。

**后续可选（按 `docs/TODO.md`，T5–T11 全部完成 2026-08-28）**：
- 语义化 v2：fastembed 本地模型（如 bge-small-zh）替换 n-gram，语义更强仍本地推理；规模大时可迁 sqlite-vec / Chroma。
- T5 归一化补充：已初采真实数据点（LLM 语义 1.0 / VLM 视觉 0.921，2026-08-28 冒烟）；
  待多场景数据后为 LLM/VLM 自报段填数据标定函数（`agent/confidence.py`），
  当前仅保留可选 raw² 经验收缩（对 0.9x 段有效；满分自报无效，需按段标定）。
- T6 动作前置等待：如需成为默认行为，据实测决定翻转 `healing.action_wait.enabled` 默认值。
