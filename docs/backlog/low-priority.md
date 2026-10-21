# Low 待办清单

> **定位**：低优先级待办与主动延期项的**明细**清单（TODO.md 是本仓库唯一待办入口，本文件是它的展开）。
> **状态**：活文档（L1–L20、R1–R4、M10、M15-3、R5 **均已修复**；本轮新登记的残余项见 §五）
> **最后更新**：2026-10-10 · 关联：[docs/reviews/2026-10-08-code-review.md](../reviews/2026-10-08-code-review.md)（问题出处）、[docs/reviews/2026-10-08-fix-report.md](../reviews/2026-10-08-fix-report.md)（High/Medium 修复记录）

## 修复进度（2026-10-10）

> 提交按「每天 2 个」的节奏推送：队列在 `E:\Project\AutoAiSelfHeal-push-queue`（Windows 任务计划程序 19:40，
> 应用 → `pytest -m unit` + ruff 双门禁 → 通过才 push；日志见该目录 `push-log.txt`）。
> 验收数字：unit 基线 **404 → 566 passed**；队列每个前缀都单独跑过 `pytest -m unit`，全绿。
> 下表「条目」= 队列 patch 编号，对应提交的时间戳都落在 UTC+8 10:00–20:00 内。

| 项 | 条目 | 项 | 条目 | 项 | 条目 |
|---|---|---|---|---|---|
| L1 | 01 ✅已推送 | L14 | 08 | L11 | 18 |
| R1 | 02 ✅已推送 | L16 | 09 | R4 | 19 |
| L6 | 03 | L3 | 10 | L19 | 20 |
| R2 | 04 | L8 + L9 | 11 | L12 | 21 |
| L7 | 05 | L17 | 12 | L20 | 23 |
| L4 | 13 | L18 | 17 | 示例配置收尾 | 24 |
| R3 | 14 | 19 → | — | — | — |
| L10 | 15 | | | | |
| L5 | 16 | | | | |

**本轮新增的三项**（M10 / M15-3 / R5，见 §二、§三）：

| 项 | 条目 | 一句话结果 |
|---|---|---|
| M10-① 中立 DTO | 25 | `FailureContext` / `HealOutcome` 迁 `selfheal/contracts.py`，原路径 re-export 零破坏 |
| M10-② plugin 装配层 | 26 | `HealingPage` 迁 `selfheal/plugin/`；engine 只留原语；L2 边界 docstring 随迁 |
| M10-③ dom 下沉 + 分层护栏 | 27 | `agent/dom/` → `selfheal/dom/`；`popup_guard` 去 knowledge 依赖；护栏改 AST 扫描并扩到五层 |
| R5 语义意图闸门 | 28 | 描述与页面任一候选无词元/子串依据 → 不采纳（模型调用前拦下） |
| M15-3 链式 e2e | 29 + 30 | 演示页加"链式"面板夹具 + 三个真实 e2e 场景（零外呼） |
| L6 调用方接线 | 31 | 不可比较向量不进检索、不落库 |
| L3 + L4 接线 | 33 | trace 状态显式注入 + 采集器 detach 接入生命周期 |

**M10 的验证口径（可复核）**：`import selfheal.engine.healing_locator` / `selfheal.collect.collector` /
`selfheal.engine.popup_guard` / `selfheal.dom` 四个入口的 import 闭包里，`selfheal.agent`、`selfheal.knowledge`、
`selfheal.reporting` **一个都不出现**（实测 `sys.modules`），装配依赖全部集中在 `selfheal/plugin/`。

## 一、Low（20 项，已修复——条目号见上表）

