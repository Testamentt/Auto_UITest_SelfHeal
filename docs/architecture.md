# 架构设计

AutoAiSelfHeal 的核心是一个「感知 → 诊断 → 决策 → 修复」的智能自愈闭环，架设在 Playwright 执行引擎之上。

## 分层

```
┌─────────────────────────────────────────────────────────────┐
│  展示层 reporting/                                            │
│  Allure 报告 · 自愈看板 · 修复审计 · 视频回放（Playwright Trace）│
├─────────────────────────────────────────────────────────────┤
│  AI 自愈 Agent 层（大脑）agent/                                │
│  orchestrator 编排闭环 │ diagnose 诊断 │ strategies/ 多策略修复 │
│  支撑：llm/ 模型抽象（provider 无关） · knowledge/ 知识库       │
├─────────────────────────────────────────────────────────────┤
│  数据采集层 collect/                                          │
│  页面截图 │ DOM 快照 │ 网络日志（C5，限长缓存）                │
│  trace 采集内联：规划中（录制已由 conftest --trace-healing 完成）│
├─────────────────────────────────────────────────────────────┤
│  能力建设层（执行引擎）engine/                                 │
│  Playwright 封装 │ 自愈定位器 │ 智能等待 │ 弹窗处理            │
└─────────────────────────────────────────────────────────────┘
```

## 自愈闭环

由 `agent/orchestrator.py` 编排：

1. **执行监控**：`engine/` 执行步骤并捕获定位失败/超时/不可交互异常。
2. **现场采集**：`collect/collector.py` 抓取截图、DOM 快照、网络日志、trace。
3. **智能诊断**：`agent/diagnose.py` 规则式粗分（零成本恒跑）+ `agent/diagnose_llm.py` LLM 精判（白名单 5 值；模型不可用时降级规则式）。
4. **多策略修复**：`agent/strategies/` 按配置顺序尝试 启发式 → 语义 → 视觉。Phase 5 起调度链为 **L1 精确指纹（`repair_key` 硬短路）→ L2 启发式 → L3 语义向量检索（知识库命中）→ L4 视觉（VLM）**，语义策略升级为"知识优先的向量检索 + LLM 兜底"。
5. **验证与沉淀**：重试原步骤；成功则把「原定位器 → 新定位器 → 置信度」写入 `knowledge/`。
6. **报告与审计**：`reporting/hooks.py` 记录全过程。

## 关键设计决策

- **知识库优先**：调 LLM 前先查 `knowledge/` 命中的修复案例/弹窗特征，降本增效。
- **provider 无关**：所有模型调用经 `llm/base.py` 抽象 + `llm/registry.py` 注册，业务代码不直接依赖具体 SDK。
- **策略可插拔**：修复策略继承 `strategies/base.py`，由 orchestrator 按置信度/成本调度，顺序可配置。
- **策略短路（T1）**：某策略置信度达 `early_accept_threshold` 即采纳，不再调用后续更贵的策略（省 LLM/VLM）。
- **护栏与隔离（2026-08-15 Code Review 加固）**：知识复用（L1/L3）均验证候选 selector 仍真实存在（失效转人审清单，不白用缓存）；单个策略内部异常不中断策略链（跳过 + 记日志）；`HealingPage.close()` / `SelfHealOrchestrator.close()` 释放自建资源（注入的资源由注入方负责）。
- **配置集中**：`config.py` 用 pydantic 统一加载校验；密钥仅存环境变量名（`.env` 已 gitignore，绝不入库）。

## 预期与风险（生产使用必读）

自愈是**提效助手**，不是"无人值守的自动改码机"。落地前必须对齐以下预期与边界：

| 点 | 说明 | 本项目对应 |
| --- | --- | --- |
| **默认不自动乱合库** | AI 的修复建议须人审或走闸门，不应未经确认就改测试代码/提交 | 运行时自愈只改"本次执行的定位"并沉淀知识库；**把修复写回 POM/代码须人审**——`healing.fix_proposals` 输出「原→新」PR 化建议清单（`reporting/fix_proposals.py`，不自动改库） |
| **多模态成本** | 视觉策略要截图并"看图"，按图计费 | visual 排在策略末位 + T1 早接受短路 + 知识库优先（L1/L3 优先命中，尽量少调 LLM/VLM）；**成本看板**：`HealingReporter.cost_summary()` 统计 LLM/VLM 调用并估算费用（T17） |
| **flaky** | 偶发变绿 ≠ 自愈成功，可能只是抖动 | `HealingRecord.verified` 区分**真自愈 vs flaky 侥幸通过**：修复后原定位器仍失效=真修复，已恢复=flaky；看板与指标按此区分（T16） |
| **高风险页** | 支付 / 强授权 / 审计类页面，误改代价高 | `healing.exclude_url_patterns` 命中即**不触发自愈**；或 `healing.dry_run` **仅报告不执行**（T13/T14） |
| **闭环** | 自愈须有闸门与人审习惯，形成可控闭环 | 见下方「闸门与人审」 |

