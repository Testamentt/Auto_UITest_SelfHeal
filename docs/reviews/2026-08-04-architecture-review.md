# 架构与代码评审报告（Phase 1–4 全量）

> 日期：2026-08-04 · 方法：5 维度并行评审 + 逐条对抗性核实（33 个评审代理，28 条发现全部确认为真）
> 维度：耦合与内聚 · 可扩展性 · 健壮性与降级 · 测试充分性 · 一致性与文档对齐
> 基线：`feat/t4-secondary-healing`（含 Phase 1–4 高优先级 T1–T4）
> 状态：**H1/H2/H3 已修复（2026-08-05）**——见「高危修复记录」

## 总体结论

架构**骨架健康、方向正确**：插件化自愈、provider 无关抽象、知识优先、策略短路、防幻觉护栏等核心决策均已落实且互相自洽。但评审暴露出一条主线问题——

> **"增强能力不得拖累主流程"的降级承诺，在基建链路（采集/弹窗/知识读写/审计）上没有兜底边界；核心重试闭环与知识沉淀-复用闭环缺乏回归护栏；可扩展性在策略注册、知识后端、弹窗类型三处是"半开闭"。**

按严重度：**高危 3 · 中危 14 · 低危 11**。

## 分维度评价

| 维度 | 评价 | 主要问题 |
| --- | --- | --- |
| 健壮性与降级 | 🟠 有缺口 | 自愈管线缺顶层兜底（高危#1）；LLM/VLM 降级契约不自洽；知识读写无异常隔离 |
| 测试充分性 | 🟠 有缺口 | wrapped 重试闭环、知识沉淀-复用闭环零覆盖（高危#2/#3）；PopupGuard/VLM 降级覆盖不足 |
| 可扩展性 | 🟡 半开闭 | 策略注册表硬编码在 orchestrator；知识后端非注册制；弹窗类型/HEALABLE 硬编码 |
| 耦合与内聚 | 🟡 有逆向依赖 | HealingPage 组合根错放 engine/，engine 逆向依赖 agent/reporting；DOM 工具跨层重复 |
| 一致性 | 🟡 有漂移 | LLM 选型四处矛盾；死配置；文档把未实现能力写成已具备 |

---

## 🔴 高危问题（3 项，建议优先修）

### H1 · 自愈管线缺顶层兜底，内部异常会替换原始失败异常直接炸向用例
- **位置**：`engine/healing_locator.py:111-132`（wrapped 自愈分支）
- **现状**：动作超时进入自愈后，整条修复链路无保护。四处内部异常会沿 wrapped 上抛、**替换掉触发自愈的原始 TimeoutError**：
  1. `SceneCollector.capture()` 的 screenshot/content 在页面关闭/崩溃/被原生 dialog 阻塞时抛异常；
  2. `popup_guard._find_visible_popup` 的 `containers.count()` 无 try；
  3. `_lookup_knowledge→find_repair` 的 SQLite 读在 DB 锁定时抛 OperationalError；
  4. `_persist` 里 `add_repair` 被 suppress，但紧随的 `_record` 不在保护内，可能把已成功的自愈翻转为失败。
- **影响**：「开启自愈」本身引入新失败模式，且原始异常丢失——违背降级承诺与 R4。
- **建议**：wrapped 自愈分支对整条链路加统一兜底（捕获→warning→按 `_resolve_uncertain` 语义走 fallback 或抛携带原始 exc 的 HealingFailedError，用 `raise...from exc` 保异常链）；`capture()` 改逐项尽力而为（screenshot/content 分别 try，失败置 None）；知识读取 try→按未命中；`_record` 纳入与 add_repair 相同的 best-effort 边界。

### H2 · 自愈动作重试闭环（wrapped）与二次自愈触发条件零单测覆盖
- **位置**：`engine/healing_locator.py:108-135`（另 `_is_timeout_error` 42-46）
- **现状**：`healing_locator.py` 覆盖仅 50%，111-135 全未覆盖。现有单测都直接调 `_heal_and_resolve` 用假编排器绕过 wrapped；e2e 无用例让"自愈后的选择器再次失败"。结果：T4「有界二次自愈」的触发条件与"上限一次不死循环"不变量在任何层级都未验证；`_is_timeout_error`（含按类名的脆弱启发式）完全无测试。
- **建议**：补无浏览器纯逻辑单测——fake locator 首次抛"类名含 Timeout"异常、二次成功（验证自愈+重试）；自愈后仍超时（断言二次调用且 `use_knowledge=False`、最多两次）；非超时异常原样上抛；直接单测 `_is_timeout_error` 三种情形。

