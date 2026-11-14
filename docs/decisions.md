<!-- 本项目统一使用 UTF-8（无 BOM）编码。 -->
# 架构决策记录（ADR）

> **定位**：影响系统结构与可维护性的决策记录；D 编号稳定，是架构取舍的当前事实源。
> **状态**：事实源（决策变化须与 [architecture.md](architecture.md) 同批更新）
> **最后更新**：2026-11-12 · 关联：[architecture.md](architecture.md) 架构 · [../README.md](../README.md) 项目展示

## 使用约定

- 编号沿用 D1–D25，单序列到底（不再有 T 前缀的独立编号）。
- 代码实现细节用「问题 → 方案 → 验证」式注释自解释；关键取舍在注释中引用本表编号（如 D6 兜底、D19 分层、D25 韧性）。
- 历史评审、计划、待办与路线图为维护者本地记录（不随代码入库）；本文件是决策的当前事实源。

## 关键 ADR

### D2 · LLM/VLM 接入方式

- **问题**：模型选型一旦阻塞，最小闭环无法推进；不同 provider 的 SDK 差异不应渗入业务代码。
- **决策**：Phase 2 起统一走 OpenAI 兼容接入，经 `llm/` 抽象层（`factory` 构建 + `registry` 注册），业务代码不直接 import 具体 SDK。
- **验证 / 代价**：无 key 时全链降级，行为与 Phase 1 等价；切换 provider 只改配置，不改调用方。

### D5 · 自愈的集成方式

- **问题**：自愈若侵入框架（替换 import / monkeypatch），会破坏既有 POM 与 Playwright 语义，关闭时也难以零开销。
- **决策**：pytest fixture 注入 + `HealingPage` 接口兼容代理；自愈只覆盖 `locator()` 与其链式派生对象的动作，其余 API 原样透传；不用 import 替换 / monkeypatch。
- **验证**：同一份 POM 在开 / 关自愈下都能跑，关闭时就是原生 `Page`；e2e 覆盖成功、兜底、关闭三场景。

### D6 · 不确定时的兜底

- **问题**：模型置信度不足时，如果静默失败或盲目重试，会把不确定变成不可控。
- **决策**：`healing.on_uncertain` 默认 `use_fallback`（优先人工备用定位器）；无备用则 fail；`pause` 仅交互模式可用。
- **验证**：`locator(sel, fallback=..., description=...)` 的兜底路径有单测与 e2e 覆盖，CI 友好。

### D7 · LLM 客户端形态

- **问题**：每接一个 provider 就引入一套 SDK 与配置，会让 CI 与依赖变重，也让切换成本失控。
- **决策**：单一 OpenAI 兼容客户端，用 `base_url` + `model` 覆盖不同 provider；SDK 惰性导入，缺失时抛 `UnavailableError` 走上层降级。
- **验证 / 代价**：CI 不装 openai 也能 import 框架；`base_url` / `model` 必须与 key 同平台，否则 401，错误信息带 provider / model / 端点主机。

### D9 · 模型输出的容错与防幻觉

- **问题**：模型输出不稳定（JSON 包裹、截断、幻觉 selector），不能让它直接驱动页面动作。
- **决策**：不依赖 `response_format`；`extract_json` 多候选容错 + 根因 / 策略输出白名单 + 防幻觉护栏（selector 必须来自页面真实候选集）。
- **验证**：单测覆盖异常 JSON 与越界输出；e2e 中模型不可用时自动降级规则式，闭环不中断。

### D14 · 知识库语义化

- **问题**：ID 变更但页面结构 / 文案不变时，纯 selector 检索会漏；调用远程 embedding 又会把延迟与费用带进热路径。
- **决策**：策略链固定为 **L1 `repair_key` 精确命中 → L2 启发式 → L3 语义向量检索 → L4 视觉（VLM）**；L3 用本地确定性 n-gram 哈希 TF 向量 + numpy 余弦，按 `page_fingerprint` 分桶；采纳规则为「已 verified 且 sim>0.92 / 7 天内 sim>0.80 自动，其余写人审清单」。
- **验证 / 代价**：零 API 费用、毫秒级；相似文案可命中；跨语言（尤其中文分词）偏弱，标定与升级路径见 architecture.md「遗留 TBD」。

### D18 · 视觉模型切换