| # | 位置 | 问题 | 实际改法 |
|---|---|---|---|
| L1 | `engine/healing_locator.py` `__getattr__` | 代理对象在 `_locator/_enabled` 未初始化时（copy/deepcopy/pickle）无限递归成 `RecursionError` | `object.__getattribute__` 取值，缺失即 `raise AttributeError(name)`（两处代理都改） |
| L2 | `HealingPage`（现 `plugin/healing_page.py`） | 只代理 `locator()` 与 HEALABLE 动作；`page.get_by_*`、`page.click(sel)`、`expect()` 不自愈 | 边界写进类与 `locator()` 的 docstring + 行为用例固化（未改 README/CLAUDE 措辞） |
| L3 | `collect/collector.py` | `page.on("request"/"response")` 只挂不摘，重复构造会重复计数 | 新增幂等 `detach()`（`close()` 等价）+ 挂载去重 + `_detached` 守卫；`HealingPage.close()` 级联调用 |
| L4 | `collect/collector.py` 内联 trace | 依赖"未录制时 `tracing.stop()` 必抛错"探测录制状态 | 新增可注入 `trace_active` provider（三层透传，conftest 按 CLI 取值），异常探测降为兜底 |
| L5 | `reporting/dashboard.py` 成本卡 | `.get(key, 0.0)` 只防缺键不防 `None`，null 会让整张看板渲染失败 | `_safe_number/_safe_count` 归一（None/空串/非数值/nan/inf → 0） |
| L6 | `llm/embedding.py` | 空文本产出全零向量（余弦未定义 → nan），`to_vector` 不校验维度 | 改返回"不可比较"标记 + `is_comparable()`/`embed_vector()`；调用方（L3 检索、写入侧）短路 |
| L7 | `llm/io.py` | `extract_json` 贪婪 `{.*}` 在多对象时静默丢弃候选；`safe_float` 接受 `"nan"/"inf"` | `JSONDecoder().raw_decode` 逐个候选；`math.isfinite` 过滤 |
| L8 | `llm/openai_client.py`、`openai_vision.py` | 声明 `**kwargs` 却完全忽略 → 调用方传参被静默丢弃 | 未知 kwargs **抛 `TypeError`**（编程错误不该被降级静默） |
| L9 | `llm/openai_client.py` | 硬编码 `max_tokens` + `temperature=0.0`，切新模型族会 400 | 新增 `llm.max_tokens_field`（`max_tokens` / `max_completion_tokens`，后者省略 temperature）；真实端点已验证两族都 200 |
| L10 | `config.py` | `viewport: dict[str,int]`（`wdith` 拼错可过）、`embedding.method: str`（`fastembed` 静默不可用） | `field_validator(mode="before")` 白名单校验 + `method` 改 `Literal`（未实现值加载期报错） |
| L11 | `config.py` | `reports/traces`、`.cache/knowledge.db` 等相对路径基于 **CWD** | `load_settings` 内以项目根 resolve（显式绝对路径不动、直接构造 `Settings()` 保持字面默认值） |
| L12 | `config.py` / `agent/fix_generator.py` | `strategy_thresholds` 键名不校验 | 未登记策略名的阈值键记一次 warning（回退全局的行为逐字节不变） |
| L13 | `scripts/notify.py`、`propose_pr.py` | 失败仅 `print` + `return 0`，长期故障只能翻日志 | 失败输出 `::warning::` GitHub 注解（退出码仍为 0） |
| L14 | `scripts/ab_compare.py` | 只校验 B 组非空；A 组为空时静默产出作废表格 | A/B 对称守卫 + "未运行"行数 >0 时报告追加 ⚠️ 告警行 |
| L15 | `docs/TODO.md`、`README.md` | 用例计数漂移；README 的 `pytest -m e2e` 会带上 erp 用例 | 2026-10-08 文档治理轮已修（计数改见 `--collect-only`、README 补 `-m "e2e and not erp"`） |
| L16 | `pyproject.toml` + e2e | `healing` marker 无人使用 → Allure"AI 自愈"分组永不出现 | 5 个自愈闭环 e2e 打 `pytestmark = [e2e, healing]`，并加"收集集合相等"用例 |
| L17 | `.github/workflows/ci.yml` | 无 `timeout-minutes`（卡死最长 6h）、无 `concurrency`、action 未固定 | unit 15 / e2e 30 分钟 + 并发组 + `allure-report-action@v1.15`（已核实存在） |
| L18 | `tests/conftest.py` session 级 `settings` | 读本机 gitignored 配置，e2e 结果依赖开发机状态 | e2e 层基线守卫：`dry_run` / `enabled=false` 且无 CLI 覆盖 → 立即 fail 并指出开关 |
| L19 | `test_llm_smoke.py`、`test_visual_smoke.py` | 真实模型调用混在普通 e2e，常规跑会外呼付费模型 | 新增 `live` marker；CI 改 `-m "e2e and not erp and not live"` |
| L20 | `reporting/allure_bridge.py` | `attach_file(type_name: str)` 拼错静默 False | `Literal` 白名单 + 运行期 warning（附可选值清单）；默认 ZIP 不变 |

## 二、主动延期的 Medium（2 项，已修复）