### H3 · 知识库「沉淀→复用」闭环未被任何层级测试，且 `_persist` 静默吞异常
- **位置**：`agent/orchestrator.py:101-112,170-190`（suppress 在 179）
- **现状**：run() 成功路径（策略命中→_persist→add_repair→_record）单测零覆盖；e2e `test_heal_success` 只断言 reporter.records，未断言写入知识库、更未断言下次复用。现有"知识复用"用例全是手工注入 RepairCase，证明不了真实沉淀的字段正确。「知识库优先」只测了读侧没测写侧。且 `_persist` 用 `suppress(Exception)` 静默吞沉淀失败、无日志（违 R4）。
- **建议**：补单测——空库下 heuristic 经 run() 命中，断言 `find_repair(original)` 非空且字段正确；再二次 run() 断言命中知识短路（`strategy=='knowledge'`），形成写→读闭环；注入抛异常的 add_repair 后端，断言 run() 仍 success；把 suppress 改为 try/except+日志。e2e 在 `test_heal_success` 末尾补 `find_repair('#submit-btn-old') is not None`。

---

## 🟠 中危问题（14 项，按主题分组）

### A. 健壮性补边界（#8/#9/#10/#11）
- **#8 strategy_order 未注册/拼写错误被静默跳过**（`orchestrator.py:148-151`）：配置写错策略名会静默降级、自愈恒失败且难排查。**建议**：Settings 加载期校验 strategy_order 都在注册表，非法即报错；至少把 continue 换成 warning。
- **#9 PopupGuard 知识读写无异常隔离**（`popup_guard.py:61-79`）：`find_popup`/`add_popup` 裸调，SQLite 锁定时会在动作重试前炸掉 wrapped；与 `_persist` 的 suppress 边界不一致。**建议**：统一"知识读写 best-effort"——find 失败按未命中、add 失败仅记日志仍返回 True；补抛异常后端的单测。
- **#10 知识库缺失败反馈与清理**（`orchestrator.py:116-131`）：`_selector_exists` 只验 `count()>0`（存在≠可操作）；缓存命中后重试失败无负反馈，失效高置信案例永久遮蔽正确修复；add_repair 只 INSERT 不去重、无界增长；读出的 confidence 无边界校验（NULL→TypeError）。**建议**：缓存重试失败→降权/标失效/删除；add_repair 按 (original,new,fingerprint) upsert 去重；读取时校验 confidence∈[0,1]。
- **#11 LLM/VLM 降级契约不自洽**（`llm/openai_client.py:62-74`）：SDK 原生异常（连接/超时/限流）未归一化为 UnavailableError 直接穿透；空 choices 抛 IndexError；`safe_float` 把 JSON `true` 当 1.0 穿透护栏。**建议**：chat()/analyze_image() 内统一捕获 openai 异常与空 choices 转抛 UnavailableError；`safe_float` 先判 bool 返回 default；补对应单测。

### B. 耦合与分层（#4/#6）
- **#4 组合根 HealingPage 错放最低层 engine/**（`healing_locator.py:19-26,179-200`）：engine 顶层 import agent/reporting/knowledge，`import engine.healing_locator` 拖入整个上层；「弹窗优先重试」「二次自愈」等编排级决策写在 engine，「知识库优先」却在 orchestrator——同一闭环策略分散两层。**建议**：HealingPage/HealingLocator 上移为顶层组合根（如 `selfheal/healing.py`），engine/ 只留纯能力；原路径保留兼容 re-export；编排级策略下沉进 orchestrator；HealingLocator 对 orchestrator 改窄 Protocol 依赖。
- **#6 稳定定位器工具跨层重复**（`popup_guard.py:128-140` vs `agent/dom.py`）：两份 build_stable_selector 已分化（弹窗版缺 text/aria 兜底）；dom.py 是纯工具却放在大脑层。**建议**：纯 DOM 工具下沉到最低共享位置（如 `selfheal/common/`），agent/dom.py re-export，popup_guard 删重复直接复用并补齐兜底。

