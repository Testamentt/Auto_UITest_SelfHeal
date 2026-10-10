# 架构设计

> **定位**：AutoAiSelfHeal 的架构与**关键设计决策**（实现细节以 `src/selfheal/` 为准）。
> **状态**：技术文档（事实源，参与文档双轨）
> **最后更新**：2026-10-08 · 关联：[README.md](../README.md) 项目展示 · [roadmap.md](roadmap.md) 决策编号 D1–D17 · [TODO.md](TODO.md) 任务清单

AutoAiSelfHeal 的核心是一个「感知 → 诊断 → 决策 → 修复」的智能自愈闭环，架设在 Playwright 执行引擎之上。

## 被测系统（管伊佳 ERP，jshERP）

自愈框架的**正式被测对象**是 [jshERP](https://github.com/jishenghua/jshERP)（Vue3 + Ant Design Vue 前端 + Spring Boot 后端），
早期自建静态演示页（`tests/e2e/pages/demo_page.html`）保留为最小演示与单测夹具。**框架四层零改动**——迁移只发生在测试层（验证 D5 插件化）。

- **配置**：`src/selfheal/config.py` 的 `SutConfig`（前端 `sut.base_url` / 后端 `sut.api_base_url` / 凭证经 `*_env` 参数化，明文不入库）；marker `erp`（CI 排除，本地跑）。
- **登录与数据**：`tests/e2e/api/erp_client.py` 标准库客户端（登录 password 需 MD5、鉴权头 `X-Access-Token`、商品 / 供应商造数与清理）；
  UI 用例经 `erp_page` fixture（租户账号 UI 登录态 + `HealingPage` 自愈开启）取得被测页面。
- **自愈演示**：商品管理真实页面上注入"前端改版"（运行时改输入框 id）→ 旧定位器失效 → 多策略自愈重定位 → 保存落库 → API 断言 + 数据清理。
- **勘测结论**（2026-08-31）：验证码已关闭；ERP 自带 intro.js 引导遮罩会遮挡操作（测试基建 `dismiss_intro` 移除）；
  antd 弹窗不销毁（DOM 残留）——详见 [TODO.md](TODO.md) 的 T23 踩坑记录。
- **本地无 ERP 时**：`erp_api` / `erp_page` fixture 检测到凭证缺失或服务不可达即 `pytest.skip`（不再 ERROR）。

## 分层

```
┌──────────────────────────────────────────────────────────────────────┐
│ 展示层 reporting/     Allure 桥 · 自愈看板 · 修复审计 · 成本与指标      │
├──────────────────────────────────────────────────────────────────────┤
│ AI 自愈 Agent agent/  大脑                                          │
│   orchestrator 编排 │ context 组装 │ fix_generator 提案 │ persistence 阈值路由 │
│   diagnose 规则式 · diagnose_llm LLM 精判 │ strategies/ 启发式·语义·视觉 │
│   dom/ 解析·指纹·候选 │ confidence 置信度归一化                       │
│   支撑：llm/ 模型抽象（LLM+VLM，provider 无关）· knowledge/ 知识库       │
├──────────────────────────────────────────────────────────────────────┤
│ 数据采集层 collect/    截图 · DOM 快照 · 网络日志（限长 200）· 现场内联 trace │
├──────────────────────────────────────────────────────────────────────┤
│ 能力建设层 engine/     Playwright 封装 · 自愈定位器 · 智能等待 · 弹窗处理  │
└──────────────────────────────────────────────────────────────────────┘
```

## 自愈闭环

由 `src/selfheal/agent/orchestrator.py` 编排（A1 重构后降为 Router + 组合根）：

1. **执行监控**：`engine/healing_locator.py` 的动作包装捕获定位超时 / 不可交互异常（`HealingPage.locator()` 与 HEALABLE 动作）。
2. **现场采集**：`collect/collector.py` 抓截图、DOM 快照、网络日志与现场内联 trace（`Scene.trace_path`），与 conftest 整用例 trace 互补。
3. **智能诊断**：`agent/diagnose.py` 规则式粗分（零成本恒跑）+ `agent/diagnose_llm.py` LLM 精判（根因白名单 5 值；模型不可用自动降级规则式）。
4. **多策略修复**：`agent/strategies/` 按配置顺序尝试；Phase 5 起调度链为
   **L1 `repair_key` 精确命中（硬短路）→ L2 启发式 → L3 语义向量检索（知识库）→ L4 视觉（VLM）**，
   语义策略 = "知识库向量检索优先 + LLM 语义定位兜底"。
5. **验证与沉淀**：`engine/` 重试原步骤；成功后经 `agent/persistence.py` 把「原定位器 → 新定位器 → 置信度」写入 `knowledge/`
   （B1：先暂存、重试成功才落库；二次自愈会显式丢弃被替换的暂存）。
6. **报告与审计**：`reporting/hooks.py` 记录全过程，供 Allure 附件 / HTML 看板 / 成本指标展示。

## 关键设计决策

- **知识库优先**：调 LLM 前先查 `knowledge/` 命中的修复案例 / 弹窗特征（L1 → 旧式指纹 → L3 语义），降本增效。
- **provider 无关**：模型调用经 `llm/base.py` 抽象 + `llm/registry.py` 注册 + `llm/factory.py` 构建；业务代码不 import 具体 SDK，
  切 provider 只改 `config/settings.yaml`（**`base_url` / `model` 必须与 key 所属平台一致**）。
- **策略可插拔**：策略继承 `agent/strategies/base.py`，由 `fix_generator` 按 `strategy_order` 与置信度 / 成本调度。
- **策略短路（T1）**：某策略置信度达 `early_accept_threshold` 即采纳，不再调用后续更贵策略。
- **置信度归一化（T5）**：`agent/confidence.py` 可插拔校准注册表（`CALIBRATORS`），各策略产出统一到"采纳概率"标尺；
  按策略独立阈值（`strategy_thresholds` / `strategy_early_accept`）裁决，缺省回退全局。
- **双来源交叉校验（T8）**：`agent/dom/parser.py` 同时用静态 HTMLParser 与 Playwright 原生查询；策略链优先原生候选、静态兜底，
  两来源口径漂移记 warning 进 `Scene`。
- **护栏与隔离**：知识复用（L1 / L3）前验证候选 selector 仍真实存在（失效转人审清单）；单个策略内部异常不中断策略链（跳过 + 记日志）；
  `HealingPage.close()` / `SelfHealOrchestrator.close()` 释放**自建**资源，**注入的资源由注入方关闭**（所有权标记 `owns_*`）。
- **模型层护栏（2026-10-08）**：超时 / 输出上限 / 重试次数 / 图片体积上限全部可配（`LLMConfig`、`VisionConfig`）；
  超限截图经 Pillow 降质 / 缩放转 JPEG（缺 Pillow 记 warning 后发原图）；异常分级为
  `FatalUnavailableError`（鉴权 / 参数，重试无用）与 `TransientUnavailableError`（限流 / 超时 / 5xx，可重试），
  统一继承 `UnavailableError` 以保持既有降级契约；错误信息带 provider / model / 端点主机（**不含密钥**）。
- **弹窗关闭的作用域与信号分级（2026-10-08）**：知识命中的关闭点击**限定在弹窗容器内**；
  候选按"强信号（整串等于关闭类标签）优先、弱信号（子串命中，如"关闭订单"）兜底"分级，降低误点业务控件风险。
- **配置集中**：`src/selfheal/config.py` 用 pydantic 统一加载与严格校验（`extra='forbid'` 全层下发）；
  密钥只存环境变量名，`.env` 已 gitignore。

## 预期与风险（生产使用必读）

自愈是**提效助手**，不是"无人值守的自动改码机"。落地前对齐以下边界：

| 点 | 说明 | 本项目对应 |
| --- | --- | --- |
| **默认不自动改库** | AI 修复建议须人审或走闸门 | 运行时自愈只改"本次执行的定位"并沉淀知识库；写回代码走 `healing.fix_proposals` 输出「原→新」PR 化建议（`reporting/fix_proposals.py`，`applied=false`）+ CI 草稿 PR，人审后合入 |
| **作用域风险** | 自动化"点错元素"代价高 | 弹窗关闭限定在容器内 + 强弱信号分级；策略候选必须来自页面真实候选集（防幻觉护栏） |
| **多模态成本** | 视觉策略要截图并"看图"，按图计费 | visual 排末位 + T1 早接受短路 + 知识库优先（L1/L3 少调模型）；`HealingReporter.cost_summary()` 统计调用与估算费用（T17） |
| **flaky** | 偶发变绿 ≠ 自愈成功 | `HealingRecord.verified` 区分真自愈 vs flaky；指标与看板分开统计（T16，口径与 `success` 取交集） |
| **高风险页** | 支付 / 强授权 / 审计页误改代价高 | `healing.exclude_url_patterns` 命中即不触发自愈；`healing.dry_run` 仅报告不执行（T13 / T14） |
| **闭环** | 须有闸门与人审习惯 | 见下节 |
| **模型不可用** | 无 key / 端点错配时能力静默消失 | 降级保留（诊断退规则式、语义 / 视觉跳过），但**每个降级点记 warning 说明原因**（2026-10-08 起） |

### 闸门与人审

- **运行时自愈**：仅在本次执行内换定位器重试，成功则沉淀知识库——不改源码。
- **修复写回代码**：生成 PR 化建议清单（Markdown + JSON，`applied=false`），人确认后合入。
- **高风险页豁免**：`healing.exclude_url_patterns`（URL glob）；`healing.dry_run` 返回 `proposed_selector` 供人审。
- **成本闸门**：知识优先 + T1 早接受阈值约束 LLM/VLM 调用；T17 看板统计调用次数与估算费用。
- **人审标记入口**：`scripts/verify_case.py --list / --repair-key <rk>` 把知识库案例标记为 `is_verified`
  （L3 "sim > 0.92 且 verified 自动采纳"的信任开关）。

### 已落地 vs 仍规划

**已落地**：Phase 1–5 全部能力；T11 现场内联 trace；T13–T17 风险控制；T18 Allure 桥 + GitHub Pages 发布；
T19 通知基建（cron 未启用）；T20 A/B 对比实证；T21 xdist 并行（分片聚合 + SQLite WAL）；
T22 草稿 PR；T23 ERP 迁移；**2026-10-08 加固**（H1–H6 / M1–M16：模型层护栏与异常分级、弹窗作用域、
资源所有权、暂存与幂等窗口、知识库双后端语义对齐、CI 门禁补齐、人审入口脚本）。

**仍规划**（明细见 [TODO.md](TODO.md) 与 [backlog/low-priority.md](backlog/low-priority.md)）：
engine / collect 反向依赖的分层重构（M10）；语义 LLM 段的 L2 交叉校验（backlog R5）；
iframe / Shadow DOM 自愈评估 spike；自愈指标跨运行时间序列；T19 定时回归 cron；T20 A/B 实证报告与 T21 `-n 2` e2e 收官。

## 已定选型与遗留 TBD

**已定**：

- LLM：OpenAI 兼容客户端，默认 `deepseek-v4-flash` @ `https://api.deepseek.com`，key 走 `OPENAI_API_KEY`
  （`LLMConfig` 覆盖，本机实际指向 CommandCode 网关——见 `config/settings.yaml`）。
- VLM：**本项目自 2026-10-10 起改用 CommandCode 网关的 `deepseek/deepseek-v4.1-flash`**（实测支持图片输入，见决策 D18），
  key 走 `COMMANDCODE_API_KEY`（与 LLM 共用同一把）；候选护栏防幻觉；`timeout_s` / `max_tokens` / `max_image_bytes` 可配。
  代码默认值仍保留通义 `qwen3-vl-plus` + DashScope **公共** compatible-mode 端点作为可移植基线（备选 qwen3.8-flash）。
- 知识库：SQLite（`knowledge/sqlite_store.py`，WAL + `busy_timeout`）与内存双实现；DOM 指纹 + 页面指纹参与检索择优；
  向量为本地确定性 n-gram（`llm/embedding.py::NgramEmbedding`，`embedding_version` 含维度）。
- 报告：Allure（`reporting/allure_bridge.py` 轻量桥）+ 自研 HTML 看板；CI 发布 GitHub Pages。

**遗留 TBD**：

- 视觉定位的控件画像（当前为候选集选择）。
- 语义向量 v2（fastembed 本地模型），规模大时评估 sqlite-vec / Chroma。
- 分层重构（M10）：装配点从 `engine/` 上移至独立插件层，`agent/dom/` 下沉中立包。
