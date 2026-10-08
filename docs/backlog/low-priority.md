# Low 待办清单

> **定位**：低优先级待办与主动延期项的**明细**清单（TODO.md 是本仓库唯一待办入口，本文件是它的展开）。
> **状态**：活文档（全部条目**未开工**；开工请按 R1 先出计划、按 R2 同步补测试，修完标记 ✅ 并注明提交）
> **最后更新**：2026-10-08 · 关联：[docs/reviews/2026-10-08-code-review.md](../reviews/2026-10-08-code-review.md)（问题出处）、[docs/reviews/2026-10-08-fix-report.md](../reviews/2026-10-08-fix-report.md)（High/Medium 修复记录）

## 一、Low（20 项，未开工）

| # | 位置 | 问题 | 建议改法 |
|---|---|---|---|
| L1 | `engine/healing_locator.py` `__getattr__` | 代理对象在 `_locator/_enabled` 未初始化时（copy/deepcopy/pickle）无限递归成 `RecursionError` | 开头用 `object.__getattribute__` 取值，缺失即 `raise AttributeError(name)` |
| L2 | `engine/healing_locator.py` `HealingPage` | 只代理 `locator()` 与 HEALABLE 动作；`page.get_by_*`、`page.click(sel)`、`expect()` 不自愈 | 文档明确边界（README/CLAUDE.md 的"POM 无缝切换"易被读成全部 API） |
| L3 | `collect/collector.py` | `page.on("request"/"response")` 只挂不摘，同页重复构造采集器会重复计数 | 提供 `detach()/close()`，或挂载前去重 |
| L4 | `collect/collector.py` 内联 trace | 依赖"未录制时 `tracing.stop()` 必抛错"探测录制状态（Playwright 版本相关） | 改显式状态位，不用异常探测 |
| L5 | `reporting/dashboard.py` 成本卡 | `.get(key, 0.0)` 只防缺键不防 `None`，外部 JSON 为 null 时整张看板渲染失败 | `float(cost.get("total_cost") or 0.0)` |
| L6 | `llm/embedding.py` | 空文本产出全零向量（余弦未定义 → nan），`to_vector` 不校验维度 | 零向量返回"不可比较"，`find_semantic` 前短路 |
| L7 | `llm/io.py` | `extract_json` 贪婪 `{.*}` 在多对象时静默丢弃候选；`safe_float` 接受 `"nan"/"inf"` | `raw_decode` 逐个尝试；`math.isfinite` 过滤 |
| L8 | `llm/openai_client.py`、`openai_vision.py` | 声明 `**kwargs` 却完全忽略，而 `base.py`/`fix_generator.py` 在转发 → 静默丢弃 | 显式拒绝（抛错）或真正透传 |
| L9 | `llm/openai_client.py` | 硬编码 `max_tokens` + `temperature=0.0`，切新模型族（需 `max_completion_tokens`/`temperature=1`）会 400 | 把字段名与温度纳入 provider profile（待验证后实施） |
| L10 | `config.py` | `viewport: dict[str,int]`（`wdith` 拼错可过）、`embedding.method: str`（`fastembed` 静默不可用） | 改 `TypedDict` / `Literal` + 未知值 warning |
| L11 | `config.py` | `reports/traces`、`.cache/knowledge.db` 等相对路径基于 **CWD**，换目录运行会分叉知识库 | `load_settings` 内以项目根 `resolve` |
| L12 | `config.py` | `strategy_thresholds` / `strategy_early_accept` 键名不校验（与 `strategy_order` 的 warning 处理不一致） | 未知名同样 warning |
| L13 | `scripts/notify.py`、`propose_pr.py` | 失败仅 `print` + `return 0`，长期故障只能翻日志 | 输出 `::warning::` 或写 `$GITHUB_STEP_SUMMARY`（退出码保持 0） |
| L14 | `scripts/ab_compare.py` | 只校验 B 组非空；A 组为空时静默产出作废表格并 `return 0` | 加 A 组守卫 + "未运行"告警行 |
| L15 | `docs/TODO.md:302`、`README.md:139/183` | 用例计数漂移（写 14 项实为 17 项）；README 的 `pytest -m e2e` 会带上 erp 用例 | 计数改"见 `pytest --collect-only -q`"；README 补 `-m "e2e and not erp"` |
| L16 | `pyproject.toml` + `tests/conftest.py` | `healing` marker 已注册且实现标签优先级，但全仓无用例使用 → README 宣称的 Allure"AI 自愈"分组永不出现 | 给 5 个自愈 e2e 补 marker，或删 marker 并同步文档 |
| L17 | `.github/workflows/ci.yml` | 无 `timeout-minutes`（卡死最长 6h）、无 `concurrency`（并发推 gh-pages）、`allure-report-action@master` | 补超时/并发组，action 固定 tag 或 SHA |
| L18 | `tests/conftest.py` session 级 `settings` | 读取本机 gitignored `config/settings.yaml`，e2e 结果依赖开发机状态 | e2e 层加"测试基线开关"断言（`dry_run` 等不得污染） |
| L19 | `tests/e2e/test_llm_smoke.py`、`test_visual_smoke.py` | 真实模型调用与普通 e2e 混在一起（`.env` import 期自动加载）→ 常规 e2e 会外呼付费模型 | 加 `live` marker，默认 `-m "e2e and not live"` |
| L20 | `reporting/allure_bridge.py`、`fix_proposals.py` | `attach_file(type_name: str)` 用字符串 `getattr` 取附件类型（拼错静默 False） | 改枚举/常量 |