### C. 可扩展性（#5/#7）
- **#5/#7 策略可插拔只完成一半**（`orchestrator.py:16,31-35,162-168`）：注册表硬编码在 orchestrator；新增策略要改 4 处（新文件+strategies/__init__+orchestrator import+注册表）；需模型的策略还要改 `_build_strategy` 的 `is SemanticStrategy/is VisualStrategy` 身份判断。与 llm/registry 的装饰器自注册形成反差。**建议**：注册表移入 strategies 包，用 `@register_strategy("name")` 自注册；定义 StrategyContext（llm_client/vision_client/settings）统一注入，删除按类分发；注册表类型改 `dict[str,type[RepairStrategy]]`，key 取 `cls.name` 消除双份名字。

### D. 测试补覆盖（#12/#13/#14）
- **#12 PopupGuard 核心逻辑仅测纯函数（27%）**（`popup_guard.py:53-80`）：知识复用路径零覆盖。**建议**：fake page/locator 补 4 例（无记录→启发式+沉淀 / 有记录→命中直接点 / 无弹窗→False / 点击抛异常→False 不沉淀）；e2e 补二次触发验证缓存复用。
- **#13 VLM 降级路径无测试**（`factory.py:39-57;openai_vision.py`）：与 LLM 不对称。**建议**：镜像 LLM 侧补 get_vision_for_settings 缺 key/disabled/未注册返回 None、openai 缺失抛 UnavailableError、get_vision 返回实例，均不触网。
- **#14 e2e 兜底用例隐式依赖"无 key"**（`test_healing_flow.py:32-36`）：若环境有 key，semantic 可能真实自愈、绕过兜底路径。**建议**：断言 `reporter.records` 为空（证明纯兜底），或注入 llm/vision=None 的 orchestrator，使"无法自愈"与 key 无关。

### E. 一致性与文档对齐（#15/#16/#17）
- **#15 LLM"已定选型"四处矛盾**（`settings.example.yaml:15-21` 等 5 处）：architecture.md 写 deepseek-chat、config.py 默认 deepseek-v4-flash、example yaml 写 gpt-4o-mini+OpenAI 端点、pyproject/CLAUDE.md 仍写"待定"——无一相同，用户复制 example 必踩坑。**建议**：以真实验证通过的配置为唯一事实源，一次性对齐 config.py/example/architecture/CLAUDE.md/pyproject 五处，删"待定"。
- **#16 execution/reporting 段是死配置**（`settings.example.yaml:10-13,42-46`）：Settings 未建模、pydantic 静默丢弃，注释宣称的行为代码里不存在（default_timeout_ms 未接线等）；channel 注释称支持 firefox/webkit 但实际恒 chromium.launch。**建议**：建 ExecutionConfig/ReportingConfig 真正接线，或从 example 移除并标"规划中"；根治：Settings 加 `extra='forbid'` 让漂移加载期报错。
- **#17 文档把未实现能力写成已具备**（`architecture.md:10,17,29` 等）：网络日志/trace 采集、视频回放是占位/未实现，但 architecture/README 呈现为已有。**建议**：在 architecture"未实现项"补 trace/network（T11）与视频回放（Phase 4），README 改"规划中"。

---

## 🟡 低危问题（11 项，择机优化）

| # | 问题 | 位置 | 建议 |
| --- | --- | --- | --- |
| 18 | agent 逆向依赖展示层（orchestrator import reporting 具体类、手工组装 HealingRecord）；`_selector_exists` 让 agent 直接做 page 操作 | orchestrator.py:25,192-203 | HealingRecord 抽为中性事件模型；orchestrator 依赖窄接口；`_selector_exists` 改注入 callable |
| 19 | 装配职责双入口（HealingPage 与 orchestrator 各自默认构造 knowledge/reporter） | healing_locator.py:191-196 | 收敛单一组合根或 build_orchestrator 工厂；diagnoser 选择抽工厂 |
| 20 | reporting 包内 hooks↔metrics 靠惰性导入规避循环（根因 HealingRecord 与实现耦合） | hooks.py:30-34 | HealingRecord 抽到 reporting/models.py，三模块单向依赖 |
| 21 | 知识库后端非注册制（新增后端要改 config Literal + factory if/else）；返回类型泄漏具体类；Protocol 无 close() | knowledge/factory.py:13-18 | 仿 llm/registry 加 register_backend；返回类型改 KnowledgeBackend；Protocol 加 close() |
| 22 | LLM provider 扩展半封闭（注册靠手改 __init__；factory 强制 key；kwargs 形状固定） | llm/__init__.py:11-12;factory.py | 增 extra 透传；key 是否必需交给 provider；get_api_key 上移中性模块；重复注册告警 |
| 23 | 弹窗类型扩展靠改硬编码常量；PopupFeature.category 形同虚设 | popup_guard.py:20-28;schema.py:26 | 容器选择器/关键词进 settings.yaml；或抽象可注册 PopupHandler 让 category 参与分派 |
| 24 | HEALABLE 动作集硬编码在核心代理类 | healing_locator.py:65-76 | 提升为 HealingConfig.healable_actions 配置项 |
| 25 | SqliteKnowledgeStore 并发/生命周期脆弱（固定共享路径、无 WAL/busy_timeout、只开不关） | sqlite_store.py:40-46 | PRAGMA WAL+busy_timeout；提供 close/atexit 生命周期；声明单线程假设 |
| 26 | strategy_order 未知策略名静默跳过（同 #8，低危视角） | orchestrator.py:148-151 | 同 #8 |
| 27 | T2"证据留存"在仓库不可验证（证据写入被 gitignore 的 reports/；冒烟门禁与 api_key_env 设计脱钩） | roadmap.md:69;.gitignore:24 | 证据迁 docs/evidence/ 入库，或如实改写；冒烟 skip 判断改读 api_key_env 指定的环境变量 |
| 28 | 多处陈旧 TODO/docstring 与已实现矛盾（browser/store/registry/diagnose） | browser.py:4 等 | 逐条清理过期 TODO，grep 'TODO' 全仓销账 |

