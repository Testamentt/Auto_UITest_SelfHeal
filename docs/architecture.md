<!-- 本项目统一使用 UTF-8（无 BOM）编码。 -->
# 架构设计

> **定位**：AutoAiSelfHeal 的架构与**关键设计决策**（实现细节以 `src/selfheal/` 为准）。
> **状态**：技术文档（事实源，与实现同步）
> **最后更新**：2026-11-12 · 关联：[README.md](../README.md) 项目展示 · [decisions.md](decisions.md) 决策记录（D1–D24，含独立编号 D25）

AutoAiSelfHeal 的核心是一个「感知 → 诊断 → 决策 → 修复」的智能自愈闭环，架设在 Playwright 执行引擎之上。

## 被测系统（管伊佳 ERP，jshERP）

自愈框架的**正式被测对象**是 [jshERP](https://github.com/jishenghua/jshERP)（Vue3 + Ant Design Vue 前端 + Spring Boot 后端），
早期自建静态演示页（`tests/e2e/pages/demo_page.html`）保留为最小演示与单测夹具。**框架四层零改动**——迁移只发生在测试层（验证 D5 插件化）。

- **配置**：`src/selfheal/config.py` 的 `SutConfig`（前端 `sut.base_url` / 后端 `sut.api_base_url` / 凭证经 `*_env` 参数化，明文不入库）；marker `erp`（CI 排除，本地跑）。
- **登录与数据**：`tests/e2e/api/erp_client.py` 标准库客户端（登录 password 需 MD5、鉴权头 `X-Access-Token`、商品 / 供应商造数与清理）；
  UI 用例经 `erp_page` fixture（租户账号 UI 登录态 + `HealingPage` 自愈开启）取得被测页面。
- **自愈演示**：商品管理真实页面上注入"前端改版"（运行时改输入框 id）→ 旧定位器失效 → 多策略自愈重定位 → 保存落库 → API 断言 + 数据清理。
- **本地无 ERP 时**：`erp_api` / `erp_page` fixture 检测到凭证缺失或服务不可达即 `pytest.skip`（不再 ERROR）。

### 实测勘测结论（真实系统踩坑，2026-08-31 ~ 09-01）

以下都是接入 jshERP 时实测出来的，**不是文档推测**——换任何真实被测系统都会遇到同类问题，是判断"做过真实迁移还是只跑了 demo 页"的关键差别。

**角色语义（人工专家确认）**

| 账号 | 角色 | 能做 | 不能做 | 测试基建用途 |
| --- | --- | --- | --- | --- |
| 租户（如 `jsh`） | 业务数据管理员 | 编辑全部业务数据 | —— | **UI 测试与 API 造数/清理统一使用** |
| admin | 平台运维用户 | 配置平台菜单、创建租户 | **不能编辑任何业务数据** | **测试基建不得使用** |

> 教训：曾误用 admin 做 API 造数（后端未拦截但语义违规），已纠正为租户凭证统一。

**接入参数**：前端 `:3001`（Vue3 + Ant Design Vue，**非 Element-UI**）；后端 `:9999/jshERP-boot`（**非被测对象**，仅造数/清理）；
登录 `POST /user/login`，**password 需 MD5 后提交**（明文报 `user password error`）；鉴权头 `X-Access-Token`；验证码由被测环境关闭。

**UI 结构与陷阱**

1. **vue-router 异步跳转**：登录成功跳 `/dashboard/analysis`，必须 `wait_for_url`，立即断言 URL 会失败。
2. **内容区不在 iframe**：SPA 同文档渲染，可直接 goto 路由。
3. **antd 弹窗默认不销毁**：关闭后 DOM 残留（hidden），再次打开出现**两份同名 id** → 污染定位与自愈候选。
   对策：避免同页签反复开关弹窗，或用 `.ant-modal:visible` 限定。
4. **intro.js 新手引导遮罩拦截全页点击**（`root_cause=covered`），每账号首次登录触发。
   对策：`dismiss_intro` 做 **DOM 移除**；**不能**用 `display:none`（会影响祖先层隐藏时其他元素的可见性判定）。
5. **菜单为 JS 动态**（无 href）：不能 goto 直达未启用页面；当前实例未启用供应商页面。
6. **造数模式**：`add` 响应不带 id → `list`（`search` 参数为 **JSON 字符串**）反查 id → `deleteBatch?ids=` 清理
   （**单条 `delete` 实测 500**）。

**坑位速查**

| 症状 | 根因 | 对策 |
| --- | --- | --- |
| 登录报 `user password error` | 密码未 MD5 | 提交前 `hashlib.md5` |
| 点击被拦截 / `root_cause=covered` | intro.js 引导遮罩 | `dismiss_intro(page)` |
| 定位器匹配 2 个 / 自愈候选同名 | antd 弹窗 DOM 残留 | 单弹窗场景设计 / `.ant-modal:visible` |
| 断言 URL 仍是登录页 | vue-router 异步跳转 | `wait_for_url("**/dashboard/**")` |
| 删除接口 500 | 单条 delete 不可用 | 用 `deleteBatch?ids=` |

## 分层

```
┌──────────────────────────────────────────────────────────────────────┐
│ 展示层 reporting/     Allure 桥 · 自愈看板 · 修复审计 · 成本与指标      │
├──────────────────────────────────────────────────────────────────────┤
│ 装配层 plugin/         HealingPage（自愈 Page 代理）· 组装知识库与闭环  │
├──────────────────────────────────────────────────────────────────────┤
│ AI 自愈 Agent agent/  大脑                                          │
│   orchestrator 编排 │ context 组装 │ fix_generator 提案 │ persistence 阈值路由 │
│   diagnose 规则式 · diagnose_llm LLM 精判 │ strategies/ 启发式·语义·视觉 │
│   confidence 置信度归一化                                            │
│   支撑：llm/ 模型抽象（LLM+VLM，provider 无关）· knowledge/ 知识库       │
├──────────────────────────────────────────────────────────────────────┤
│ 中立层 contracts.py / dom/   DTO（FailureContext · HealOutcome）· DOM 解析/指纹/候选 │
├──────────────────────────────────────────────────────────────────────┤
│ 数据采集层 collect/    截图 · DOM 快照 · 网络日志（限长 200）· 现场内联 trace │
├──────────────────────────────────────────────────────────────────────┤
│ 能力建设层 engine/     Playwright 封装 · 自愈定位器 · 智能等待 · 弹窗处理  │
└──────────────────────────────────────────────────────────────────────┘
```

**依赖方向（D19 后固定，有 AST 护栏用例）**：只允许自上而下——`reporting`/`plugin` → `agent` →（`llm`/`knowledge`/`dom`/`contracts`）；
`engine`、`collect` 只依赖 `contracts`/`dom`/`config`，**import 闭包里不出现 `agent`/`knowledge`/`reporting`**
（实测 `sys.modules` 为空，见 `tests/unit/test_layering.py`）。

## 自愈闭环

由 `src/selfheal/agent/orchestrator.py` 编排（重构后降为 Router + 组合根）：

1. **执行监控**：`plugin/healing_page.py` 的 `HealingPage.locator()` 与 `engine/healing_locator.py` 的 HEALABLE 动作包装捕获定位超时 / 不可交互异常（D19 前 `HealingPage` 在 engine，装配依赖因此倒置）。动作前先做**快速探测**（`_probe_target()`，`healing.probe` 默认 2.5s）：目标不就绪即直接进闭环，不必等满 Playwright 默认超时（D22）；探测通过则走原生动作，不改变结论。
2. **现场采集**：`collect/collector.py` 抓截图、DOM 快照、网络日志与现场内联 trace（`Scene.trace_path`），与 conftest 整用例 trace 互补。
3. **智能诊断**：`agent/diagnose.py` 规则式粗分（零成本恒跑）+ `agent/diagnose_llm.py` LLM 精判（根因白名单 5 值；模型不可用自动降级规则式）。
4. **多策略修复**：`agent/strategies/` 按配置顺序尝试；Phase 5 起调度链为
   **L1 `repair_key` 精确命中（硬短路）→ L2 启发式 → L3 语义向量检索（知识库）→ L4 视觉（VLM）**，
   语义策略 = "知识库向量检索优先 + LLM 语义定位兜底"。
5. **验证与沉淀**：`engine/` 重试原步骤；成功后经 `agent/persistence.py` 把「原定位器 → 新定位器 → 置信度」写入 `knowledge/`
   （先暂存、重试成功才落库；二次自愈会显式丢弃被替换的暂存）。
6. **报告与审计**：`reporting/hooks.py` 记录全过程，供 Allure 附件 / HTML 看板 / 成本指标展示。

## 关键设计决策

> 编号化、可追溯的决策记录见 [decisions.md](decisions.md)；本节只保留设计层面的要点。

- **知识库优先**：调 LLM 前先查 `knowledge/` 命中的修复案例 / 弹窗特征（L1 → 旧式指纹 → L3 语义），降本增效。
- **provider 无关**：模型调用经 `llm/base.py` 抽象 + `llm/registry.py` 注册 + `llm/factory.py` 构建；业务代码不 import 具体 SDK，
  切 provider 只改 `config/settings.yaml`（**`base_url` / `model` 必须与 key 所属平台一致**）。
- **策略可插拔**：策略继承 `agent/strategies/base.py`，由 `fix_generator` 按 `strategy_order` 与置信度 / 成本调度。
- **策略短路**：某策略置信度达 `early_accept_threshold` 即采纳，不再调用后续更贵策略（前提：该候选已过自身阈值与唯一性校验；click / fill 等要求可交互的动作还须当前可交互，`set_input_files` 例外）。
- **置信度归一化**：`agent/confidence.py` 可插拔校准注册表（`CALIBRATORS`），各策略产出统一到"采纳概率"标尺；
  按策略独立阈值（`strategy_thresholds` / `strategy_early_accept`）裁决，缺省回退全局。
  **逐候选裁决**：每个候选先按自己策略的阈值过滤，合格者才参与"取最优"——高置信但未达自身阈值的候选
  不会遮蔽低置信但已达标的候选；被拒候选仍进人审建议出口，不丢审计信息。
- **双来源交叉校验**：`agent/dom/parser.py` 同时用静态 HTMLParser 与 Playwright 原生查询；策略链优先原生候选、静态兜底，
  两来源口径漂移记 warning 进 `Scene`。
- **护栏与隔离**：候选采纳与知识复用（L1 / L3）前验证 selector **唯一命中**（多命中会被 Playwright strict mode
  直接拒绝，属硬底线）；**可交互性**（visible / enabled）按动作区分作早接受门槛 / 同分 tie-break 信号而非淘汰条件——`set_input_files`
  对隐藏 `input[type=file]` 同样可用，且 click 等动作由 Playwright 自动等待，硬拒会误杀隐藏上传框与
  "暂不可见但稍后可见"的元素。单个策略内部异常不中断策略链（跳过 + 记日志）；
  `HealingPage.close()` / `SelfHealOrchestrator.close()` 释放**自建**资源，**注入的资源由注入方关闭**（所有权标记 `owns_*`）。
- **自愈触发延迟（2026-11-12）**：开启自愈时定位失败原本要等满 Playwright 默认超时（30s）才进闭环，
  单条失效步骤 +30s。现在动作前先做**快速探测**（`healing.probe.timeout_ms`，默认 2.5s）：
  目标不就绪即直接进自愈。探测通过则走原生动作；未通过则走自愈（与等满超时后同一条路径）。
  判定用 `wait_for()` 在预算内等待，不误杀慢加载元素；
  `count>1` 与 `disabled` 状态**不**提前判死（前者是编程错误、后者可恢复，提前判会掩盖/误杀）。
  实测（`python scripts/benchmark_probe.py` 可复现，同机、唯一变量为探测开关）：

  | 口径 | 关闭探测 | 开启探测 | 降幅 |
  | :--- | ---: | ---: | ---: |
  | 单条「失效定位器 → 自愈」用例 | 34.89s | 7.17s | -79% |
  | 整个 e2e 套件（`--full`） | 447.97s | 112.33s | -75% |

  两种口径含不同固定开销（pytest / 浏览器启动），**不可互相换算**；各自与自己的闭合比才有效。

  **代价与边界（勿读成"零影响"）**：
  - 链式重放使用同一预算作为短超时——**慢渲染的链**（>2.5s 才就绪）会从"等 30s 后成功"
    变为"2.5s 判超时 → 转第二次修叶子"，即多跑一轮闭环（可能含额外 LLM/VLM 费用）。
    这是**用"更快的失败判定"换"可能的额外自愈"，不是纯收益**；
  - 探测超时本身会触发完整闭环：正常但慢的页面会多一次自愈尝试（有兜底，不会直接失败，
    但会产生成本与日志）。对延迟敏感的团队可调大 `probe.timeout_ms`，
    或 `probe.enabled: false` 恢复旧行为（零回归）。
- **L3 阈值外置（D23，2026-11-12）**：语义检索的四个阈值从模块常量改为 `knowledge.semantic` 配置
  （`min_sim` / `fresh_sim` / `verified_sim` / `fresh_days`，默认 0.75 / 0.80 / 0.92 / 7），经 frozen dataclass
  `L3Thresholds` 注入 `SemanticStrategy`，可按被测系统的真实分布标定而不必改代码发版；
  加载期校验 `0 <= min_sim <= fresh_sim <= verified_sim <= 1`，倒挂直接 `ValueError`
  （"更严的信任条件反而更易采纳"属静默错误）。不传配置时逐值等于原模块常量（零回归）。
  边界：阈值可标定，但**分桶粒度仍按 `page_fingerprint`**（见下节「边界与下一步」）。
- **知识库运维 CLI（D24，2026-11-12）**：`scripts/knowledge_admin.py` 提供 `--stats / --prune / --dedupe / --export`；
  写操作**默认 dry-run**（需显式 `--apply`），`--prune` 只删「未人审 + 命中 0 次 + 超龄」的案例，
  **已人审或被复用过的永不自动删除**。只动知识库数据、不改代码与定位器；连接沿用 WAL + `busy_timeout=30000`，
  避免与运行中的用例抢锁。人审标记仍由 `scripts/verify_case.py` 负责（职责分离）。
- **模型层护栏（2026-10-08）**：超时 / 输出上限 / 重试次数 / 图片体积上限全部可配（`LLMConfig`、`VisionConfig`）；
  超限截图经 Pillow 降质 / 缩放转 JPEG（缺 Pillow 记 warning 后发原图）；异常分级为
  `FatalUnavailableError`（鉴权 / 参数，重试无用）与 `TransientUnavailableError`（限流 / 超时 / 5xx，可重试），
  统一继承 `UnavailableError` 以保持既有降级契约；错误信息带 provider / model / 端点主机（**不含密钥**）。
- **弹窗关闭的作用域与信号分级（2026-10-08）**：知识命中的关闭点击**限定在弹窗容器内**；
  候选按"强信号（整串等于关闭类标签）优先、弱信号（子串命中，如"关闭订单"）兜底"分级，降低误点业务控件风险。
- **模型调用韧性（D25，2026-11-10）**：瞬态故障（429 / 超时 / 5xx / 连接失败）按 `llm.retry` 指数退避重试，
  连续 `circuit_breaker.failure_threshold` 次逻辑调用失败后打开熔断（冷却后半开单探针）；熔断期间抛
  `CircuitOpenError`（`UnavailableError` 子类）直接走既有降级链。Fatal（401/403/400/404）不重试、不参与熔断计数。
  **SDK 自带重试固定关闭**（`llm/factory.py` 传 `max_retries=0`），避免本层与 SDK 叠加成 3×3=9 次付费调用；
  熔断状态为单进程（xdist 各 worker 独立）；重试 / 熔断计数进 `reporter.stats`，成本按**真实尝试次数**估算。
- **候选裁决与定位器唯一性**：修复两个审计复现的缺陷——①阈值裁决改为「逐候选按自身阈值过滤后再比」
  （此前先按原始置信度取最大、再用赢家自己的阈值裁决，会让高置信未达标候选遮蔽低置信达标候选，导致本可修复却判失败）；
  ②`selector_exists` 从"存在即可"升级为"**唯一命中**"，统一用于候选采纳 / 知识复用 / `verified` 判定
  （非唯一 `text="提交"` 在重试时抛 strict-mode violation——非 TimeoutError、不触发自愈，且脏案例会写进知识库）。
  可交互性另由 `selector_actionable` / `action_requires_actionability` 按动作取舍：**置信度优先**
  （不拿"当前可交互"压过高置信候选——可交互诱饵点错代价更高，D6 宁可失败不错误通过），
  可交互性只作早接受门槛与同分 tie-break，且不可交互时仍采纳并记日志（交 Playwright 自动等待）。
  决策与验证口径见 [decisions.md](decisions.md) D20 / D21。
- **配置集中**：`src/selfheal/config.py` 用 pydantic 统一加载与严格校验（`extra='forbid'` 全层下发）；
  密钥只存环境变量名，`.env` 已 gitignore。

## 预期与风险（生产使用必读）

自愈是**提效助手**，不是"无人值守的自动改码机"。落地前对齐以下边界：

| 点 | 说明 | 本项目对应 |
| --- | --- | --- |
| **默认不自动改库** | AI 修复建议须人审或走闸门 | 运行时自愈只改"本次执行的定位"并沉淀知识库；写回代码走 `healing.fix_proposals` 输出「原→新」PR 化建议（`reporting/fix_proposals.py`，`applied=false`）+ CI 草稿 PR，人审后合入 |
| **作用域风险** | 自动化"点错元素"代价高 | 弹窗关闭限定在容器内 + 强弱信号分级；策略候选必须来自页面真实候选集（防幻觉护栏）且**唯一命中**（多命中直接拒绝，不交给 strict mode 崩）；可交互性按动作作择优信号 |
| **多模态成本** | 视觉策略要截图并"看图"，按图计费 | visual 排末位 + 早接受短路 + 知识库优先（L1/L3 少调模型）；`HealingReporter.cost_summary()` 统计调用与估算费用 |
| **flaky** | 偶发变绿 ≠ 自愈成功 | `HealingRecord.verified` 区分真自愈 vs flaky（原定位器仍**不可用**——不存在 / 多命中——才算真修复；隐藏 / 置灰属瞬态，不算）；指标与看板分开统计（口径与 `success` 取交集） |
| **高风险页** | 支付 / 强授权 / 审计页误改代价高 | `healing.exclude_url_patterns` 命中即不触发自愈；`healing.dry_run` 仅报告不执行 |
| **闭环** | 须有闸门与人审习惯 | 见下节 |
| **模型不可用** | 无 key / 端点错配时能力静默消失 | 降级保留（诊断退规则式、语义 / 视觉跳过），但**每个降级点记 warning 说明原因**（2026-10-08 起） |
| **模型端点限流 / 故障** | 瞬态抖动导致自愈误判失败；持续故障让每条用例空转重试（CI 被拖死） | D25：仅瞬态重试（指数退避）+ 三态熔断；熔断后毫秒级降级。模拟演示：15 次自愈等待 31.53s → 6.31s（-80%，`scripts/resilience_demo.py`） |

### 闸门与人审

- **运行时自愈**：仅在本次执行内换定位器重试，成功则沉淀知识库——不改源码。
- **修复写回代码**：生成 PR 化建议清单（Markdown + JSON，`applied=false`），人确认后合入。
- **高风险页豁免**：`healing.exclude_url_patterns`（URL glob）；`healing.dry_run` 返回 `proposed_selector` 供人审。
- **成本闸门**：知识优先 + 早接受阈值约束 LLM/VLM 调用；成本看板统计调用次数与估算费用。
- **人审标记入口**：`scripts/verify_case.py --list / --repair-key <rk>` 把知识库案例标记为 `is_verified`
  （L3 "sim > verified_sim 且 verified 自动采纳"的信任开关；阈值由 `knowledge.semantic` 配置，默认 0.92）。
- **知识库运维入口**：`scripts/knowledge_admin.py --stats / --prune / --dedupe / --export`。
  写操作**默认 dry-run**（需显式 `--apply`）；`--prune` 只删「未人审 + 从未命中 + 超龄」的案例，
  已人审或被复用过的**永不**自动删除。

### 已落地与边界

**已落地**：Phase 1–5 全部能力；现场内联 trace；风险控制（高风险页豁免 / dry-run / 人审 / flaky 区分 / 成本看板）；
Allure 桥 + GitHub Pages 发布；通知基建（定时回归 cron 未启用）；A/B 对比演示；xdist 并行（分片聚合 + SQLite WAL）；
草稿 PR；管伊佳 ERP 迁移；**2026-10-08 加固**（模型层护栏与异常分级、弹窗作用域、资源所有权、暂存与幂等窗口、
知识库双后端语义对齐、CI 门禁补齐、人审入口脚本）；**2026-10-10 收尾**（工程细节、分层倒置修复 D19、
链式自愈 e2e、语义意图闸门）；**2026-11-10 韧性层**（D25：指数退避重试 + 三态熔断、SDK 重试关闭、
成本按真实尝试计、假 provider e2e）；**2026-11-12 延迟与知识库**（D22 自愈触发快速探测、D23 L3 阈值外置、
D24 知识库运维 CLI）。

**边界与下一步**（明细见维护者本地记录，不随代码入库）：

- **L3 分桶仍按 `page_fingerprint`**（URL 路径 + DOM 结构哈希）：前端改版会换桶，极端情况下历史修复在换桶后不被检索到。
  计划 §3.2 的 **URL 路径分桶未实施**，列为下一步；当前取舍是不引入跨桶检索，避免把不同页面的修复互相污染。
- **iframe / Shadow DOM 自愈**：跨 frame 候选解析与存在性验证属架构级改动，当前定位在 POM 的 `locator()` 边界，
  先做评估 spike 再决定是否立项。
- **其余路线**：自愈指标跨运行时间序列；定时回归 cron 启用；A/B 真实失效场景集扩充与并行 e2e 收官；
  视觉成本重估、链节点级描述、链式低置信兜底、链式用例知识库串扰、意图闸门按数据标定、ab 夹具 provider、CI 加固。

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
- 文件编码：全仓文本文件统一 UTF-8（无 BOM），首行带编码声明；由 `tests/unit/test_file_encoding.py` 在 unit 门禁守护
  （编码 + 无 BOM + 声明存在三项断言）。

**遗留 TBD**：

- 视觉定位的控件画像（当前为候选集选择）。
- 语义向量 v2（fastembed 本地模型），规模大时评估 sqlite-vec / Chroma。