## 二、主动延期的 Medium（2 项）

| # | 位置 | 为什么本轮没做 | 建议启动方式 |
|---|---|---|---|
| M10 | `engine/healing_locator.py`、`engine/popup_guard.py`、`collect/collector.py` 反向 import `agent/knowledge/reporting` | 属**结构重构**：要新增装配层（把 `HealingPage` 与 DTO 移出 engine）、把 `agent/dom/` 下沉为中立包，并同步 `docs/architecture.md` + 学习文档镜像（R7.4 双轨）——影响面横跨 8+ 文件与两类文档，需独立计划与回归窗口 | 按 R1 出计划：① `contracts.py` 放 `FailureContext`/`HealOutcome`；② `HealingPage` 迁 `plugin/`；③ `agent/dom/` → `selfheal/dom/`；④ 更新文档双轨 |
| M15（第 3 条） | `tests/e2e/test_healing_flow.py` 链式自愈无 e2e | 需要先给演示页加"嵌套 + 内层旧 id"的 DOM 夹具（`tests/e2e/pages/demo_page.html`），属新场景建设而非既有缺陷修复；其余两条（VLM 单测、工厂断言失效）已本批修复 | 在 demo 页加 `<section id="panel"><button id="inner-old">…</button></section>`，新增 `test_chain_healing_e2e`：`healing_page.locator("#panel").locator("#inner-old").first.click()` 断言语义与记录 |

## 三、修复过程中新发现的残余项（未开工）

> 来源：2026-10-08 修复轮的实现者复核（M4/M5 知识库对齐时发现），都不构成本轮缺陷，但需登记以免丢。

| # | 位置 | 现象 | 建议 |
|---|---|---|---|
| R1 | `knowledge/sqlite_store.py` vs `store.py` | `created_at` 双端仍漂移：SQLite 由 `DEFAULT CURRENT_TIMESTAMP` 产出 `"YYYY-MM-DD HH:MM:SS"`，内存端写入方未传则为 `None` → 影响 `_is_fresh`（L3 七天新鲜窗口）的判定口径 | 与 `last_hit_at` 同法归一：写入侧统一 `utc_now_iso()`，读取侧兼容旧格式 |
| R2 | `knowledge/sqlite_store.py` `find_repair` | 同分并列时未显式 `ORDER BY rowid`，理论上与内存端插入序不保证一致（实测一致） | SQL 补次级排序键 `rowid`，让契约可依赖 |
| R3 | `knowledge/sqlite_store.py` | `confidence` 为 NULL 的旧行：`ORDER BY confidence DESC` 把 NULL 排最后（读出归一 0.0）；若脏数据含负值，与内存端排序不一致 | 读侧归一后再排序，或迁移时把 NULL 归零 |
| R4 | `knowledge/sqlite_store.py` `_migrate_legacy_tables` | 多进程竞态下被忽略的 `duplicate column` 不计入 `added`，该场景不触发迁移 warning（仅有 info） | 如需审计合规，把忽略项单独计入返回结构 |
| **R5** | `agent/strategies/semantic.py` `_llm_semantic` | **语义 LLM 段没有 L2 交叉校验**（视觉段有 C4 融合降权）：模型对"与页面完全无关的描述"仍会给出高置信候选并被采纳（自报置信度、`shrink_self_reported` 默认关闭）。实测证据：真实模型可用后，`#ghost-btn-old` + 描述 `zzz_不存在的描述` 在**全量跑**中被 LLM 采纳点到 `#ghost-btn`（单文件跑走 fallback）——已先用 `offline_healing_page` 夹具把该用例改成确定性，但**产品侧的采纳护栏仍缺**。注意：直接把视觉段的 `conf × (0.4+0.6×l2)` 融合照搬过来会误伤合法场景（实测会让 `test_semantic_llm_locate_login_button` 的候选跌破阈值），需先设计"意图重叠下限 / 描述信息量"判据并在真实数据上标定 | 单独立项：给语义段加与视觉段对称的交叉校验（或用 `score_selector` 的 L2 下限做闸门），并补真实模型回归数据 |

## 四、开工前提醒

- R1：先计划后写入；R2：新增/修改代码须同时补 unit + e2e（缺一不可）；R4：不允许静默失败。
- L15–L20 多为"工程/文档一致性"项，适合与下一次功能迭代同批清理，单独开一批也完全可以。