---

## 改进建议（按优先级）

**第一优先（高危，建议立即）**
1. **H1 顶层兜底**：wrapped 自愈链路统一 try + capture 尽力而为 + 知识读写/审计 best-effort（一次性补齐降级边界）。
2. **H2+H3 补核心闭环测试**：wrapped 重试/二次自愈单测、知识沉淀-复用写→读闭环单测、_persist 异常容忍测试。

**第二优先（中危，短期）**
3. 健壮性补边界（#8/#9/#10/#11）：strategy_order 校验、PopupGuard 知识隔离、知识失效反馈+去重、LLM/VLM 异常归一化。
4. 一致性对齐（#15/#16/#17/#27/#28）：LLM 选型五处统一、死配置处理、文档能力对齐、证据入库、清理陈旧 TODO。

**第三优先（架构重构，需评审后动手）**
5. 解耦与开闭（#4/#5/#6/#7/#18-24）：组合根上移、策略自注册、知识后端注册制、DOM 工具下沉、reporting 数据模型抽离。这些是结构性重构，改动面大，建议单独开分支、逐项小步、充分回归。

> 说明：第三优先的重构会触碰核心链路，风险高于收益的时间窗，建议在 H1-H3 与中危修复稳定后、且有需要（如真的要加新策略/新后端）时再启动，避免过早重构。

---

## 高危修复记录（2026-08-05）

### H1 · 自愈管线兜底 ✅
- `collect/collector.py`：capture() 改尽力而为——url/screenshot/content 分别 try，失败置默认，采集失败不炸闭环（visual 无截图自然跳过）。
- `engine/popup_guard.py`：`containers.count()` 纳入 try；`find_popup`/`add_popup` 改 `_safe_find_popup`/`_safe_add_popup` 异常隔离（读失败按未命中、写失败仅记日志）。
- `agent/orchestrator.py`：`_persist` 由 `suppress(Exception)` 改 try/except+warning（不静默）；`_record` 纳入 best-effort。
- `engine/healing_locator.py`：`_heal_and_resolve` 顶层兜底——闭环内部异常转"自愈内部失败"outcome 走 D6 兜底，不替换原始定位失败异常。
- 各模块加 `logging.getLogger`。

### H2 · wrapped 重试闭环单测 ✅
- 新增 `tests/unit/test_wrapped_healing.py`：自愈+重试、二次自愈有界（T4 不变量：第二次 use_knowledge=False 且最多两次）、非超时原样上抛、`_is_timeout_error` 三种判定，均为无浏览器纯逻辑测试。

### H3 · 沉淀-复用闭环单测 + 日志化 ✅
- `_persist` 记 warning（R4 不再静默）。
- 新增 `tests/unit/test_persist_failure.py`：写→读闭环（run 沉淀字段正确 + 二次 run 知识短路）+ 沉淀失败容忍（注入抛异常后端，run 仍 success）。
- 新增 `tests/unit/test_collector.py`：capture 尽力而为四场景。

**验证**：ruff 全过；unit 84 + 核心 e2e 5 + 真实模型冒烟 2 全绿。