### 闸门与人审（闭环的关键）

- **运行时自愈**（已实现）：仅在本次执行内换定位器重试，成功则沉淀知识库——不改源码、风险可控。
- **修复写回代码**（已落地）：把"原定位器 → 新定位器"批量应用到 POM/测试代码时，生成 **PR 化建议清单**（`healing.fix_proposals` → `reporting/fix_proposals.py`，Markdown+JSON，`applied=false` 不自动改库），**人确认后合入**。
- **高风险页豁免**（已落地）：`healing.exclude_url_patterns` 按 URL glob 直接排除；`healing.dry_run` 对不确定页面"仅报告不执行"，返回 `proposed_selector` 供人审。
- **成本闸门**（已落地）：L1/L3 知识优先 + T1 早接受阈值约束 LLM/VLM 调用；T17 成本看板统计调用次数与估算费用。

### 已落地 vs 仍规划（见 `docs/TODO.md`）

**已落地（Phase 4/5）**：视频回放（Playwright Trace：`context.tracing` 录制 → `playwright show-trace` 回放，`--trace-healing` 触发）、风险控制 T13–T17（高风险页豁免 / dry-run / 修复写回人审 / flaky 区分 / 成本看板）、知识库语义化（L1 精确命中 + L3 向量检索）、**Allure 报告增强（T18，决策 D15）**：`reporting/allure_bridge.py` 轻量桥（`_HAS_ALLURE` 单点依赖探测，未装全 API no-op）——环境页（environment.properties）+ marker→标签（healing>e2e>unit 取唯一 feature）+ 证据附件（自愈记录 JSON 含 `verified_by_selector_exists`、trace zip）；CI `publish` job 合并 unit/e2e results 发布 **GitHub Pages**（gh-pages 分支，含历史趋势，仅 main 触发）。

**仍规划**：
- **trace 现场内联已落地（T11，2026-08-28）**：采集器 `SceneCollector._try_inline_trace` 在外层录制中（`--trace-healing` / `browser.trace`）把当前片段导出为独立现场 trace（`trace_dir/inline-trace-<uuid>.zip`，可 `playwright show-trace` 回放），填入 `Scene.trace_path` 并恢复录制；未录制不擅自启动（占位为空）。conftest 整用例录制（`context.tracing` → `reports/traces/trace-<test>.zip`）与现场 trace 职责互补、互不冲突。网络日志（C5）已实现：`Scene.network_logs` 随现场带出近期请求/响应（限长 200 条防膨胀）。

## 已定选型与遗留 TBD

**已定**：
- LLM：DeepSeek（OpenAI 兼容端点，`deepseek-v4-flash`），key 走 `OPENAI_API_KEY`。
- VLM：通义 `qwen3-vl-flash`（DashScope 兼容端点），key 走 `DASHSCOPE_API_KEY`；候选护栏防幻觉。
- 知识库后端：SQLite（`knowledge/sqlite_store.py`），DOM 指纹 + 页面指纹（page_fingerprint）参与检索择优。
- 知识库语义化（Phase 5 A）：本地确定性 n-gram 哈希 TF 向量（`llm/embedding.py::NgramEmbedding`，零 API 费用）+ numpy 余弦；L1 `repair_key` 精确命中 + L3 语义向量检索；`embedding_version` 含维度（如 `v1-ngram-512`，调整 dim 自动换桶防维度错配）平滑迁移；升级路径 v2 = fastembed 本地模型（如 bge-small-zh）。

**遗留 TBD**：
- 视觉定位的控件画像（当前为候选集选择）。
- 语义向量 v2 落地（fastembed 本地模型），规模大时评估 sqlite-vec / Chroma。