- **问题**：多平台意味着多套端点与计费口径；原 VLM 端点延迟高。
- **决策**：VLM 改用 CommandCode 网关的 `deepseek/deepseek-v4.1-flash`（与 LLM 同网关同凭据）；代码默认值仍保留通义 `qwen3-vl-plus` + DashScope 公共端点作为可移植基线，本机 `config/settings.yaml` 覆盖。
- **验证 / 代价**：用项目自身客户端实测支持图片输入；真实视觉冒烟通过（`confidence=0.873`）；代价是推理模型含 reasoning token，成本单价需按新平台重估。

### D19 · 分层倒置修复

- **问题**：`engine/`、`collect/` 反向 import `agent` / `knowledge` / `reporting`，导致「能力建设层」拉起整个大脑与知识库，单测无法隔离、替换困难。
- **决策**：新增装配层 `selfheal/plugin/`（`HealingPage` 自 engine 上移）、中立 DTO 层 `selfheal/contracts.py`、`dom/` 下沉为 `selfheal/dom/`；`engine/popup_guard.py` 用窄 Protocol 去掉对 knowledge 的依赖；分层不变量由 AST 扫描用例守护。
- **验证**：四个入口的 import 闭包里 `agent` / `knowledge` / `reporting` 全为空；护栏做过「故意违规必失败」验证。

### D20 · 逐候选阈值裁决

- **问题**：按策略独立阈值（`strategy_thresholds`）曾"先按原始置信度取最大、再用赢家自己的阈值裁决"——高置信但**未达自身阈值**的候选会遮蔽低置信但**已达标**的候选（审计复现：heuristic 0.75 / 自身阈值 0.8 遮蔽 visual 0.62 / 自身阈值 0.6 → 本可修复却整体判失败）。
- **决策**：每个候选**先按自己策略的阈值过滤**，合格者才参与"取最优"；早接受短路保留（仍按该策略自身阈值与候选可用性）；被拒候选不丢弃，经 `FixProposal.rejected` 进失败路径的人审建议出口（审计信息不随过滤消失）；候选 `strategy` 归一为调度键，避免插件策略"过滤用 A 阈值、路由按 B 判"。
- **验证**：`tests/unit/test_bugfix_regression.py`（缺陷原样回归）+ `tests/unit/test_threshold_arbitration.py`（三策略择优 / 早接受 / 全被拒→人审建议 / 默认配置零回归）+ `tests/e2e/test_strategy_threshold_e2e.py`（阈值仍是端到端硬性路由）。

### D21 · 修复定位器的唯一性硬校验与动作感知可交互性

- **问题**：`selector_exists` 曾只判 `count() > 0`——两个同名按钮时启发式产出的 `text="提交"` 被当成"可用的修复"采纳，重试 `click()` 抛 Playwright strict-mode violation（**非 TimeoutError**，不触发自愈，直接冒泡），且脏案例会经 stage → commit 落库成为后续复用的污染源。
- **决策**：**唯一命中（`count()==1`）为硬底线**，统一用于候选采纳 / L1·L3 知识复用 / `verified` 判定；**可交互性按动作区分**（`set_input_files` 对隐藏 `input[type=file]` 同样可用，click / fill 等由 Playwright 自动等待），只作早接受门槛与同分 tie-break，**不以可交互性压置信度**（D6：宁可失败，也不错误地动作），候选当前不可交互时仍采纳并记日志；无浏览器替身缺探测方法时只校验唯一性。
- **验证**：`tests/unit/test_bugfix_regression.py`（非唯一 `text="提交"` 必须被拒）+ `tests/unit/test_selector_usability.py`（count 0/1/2、hidden/disabled、探测异常、隐藏上传框采纳、置信度优先 / tie-break、知识复用、verified 口径）；独立复核用真 Chrome 验证隐藏 file input 与"暂不可见稍后可见"未被误杀、同名按钮场景无 strict-mode 冒泡。

### D25 · 模型调用韧性（2026-11-10）

- **问题**：异常分级已区分 Fatal / Transient，但一直没有消费者；SDK 自带重试不可见、不区分 Fatal，且与业务重试叠加会放大付费调用。
- **决策**：`llm/resilience.py` 统一实现指数退避 + 三态熔断（CLOSED / OPEN / HALF_OPEN）；瞬态故障重试，连续失败熔断后抛 `CircuitOpenError` 走既有降级；Fatal 不重试；SDK 自带重试固定关闭（`max_retries=0`）；熔断状态为单进程（xdist 各 worker 独立）。
- **验证**：假 provider 的 e2e 覆盖重试 / 熔断 / 降级；模拟演示 15 次自愈等待 31.53s → 6.31s（`scripts/resilience_demo.py`）；成本按真实尝试次数计。