| # | 位置 | 结论 |
|---|---|---|
| M10 | `engine/`、`collect/` 反向 import 上层 | ✅ 条目 25–27：新增 `plugin/` 装配层 + `contracts.py` 中立 DTO + `dom/` 下沉；engine/collect 的 import 闭包现已为空，装配依赖集中在 `plugin/`。原计划的第 ④ 步（文档双轨）见 §五 R12 |
| M15（第 3 条） | 链式自愈无 e2e | ✅ 条目 29–30：演示页新增 `<section id="panel">` + 失效旧 id `#inner-btn-old` 夹具，新增 `tests/e2e/test_chain_healing_e2e.py` 三场景（链式重放 / 二次自愈修叶子 / 关闭自愈仍原生），零外呼且带 `llm_calls`/`vlm_calls == 0` 护栏 |

## 三、修复过程中新发现的残余项（R1–R5，已修复）

> 来源：2026-10-08 修复轮的实现者复核（M4/M5 知识库对齐时发现）。

| # | 位置 | 结论 |
|---|---|---|
| R1 | `knowledge/sqlite_store.py` vs `store.py` | ✅ 条目 02：两端写入侧统一 `utc_now_iso_if_missing()`，读侧兼容旧空格格式 |
| R2 | `find_repair` 并列次序 | ✅ 条目 04：`ORDER BY confidence DESC, rowid ASC`，契约写进 docstring |
| R3 | NULL / 负 `confidence` | ✅ 条目 14：两端 `_normalize_confidence` 归一后再排序（**刻意不夹取 >1.0**，越界交给 `fix_generator` 的 fail-closed 门槛拒绝） |
| R4 | 迁移忽略项不可审计 | ✅ 条目 19：返回 `MigrationReport(added, ignored)`，忽略项逐条记 info |
| R5 | 语义 LLM 段缺 L2 交叉校验 | ✅ 条目 28：加"意图依据"闸门（只看描述、只做布尔下限、不改置信度——**不照搬**视觉段融合，实测融合会误伤合法场景） |

## 五、本轮新登记的残余项（未开工）

| # | 位置 | 现象 | 建议 |
|---|---|---|---|
| R6 | `agent/fix_generator.py` 成本常量 | VLM 已换到 CommandCode 的 `deepseek/deepseek-v4.1-flash`（推理模型，含 reasoning token），`_DEFAULT_UNIT_COST['vlm']` 仍是旧平台的估值 | 按新平台单价重估成本常量（决策 D18 的代价项） |
| R7 | `engine/healing_locator.py::HealingLocator.locator()` | 链节点级 `description`/`fallback` 不被解析，原样透传给原生 `Locator.locator()` → 直接 `TypeError`（M15-3 实测） | 在 `_chain` 里剥离自愈增强参数，或显式拒绝并提示改挂根定位器 |
| R8 | `agent/orchestrator.py` 链式根修复 | 策略**定位正确但置信度低于阈值**时（实测视觉 0.27）会让整次链式点击抛 `HealingFailedError`，即使候选已命中目标元素 | 评估"低置信但 L2 依据充分"的兜底路径，或把链式失败降级为逐跳重试 |
| R9 | `tests/e2e/test_chain_healing_e2e.py` | session 级知识库会跨用例串扰，链式用例的"修根"一跳结果随历史变化（首跑 flaky 的成因之一） | 需要时改用独立内存知识库夹具（参考 `test_knowledge_reuse.py` 的 fresh 夹具） |
| R10 | `agent/strategies/semantic.py` 意图闸门 | 只拦"零依据"；"有依据但 LLM 选错元素"仍会放行（设计如此） | 用真实数据标定：把 l2 作第二输入接进 `agent/confidence.py` 的 `CALIBRATORS[KEY_SEMANTIC_LLM]`（落点已写进模块 docstring） |
| R11 | `tests/e2e/test_ab_scenarios.py::ab_page` | 该 fixture 自建 `HealingPage` 未注入 trace provider → 仍走异常探测兜底（功能等价） | 补一行 provider 注入即可彻底消除探测 |
| R12 | `docs/architecture.md`、`docs/roadmap.md` + 学习镜像 | M10 分层变更与 R5/M15-3 事实已写入源文档，**学习版镜像需在队列推完后同批同步**（R7.4） | 队列推完后按 `.claude/skills/doc-study-sync` 同步并推送镜像 |
| R13 | `.github/workflows/ci.yml` | `notify` / `propose-pr` / `publish` 三个 job 仍无 `timeout-minutes`；`actions/*` 与 `peaceiris` 是 major tag（非精确 pin） | 下一轮 CI 加固时一并收紧 |

## 六、开工前提醒

- R1：先计划后写入；R2：新增/修改代码须同时补 unit + e2e（缺一不可）；R4：不允许静默失败。
- 队列机制说明见 `E:\Project\AutoAiSelfHeal-push-queue\README.md`（每天 2 条、窗口守卫、门禁、失败回滚）。