## 全部决策索引

| 编号 | 决策点 | 结论 | 理由 |
| --- | --- | --- | --- |
| D1 | 实施路径 | 纵向切片优先 | 先证明价值；不卡待定项 |
| D2 | LLM/VLM 接入 | Phase 2 接入，OpenAI 兼容优先 | 避免选型阻塞最小闭环 |
| D3 | 演示对象 | 自建本地测试页（POM 化） | 可控、可复现、可无缝切换其他 POM 用例 |
| D4 | 知识库后端 | SQLite | 零依赖、可持久化、易演示 |
| D5 | 自愈集成方式 | Fixture 骨架 + 接口代理 + 装饰器补充；不用 import 魔法 | 可开关、POM 无缝、可维护 |
| D6 | 不确定时兜底 | use_fallback，无备用则 fail；pause 仅交互模式 | CI 友好 + 人工兜底 |
| D7 | LLM 客户端形态 | 单一 OpenAI 兼容客户端（`base_url` + `model` 覆盖多 provider），SDK 惰性导入 | 切换 provider 只改配置；CI 无 openai 也能 import |
| D8 | DOM 公共能力 | 抽 `agent/dom/` 公共工具（解析 / 稳定定位器 / 指纹 / 候选），策略与 LLM 提示共用 | 消除循环导入、重复与私有耦合 |
| D9 | LLM 降级策略 | 不依赖 `response_format`；`extract_json` 容错 + 输出白名单 + 防幻觉护栏（selector 须唯一命中） | 模型不稳定时闭环不中断 |
| D10 | 知识库后端形态 | `KnowledgeBackend` 接口 + 内存 / SQLite 双实现 + factory 选择；DOM 指纹参与检索择优 | 可切换、可持久化、同结构页面复用更可靠 |
| D11 | 弹窗处理 | 知识优先（弹窗特征库）+ 关闭按钮启发式识别，成功后沉淀特征；动作超时先清弹窗再走自愈 | 直击"被遮挡"类失败 |
| D12 | 智能等待 | 先可见，再要求 `bounding_box` 连续 `stable_ms` 不变；POM 显式调用（可选增强） | 减少加载抖动误判，不改变默认行为 |
| D13 | 视觉定位（初版） | OpenAI 兼容 VLM（`qwen3-vl-plus`，备选 qwen3.8-flash）；候选集护栏；key 走 `DASHSCOPE_API_KEY` | 复用 OpenAI 兼容机制；防幻觉；密钥参数化（**2026-10-10 起实际选型见 D18**） |
| D14 | 知识库语义化 | 本地确定性 n-gram 哈希 TF 向量 + numpy 余弦；L1 `repair_key` 硬短路 → L2 启发式 → L3 语义检索 → L4 VLM；按 `page_fingerprint` 分桶；采纳规则见关键 ADR | 热路径不调 API embedding；ID 变但文本 / 结构不变仍可命中；防污染 + 冷启动免人审 |
| D15 | Allure 报告增强 | 轻量桥 `reporting/allure_bridge.py`（`_HAS_ALLURE` 单点探测，未装全 no-op）；环境页 + marker→标签 + 证据附件；CI `publish` job 发布 GitHub Pages（gh-pages，含历史趋势，仅 main） | 展示层不该侵入 agent；标签单 feature 防爆炸；闭环过程以结构化附件呈现够用 |
| D16 | 模型层护栏与异常语义 | 超时 / 输出上限 / 重试次数 / 图片体积上限**全部可配**（`LLMConfig`、`VisionConfig`）；异常分级为 `FatalUnavailableError`（鉴权 / 参数，重试无用）与 `TransientUnavailableError`（限流 / 超时 / 5xx，可重试），均继承 `UnavailableError`；`HealingFailedError` 同时继承 Playwright 与内置 `TimeoutError` | 2026-10-08 实证：key 与端点不同平台时只报异常类名，无法定位；401 与 429 同待遇导致重试策略与成本不可控 |
| D17 | 文档治理 | 主线只保留三篇：根 `README.md`（展示）、`docs/architecture.md`（架构）、`docs/decisions.md`（决策）；历史评审 / 计划 / 待办 / 模板归档到 `docs/archive/`（维护者本地记录，不随代码入库）；`reviews/plans/sessions` 只读 | 2026-11-11 文档收敛：主线对抗噪音，历史材料保留可查证 |
| D18 | 视觉模型切换 | VLM 由通义 `qwen3-vl-plus`（DashScope）改为 **CommandCode 网关的 `deepseek/deepseek-v4.1-flash`**（与 LLM 同网关同凭据）；凭据变量由 `DASHSCOPE_API_KEY` 改名为 `COMMANDCODE_API_KEY`；代码默认值仍保留 qwen3-vl-plus + DashScope 公共端点作为可移植基线（仅本机 `config/settings.yaml` 覆盖） | ① 项目自身客户端实测该模型**支持图片输入**；② 真实视觉冒烟 `1 passed`、`confidence=0.873`（原 qwen3-vl-plus 0.921）、耗时 8.6s vs 60s 级；③ 与 LLM 同平台可少一套端点与计费口径。代价：推理模型含 reasoning token，成本单价需按新平台重估 |
| D19 | 分层倒置修复 | 新增装配层 `selfheal/plugin/` + 中立 DTO 层 `selfheal/contracts.py` + `dom/` 下沉为 `selfheal/dom/`；`engine/popup_guard.py` 用窄 Protocol `PopupKnowledge` 去掉对 knowledge 的依赖；分层不变量改由 AST 扫描用例守护 | 反向依赖让"能力建设层"实际拉起整个大脑与知识库：单测无法隔离、替换/裁剪困难。实测改后四个入口的 `sys.modules` 闭包里 agent/knowledge/reporting 全为空；护栏做过"故意违规必失败"验证 |
| D20 | 阈值裁决 | 逐候选先按自身策略阈值过滤，合格者再取最优；被拒候选进人审建议；候选 strategy 归一为调度键 | 高置信未达标候选不得遮蔽低置信达标候选（审计复现过误判失败，见关键 ADR） |
| D21 | 修复定位器校验 | `selector_exists` 唯一命中硬校验；可交互性按动作取舍（早接受门槛 / 同分 tie-break，不压置信度） | 非唯一 `text="提交"` 重试时 strict-mode 崩溃并污染知识库；隐藏上传框不可硬拒（见关键 ADR） |
| D22 | 自愈触发快速探测 | 动作前以 `healing.probe.timeout_ms`（默认 2.5s）探测目标是否就绪，不就绪直接进自愈；探测**不改变结论、只改变到达结论的速度**；判定用 `wait_for()` 在预算内等待（不误杀慢加载）；`count>1` 与 `disabled` 不提前判死；链式重放复用同一预算 | 原实现要等满 Playwright 默认超时（30s）才进闭环，单条失效 +30s、e2e 全量 492s。实测：单条 30.63s → 0.63s、链式 33s → 5.4s、全量 492s → 106s。多命中属编程错误、disabled 可恢复，提前判会掩盖/误杀 |
| D23 | L3 阈值外置 | 语义检索的 `min_sim` / `fresh_sim` / `verified_sim` / `fresh_days` 从模块常量改为 `knowledge.semantic` 配置（`L3Thresholds` 注入策略）；层级约束 `min <= fresh <= verified` 在加载期校验 | 阈值按被测系统标定要改代码、发版；倒挂配置会让"更严的信任条件反而更易采纳"，属静默错误 |
| D24 | 知识库运维入口 | `scripts/knowledge_admin.py`：`--stats / --prune / --dedupe / --export`；写操作**默认 dry-run**；`--prune` 保护已人审与被复用过的案例 | `verify_case.py` 只管人审标记，缺"看全貌、清垃圾"入口；知识库长期运行会积累失效、低价值与重复案例，无入口只能人工直连数据库 |
| D25 | 模型调用韧性 | `llm/resilience.py` 指数退避重试 + 三态熔断；瞬态重试、Fatal 不重试；`CircuitOpenError` 继承 `UnavailableError` 走既有降级；SDK 重试固定关闭；熔断状态单进程 | 异常分级长期无消费者；SDK 重试不可见且与业务层叠加成 9 次付费调用；模拟演示 31.53s → 6.31s |
