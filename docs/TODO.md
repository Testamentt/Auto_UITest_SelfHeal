# TODO — 优化与待办清单

> 来源：`docs/reviews/2026-08-04-phase3-retrospective.md` · 全量评审 `docs/reviews/2026-08-15-code-review.md` · 全量评审 `docs/reviews/2026-09-03-code-review.md` · 随进展勾选/更新（R5 活文档）
> T1–T4 具体实现方案：`docs/plans/2026-08-04-phase4-t1-t4-implementation.md`
> 图例：⬜ 待办 · 🟡 进行中 · ✅ 完成 · 🔴 阻断项 · 🟠 部分完成

## 🔥 高优先级（Phase 4 核心：证据 + 指标 + 加固）

- [x] **T1 · 策略短路，省 LLM/VLM 调用**（P1）✅ 2026-08-04
  - `_best_candidate` 达 `early_accept_threshold`(0.85) 即返回，不再尝试后续更贵策略
  - 位置：`src/selfheal/agent/orchestrator.py`、`config.py`（新增 early_accept_threshold）
  - 验收：启发式命中的自愈不再触发 semantic/visual（`test_strategy_short_circuit.py` 2 项）
- [x] **T2 · 真实模型跑通 + 证据留存**（P3）✅ 2026-08-04
  - 真实模型跑通：DeepSeek LLM 连通 OK；**qwen3-vl-flash 视觉定位真实 e2e 通过**（从截图识别登录按钮，置信度 0.95，strategy=="visual"）
  - 证据留存：冒烟测试自动保存截图 + 模型结果到 `reports/evidence/`（visual_scene.png / visual_result.json / llm_healing_records.json）
  - key 支持从 `.env` 加载（python-dotenv，已 gitignore）+ `.env.example` 模板；key 绝不入 git
  - 附带修复：①知识命中也记录（提取 `_record`），解决会话级知识缓存致冒烟 0 记录；②视觉冒烟加重试适配 VLM 输出非确定性
- [x] **T3 · 自愈指标看板（价值叙事落点）**（P4）✅ 2026-08-04
  - `reporting/metrics.py::compute_metrics`（总数/成功率/策略分布/根因分布）
  - `dashboard.py` 增指标摘要（成功率卡片 + 策略/根因分布）；`HealingReporter.metrics()`
  - 验收：`test_metrics.py` + `test_dashboard.py` 8 项单测；已用真实数据生成看板（100% 成功）
  - 遗留：通过率前后 A/B 对比（T3b）需专用演示套件
- [x] **T4 · 修复失败二次自愈 / 缓存验证**（P2）✅ 2026-08-04
  - `_lookup_knowledge` 增缓存验证（`_selector_exists`）：缓存的新选择器失效则不用、转策略重修
  - `_healing_action` 重试仍失败 → 二次自愈（`use_knowledge=False` 跳过缓存，有界一次防循环）
  - 位置：`engine/healing_locator.py`、`agent/orchestrator.py`
  - 验收：`test_secondary_healing.py` 5 项单测 + 核心 e2e 回归通过

## 🔴 2026-09-03 Code Review 待修复项（评审 `docs/reviews/2026-09-03-code-review.md`）✅ 全部修复（2026-09-03）

- [x] **R1 · settings.example.yaml 顶层 `action_wait` 段致配置加载崩溃**（Critical，实证）✅
  - `action_wait` 实为 `healing.action_wait` 子字段（config.py `HealingConfig`），example 写成顶层段 →
    `extra='forbid'` 下 `Settings.model_validate` 直接 ValidationError，复制模板即启动崩溃
  - 修复：段移入 `healing:` 内；验收：example 经 `model_validate` 加载成功（实证）
- [x] **R2 · propose_pr.py `build_pr_body` 残片 → ruff 门禁红**（High，实证）✅
  - L51-59 残缺首定义（F841+F811 共 2 errors）已删除；验收：`ruff check .` 0 errors
- [x] **R3 · 环境特定端点硬编码入 git 默认值**（Major）✅
  - `VisionConfig.base_url` 默认改 DashScope 公共端点、`SutConfig.api_base_url` 默认改 localhost 占位；
    专属 MaaS 端点与内网地址迁入 gitignore 的 `config/settings.yaml`（本机行为不变，双路径实证）
- [x] **R4 · erp_page fixture 未登记 `_session_reporters`**（Major）✅
  - conftest erp_page 内补登记，ERP 自愈记录进 dashboard / healing-records.json / T19 通知
- [x] **R5 · Minor 批** ✅
  - m2 allure_bridge docstring 优先级补 erp；m3 notify 策略分布过滤 None（对齐 metrics 口径）；
    m4 agent/llm_io.py 兼容壳按 R3 回收删除（grep 确认无引用）；m5 example 尾注更新（T9 已决策
    不建模）；m6 trace 录制参数抽 `TRACE_RECORDING_KWARGS` 常量（collector/conftest 同源）；
    m7 `interactive_candidates` 改 `is not None`（原生成功即优先，含空结果）；Scene 注解精确化
  - 遗留：m1 已于同日子代理复核轮修复（`build_compact_dom` 上移 `agent/dom/compact.py`，
    见下节 V5-⑪）；nit 批（ab_compare 口径 / attach_file 类型 / erp_client pageSize /
    CI PR body 快照注释）未动
  - 验收：ruff 0 errors + unit 293 passed + 配置双路径加载实证

## 🟠 2026-09-03 子代理复核加固项 ✅ 全部修复（2026-09-03 同日）

> 背景：两轮 review 完成后，以 5 个子代理（opencode-custom/glm-5.3-flash）对全部修复做独立复核
> （V1–V5），10 项结论全部 confirmed；fresh-eyes 扫描新增 12 条发现（0 high / 4 medium / 8 low）。
> 本轮全部修复：**ruff 0 errors、unit 312 passed（+19 回归测试）**。

- [x] **V4-① 嵌套配置模型未设 extra='forbid'**（Medium）✅
  - 只有顶层 Settings 拒绝未建模字段，healing: 等嵌套段拼错键被静默丢弃（dry_run 类安全
    开关静默失效）→ 新增 `_StrictModel` 基类下发全部 8 个子模型；
    回归：`test_config_strict.py`（example 可加载 / 嵌套未知键拒绝）
- [x] **V4-② 全局阈值无 [0,1] 值域校验**（Medium）✅
  - `confidence_threshold` 配负数=无条件全采纳垃圾修复、>1=自愈永不采纳 →
    `_validate_thresholds` 补三全局阈值值域校验；回归：`test_config_strict.py`
- [x] **V4-③ `embedding.dim` 无正整数校验**（Low）✅
  - dim=0 在 NgramEmbedding 取模处除零崩溃、负数产生负索引错向量 → 加载期拒绝
- [x] **V4-④ orchestrator 构造失败泄漏已建资源**（Low）✅
  - `__init__` 包 try/except：失败即 `close()` 已建自有资源再原样上抛；注入资源不代关；
    回归：`test_review_hardening.py`（自有清理 / 注入不代关）
- [x] **V4-⑤ 豁免审计记录虚增"真自愈"指标**（Low）✅
  - 高风险页豁免 HealingRecord 补 `verified=False`（豁免不是修复）
- [x] **V4-⑥ `_safe_close` 零日志吞关闭失败**（Low）✅
  - 失败记 warning（best-effort 语义不变，关闭故障不再完全不可见）
- [x] **V5-⑦ sqlite 旧库无 schema 迁移，fixture 直接炸**（Medium）✅
  - `PRAGMA table_info` 探测缺列 → `ALTER TABLE ADD COLUMN` 补齐 + 历史重复行去重
    （建唯一索引前）+ 迁移审计日志；`build_knowledge_store` 对 `sqlite3.Error` 降级
    memory 后端（warning，不炸会话）；
    回归：`test_knowledge_sqlite.py`（旧库补列 / 重复行清理 / 坏库降级）
- [x] **V5-⑧ add_repair upsert 覆盖人审 is_verified**（Low）✅
  - DO UPDATE 列剔除 is_verified：再次沉淀不覆盖 `set_verified(True)`（L3 防污染
    自动采纳 sim>0.92 且 is_verified 依赖该信任状态）；回归：preserves_verified 用例
- [x] **V5-⑨ add_popup 无去重无界增长**（Low）✅
  - signature 唯一索引 + upsert（最新观察胜出；旧库先删非唯一同名索引再建，
    保证 ON CONFLICT(signature) 生效）；memory 后端语义对齐
- [x] **V5-⑩ OpenAICompatibleVLM 缺 close()**（Low）✅
  - 补幂等 close（与 OpenAICompatibleLLM 对齐），orchestrator.close() 对 VLM 不再静默 no-op
- [x] **V5-⑪ m1 `llm/io.py` 反向依赖 `agent.dom`（分层倒置，上轮遗留立项）**（Medium）✅
  - `build_compact_dom` 上移 `agent/dom/compact.py`（llm/ 只留纯 I/O 编解码）；
    `test_layering.py` 静态守卫固化"llm/、knowledge/ 禁止 import selfheal.agent"
- [x] **V5-⑫ `semantic._bump` 静默吞错**（Low）✅
  - suppress → except + warning 日志（R4"不静默吞错"对齐），失败仍不阻塞采纳

## 🟡 中优先级

### ⚠️ 踩坑记录（事故沉淀，R8.5）

- **2026-09-03 · PowerShell 5.1 编码事故（CI workflow 乱码）**：
  - 事故：用 PS5.1 `Get-Content -Raw` / `Set-Content -Encoding utf8` 往返处理无 BOM 的 UTF-8
    `.github/workflows/ci.yml` → PS5.1 按 ANSI(GBK) 误读，**全部中文变乱码字节**（BOM 去除后仍乱），
    推送后 CI 注解 job 名呈 GBK 乱码（"修复建议草稿 PR" → "淇寤鸿鑽夌 PR"）
  - 修复：以安全编辑工具全量重写 ci.yml（字节级验证正确中文 / 乱码字节不存在 / 无 BOM）
  - **教训（对所有协作者生效）**：①含中文文件禁用 PS5.1 `Get-Content`/`Set-Content`/`Out-File` 往返，
    改用编辑工具或 `[System.Text.UTF8Encoding]::new($false)` 显式无 BOM 写出；②PS5.1 传**复杂中文
    commit message**（含全角括号/引号嵌套）会被拆参（`fatal: Invalid path`），改用 `git commit -F <file>`；
    ③写后必做字节级断言（如 `"目标串".encode() in data`），控制台显示编码不可信（GBK 终端显示 UTF-8 恒乱码）


- [x] **T5 · 置信度归一化 / 按策略阈值**（P5）✅ 2026-08-28
  - 方案 C（计划确认）：`agent/confidence.py` 归一化层——`CALIBRATORS` 可插拔注册表 + `calibrate`，
    默认恒等（与 T4 后行为零回归）；`shrink_self_reported` 开启时对 LLM 自报段做 raw² 保守收缩，
    待真实模型标定数据沉淀后再启用/调参（roadmap 待解决问题）
  - 按策略阈值：`healing.strategy_thresholds` / `strategy_early_accept`（缺省回退全局，
    validator 防越界/倒挂）；路由接线：generate 采纳 / best_candidate 短路 / lookup_knowledge
    按来源策略 / persistence.resolve 采纳
  - 策略产出点统一经 calibrate 出口（heuristic / semantic L3+LLM / visual，标尺契约文档化于 confidence.py）
  - 位置：`src/selfheal/agent/confidence.py`（新增）、`config.py`、`fix_generator.py`、`persistence.py`、`strategies/`
  - 验收：`test_confidence_normalize.py` 19 项单测 + `test_strategy_threshold_e2e.py` 2 项 e2e；
    全量 unit 219 passed、e2e 9 passed、ruff 全绿
- [x] **T6 · 智能等待默认融入动作前置** ✅ 2026-08-28
  - `healing.action_wait` 子配置（enabled 默认 **False**=零侵入守 D12 / timeout_ms=2000 / stable_ms=300）
  - `HealingLocator._wait_before_action`：动作（HEALABLE）执行前做一次短稳等待（复用 smart_wait），
    best-effort——等待失败仅记 debug、不阻塞动作（等待是增强不是正确性前提）
  - 位置：`src/selfheal/config.py`（ActionWaitConfig）、`engine/healing_locator.py`
  - 验收：`test_action_wait.py` 6 项单测（开关/前置调用/失败降级/显式调用可用）+
    `test_smart_wait_flow.py` 扩展 2 项 e2e（抖动元素直接 click 成功 / 既有自愈流程不受影响）；
    全量 unit 225 passed、ruff 全绿
- [x] **T7 · 知识二次命中 e2e（坐实"越用越聪明"）**（P9）✅ 2026-08-28
  - `tests/e2e/test_knowledge_reuse.py` 2 项：场景 A 第一次启发式自愈落库 → 第二次同场景
    strategy=="knowledge" 直接复用；场景 B 沉淀后页面再次改版使缓存 selector 失效 →
    缓存验证拒绝 → 转策略重修（知识库非"永久正确答案"）
  - 用例使用独立内存知识库自证链路（不受 session 级共享库初始状态影响）
  - 验收：`-m e2e test_knowledge_reuse` 2 passed；全量 e2e 回归通过
- [x] **T8 · Playwright 原生定位替代/校验 HTMLParser 重解析**（P7）✅ 2026-08-28
  - 新增原生解析路径 `agent/dom/parser.py::parse_interactive_elements_native`（浏览器端一次性
    取回交互候选）+ `interactive_candidates(scene)` 入口（策略链优先原生、静态解析兜底）
  - 采集器 `collector.capture()` 做两来源**交叉校验**（`cross_validate_interactive` 按稳定定位器
    对齐），差异记 warning + `Scene.dom_cross_check` 供报告溯源；原生解析失败降级静态、零行为变化
  - 静态 HTMLParser 保留为单测主力路径（无浏览器）；指纹/LLM 提示/score_selector 保持静态（确定性依赖）
  - 验收：`test_dom_native_check.py` 9 项单测（一致/单侧缺失/空集/裸元素/候选入口/native 降级）+
    `test_native_dom_e2e.py` 3 项 e2e（演示页/弹窗页两来源一致、heuristic 原生候选路径）；
    全量 unit 234 passed、ruff 全绿

## ⚪ 低优先级（清理 / 可维护性）

- [x] **T9 · 对齐 config 与 example yaml**（P6）✅ 2026-08-05（即评审 #16）
  - 移除 example 的 execution/reporting 死配置段；`Settings` 加 `extra='forbid'`，未来漂移加载期报错
  - 位置：`src/selfheal/config.py`、`config/settings.example.yaml`
- [x] **T10 · 工具模块归位**（P8）✅ 2026-08-28
  - `agent/dom.py` → `agent/dom/` 子模块（A5）已完成；本轮补完 `agent/llm_io.py`：
    LLM I/O 基础设施（build_compact_dom / extract_json / safe_float / safe_str）迁至
    **`llm/io.py`**（随模型抽象层归位：模型通信的输入准备与输出编解码）
  - 调用方（diagnose_llm / semantic / visual / test_llm_io）全部改走新路径；
    `agent/llm_io.py` 降为 **shim**（带 TODO(workaround) 标记，确认无外部引用后删除）
  - 顺带清理 `config.py` 过期 TODO 注释（T9 已闭环：execution/reporting 决策不建模，extra=forbid 防漂移）
  - 验收：全量 unit 234 passed、ruff 全绿（迁移零行为变化）
- [x] **T11 · collector 补 trace / network 采集** ✅ 2026-08-28
  - network（C5）✅（2026-08-05）：`Scene.network_logs` 随现场带出近期请求/响应（限长 200）
  - trace 内联 ✅：`SceneCollector._try_inline_trace`——外层录制中（`--trace-healing` /
    browser.trace）时把当前片段导出为独立现场 trace（`trace_dir/inline-trace-<uuid>.zip`，
    可 show-trace 回放）填入 `Scene.trace_path`（不再占位）并恢复录制；未录制不擅自启动
    （探测基于 Playwright stop 未 start 抛错，实测确认）；trace_dir 由 ContextAssembler 注入
    settings.browser.trace_dir；与 conftest 整用例录制职责互补、互不冲突
  - 位置：`src/selfheal/collect/collector.py`（SceneCollector）、`agent/context.py`
  - 验收：`test_collector.py` 扩展 4 项单测（fake tracing：未录制/落盘+恢复/恢复失败保路径）+
    `test_trace_inline_e2e.py` 3 项 e2e（录制中落盘/恢复后再采集新文件/未录制占位 None）；
    全量 unit 238 passed、ruff 全绿
- [x] **T12 · orchestrator 职责瘦身**（P8）✅ 2026-08-05（A1 重构）
  - 已落地：拆分 ContextAssembler（context.py）/ FixGenerator（fix_generator.py）/ PersistenceHandler（persistence.py），
    orchestrator 降为 Router + 组合根
  - 位置：`src/selfheal/agent/orchestrator.py`
- [x] **T18 · Allure 报告增强（环境页/标签/证据链/Pages 发布）** ✅ 2026-08-31
  - 新增 `reporting/allure_bridge.py`（**零侵入**，同 D5 精神）：模块级 `_HAS_ALLURE` 一次性
    探测依赖，未装 allure-pytest 全 API 降级 no-op；write_environment（环境页）/
    apply_dynamic_labels（marker→标签，优先级 **healing > e2e > unit** 取唯一 feature）/ attach_json/attach_file
  - 接线：conftest autouse fixture 动态打标签（dynamic API 须测试上下文）+ sessionfinish
    写 environment.properties；`_attach_trace_to_allure` 统一走 bridge
  - 证据链：自愈记录 JSON 附件（`reporting/hooks.py`）增 `verified_by_selector_exists`
    字段（**复用 T16 verified 布尔，不额外采集**）；trace zip 附件保留
  - CI 新增 `publish` job：合并 unit/e2e results → simple-elf/allure-report-action（历史趋势）
    → peaceiris 发布 **gh-pages**（分支已建：占位页 + .nojekyll）；仅 main 分支发布
  - 验收：`test_allure_bridge.py` 14 项单测（优先级/环境页/附件/降级分支）+ 本地
    `--alluredir` 实测（标签、自愈记录附件、环境页均落盘）+ 全量 unit/e2e 回归、ruff 全绿

## 🚀 后续规划（T19+，2026-08-31 性价比评估立项）

> 评估口径：**难度**（工作量+技术风险）× **回报**（价值/使用频次），仅收录高性价比项。
> 实现顺序即编号顺序：**T19 → T20 → T21 → T22 → T23**；候补与落选记录见本节末尾。

- [x] **T23 · 被测系统迁移：管伊佳 ERP（jshERP）**（难度 ★★★ / 回报 ★★★★★）🟠 P1+P2 完成 2026-08-31
  - **P0 勘测**（实测结论沉淀于 `tests/e2e/api/erp_client.py` docstring）：
    前端为 **Ant Design Vue**（非 Element-UI）；登录 `POST /user/login` **password 需 MD5** 后提交；
    鉴权头 **X-Access-Token**；登录输入框稳定 id（#loginName/#password）；内容区不在 iframe；
    **验证码已被用户关闭**；商品弹窗表单全带稳定 id + placeholder（heuristic 语义匹配依据）
  - **P1 基建**：`SutConfig`（前端/后端/凭证 env 参数化）+ `tests/e2e/api/erp_client.py`
    （标准库客户端：登录 + 商品/供应商造数清理，删除走 deleteBatch 实测可用）+
    conftest `erp_page`（测试账号 UI 登录态 + HealingPage 自愈开）/`erp_admin`（admin 造数）
    fixtures（凭证缺失自动 skip）+ marker `erp`（CI 门禁 `-m "e2e and not erp"`）；
    登录冒烟 1 passed（3.88s）
  - **P2 自愈场景**：`test_erp_material_add_with_healing`——商品新增弹窗内**前端改版注入**
    （#name → #name-v2，与真实"前端升级改 id"同构）→ 旧定位器失效 → 自愈凭
    description="商品名称" 重定位 → 保存落库 → API 断言 + 自愈记录 + 数据清理。
    **实测 PASSED（22.96s）**，且知识库 L1 命中实锤（二次失效 `knowledge 1.0` 直达）
  - 踩坑记录：antd 弹窗默认不销毁（DOM 残留污染定位与自愈候选）→ 单弹窗内完成改版演示；
    intro.js 新手引导遮罩全页遮挡（covered）→ `dismiss_intro` DOM 移除（display:none 有
    可见性判定副作用）；vue-router 异步跳转需 `wait_for_url`
  - ⏸ **供应商场景下轮补**：jsh 测试账号无供应商菜单权限、admin 直达路由渲染空白
    （需菜单点击导航 POM，菜单为 JS 动态无 href）；API 造数/清理已就绪
  - **角色语义修正（专家确认 2026-09-01）**：admin = 平台运维用户（仅平台菜单配置/创建
    租户，不能编辑任何业务数据，测试基建不得使用——此前误用 admin 造数已纠正）；租户
    （jsh）= 业务数据管理员，UI 测试与 API 造数/清理统一租户凭证
    （SutConfig 简化为 username_env/password_env，erp_admin → erp_api）
  - **VLM 配置修正（2026-09-01）**：端点迁至阿里云百炼 MaaS 专属端点 + 模型升级
    qwen3-vl-flash → **qwen3-vl-plus**（备选 qwen3.8-flash）；`VisionConfig` 增
    `timeout_s=60` / `max_tokens=1000` 可配置（plus 响应慢于 flash，原 20s/500 曾致
    VLM 调用超时/截断而自愈失败）——修正后 ERP 自愈场景恢复 PASSED（23.51s）

- [ ] **T19 · 自愈回归通知 + 定时回归挂点**（难度 ★★☆ / 回报 ★★★★）🟠 部分完成 2026-08-31
  - ✅ 通知基建：`scripts/notify.py`——`build_summary`（成功率/真自愈率/策略分布/成本统计）
    + `build_payload` 四 provider（钉钉 / 企微 / Slack / generic，纯函数可快照）
    + `send`（urllib best-effort，失败不阻塞 CI）+ `WEBHOOK_URL` 未配置自跳过（exit 0）
  - ✅ 数据流：`conftest.sessionfinish` 落盘 `reports/healing-records.json`（CI artifact
    `healing-records`）；ci.yml 增 `notify` job——main 失败必告警（PR 不打扰），
    成功摘要分支已预留 `schedule` 条件，将来启用 cron 即自动生效
  - ⏸ 定时回归 cron 暂不启用：用户决策"目前不需要进行回归"（2026-08-31）——
    workflow 中 schedule 配置以注释保留（含启用说明），恢复仅需取消注释一行
  - 验收：`test_notify.py` 12 项单测（四格式 / 统计口径 / send mock / skip 与缺摘要降级）；
    全量 unit 264 passed、ruff 全绿；真实 webhook 推送与 e2e 回归留集成阶段
- [x] **T20 · 自愈价值 A/B 对比演示套件**（T3b）（难度 ★★☆ / 回报 ★★★★★）✅ 2026-08-31（含实跑实证）
  - ✅ `tests/e2e/test_ab_scenarios.py`：3 个「UI 改版定位失效」场景（登录按钮 / 用户名框 /
    密码框）× parametrize 双变体同源运行——`disabled` 变体 xfail(strict)（CI 保持绿，
    语义=无自愈需人工修复）、`healing` 变体走自愈（description 与 aria-label 全等，
    确定性 heuristic 命中，不依赖真实模型）
  - ✅ `scripts/ab_compare.py`：两轮定向运行（`-k` 过滤变体 + junitxml 收集）→
    `parse_junit`（区分 xfail 与真 skipped）/ `build_rows`（对齐判定：自愈修复 ✓ /
    自愈未救回 / 非失效场景）/ `render_markdown` → `reports/ab-compare.md`
  - ✅ **实跑实证（集成回归）**：关闭自愈 **0/3** 通过（需人工修复 **3** 次）vs 开启自愈
    **3/3** 通过（0 干预）；自愈成本 ¥0.09（LLM 2 / VLM 1）；结论已贴 README
  - 修正记录：junit 中 xfail 记 `<skipped type="pytest.xfail">`，首轮误判为真跳过——
    解析归类区分后判定正确（`test_ab_compare.py` 7 项覆盖）；stdout GBK 编码容错
  - 全量 unit 293 passed、ruff 全绿
  - 同一组演示场景 ×（开启/关闭自愈）双轮运行 → 对比报告（通过率 / 人工干预次数 / 耗时）；
    README 与汇报直接引用实证数据——**核心卖点"自愈提升稳定性"的量化证据**
  - 位置：`scripts/ab_compare.py` + 复用 `tests/e2e/pages/` 演示页
  - 验收：一键脚本产出对比结论；对比计算逻辑 unit 覆盖
- [ ] **T21 · pytest-xdist 并行兼容**（难度 ★★☆ / 回报 ★★★）🟠 代码+单测并行验证完成 2026-08-31
  - ✅ 诊断：xdist 下每 worker 独立进程——knowledge session 级 tmp 库实际不共享（无并发写冲突），
    **真实 bug 是 `_session_reporters` 聚合**：各 worker 的 sessionfinish 互相覆盖
    dashboard.html / healing-records.json → 记录丢失
  - ✅ 修复：conftest 分片聚合协议——`is_xdist_worker`（workerinput 判定）→ worker 写
    `.healing-shard-<gwN>.json` 分片 → controller 合并分片 + 本地记录后统一写
    dashboard / healing-records.json 并清理分片；无 xdist 单进程路径行为不变（零回归）
  - ✅ SQLite 防御：`connect(timeout=30)` + `PRAGMA busy_timeout=30000` + `journal_mode=WAL`
    （多进程共享库场景的写排队 + 读写并发；临时库/单进程无害）
  - ✅ pyproject dev 显式声明 `pytest-xdist>=3.5`
  - 验收：`test_xdist_compat.py` 12 项单测（worker 判定/分片读写清理/聚合出口/pragma 生效/
    双连接交错写）+ `pytest -m unit -n 2` 全量 282 passed + **`pytest -m e2e -n 2` 全量
    23 passed / 3 xfailed（2026-08-31 集成回归）**：xdist 下 dashboard/healing-records.json
    由分片正确聚合（各 worker 自愈记录不丢）、allure results 130 文件无冲突、并行提速约 2 倍
  - 现状：环境装有 xdist 但 pyproject 未声明；多 worker 下 SQLite 并发写、
    `_session_reporters` 聚合、Allure results 合并行为均未验证
  - 内容：`-n 2` 全量验证 → 修 SQLite（WAL / 写冲突重试）+ 会话聚合策略（xdist worker 协议）
    → pyproject 显式声明依赖
  - 验收：`pytest -m unit -n 2` 与 `pytest -m e2e -n 2` 全绿且看板/Allure 产物完整
- [x] **T22 · 修复建议自动开草稿 PR**（难度 ★★☆ / 回报 ★★★）✅ 2026-08-31
  - ✅ `scripts/propose_pr.py`：`load_proposals`（读 T15 产物 JSON，坏文件跳过）+
    `build_pr_body`（人审 checklist 头 + 摘要表，竖线转义与 T15 同款）+
    `has_open_pr`（label 查重防重复开）+ `create_draft_pr`（gh CLI best-effort）；
    标准库零项目依赖（propose-pr job 不装包），全链路安全降级 exit 0
  - ✅ CI：e2e 步骤注入 `healing.fix_proposals=true`（config/settings.yaml 单键覆盖）产出建议 →
    上传 artifact → `propose-pr` job（main 且 e2e 成功时）：产物提交到 `selfheal/proposals`
    分支（git add -f，reports 在 gitignore）→ gh 开**草稿** PR（人审 checklist 内置，
    绝不自动合并，守 T15 边界）
  - 验收：`test_propose_pr.py` 10 项单测（产物加载/body 组装与转义/查重降级/gh mock）；
    全量 unit 292 passed、ruff 全绿；真实开 PR 需仓库 GITHUB_TOKEN 权限（CI 首跑验证）
  - CI 读 fix-proposals JSON → 自动开**草稿** PR（body 含人审 checklist）；仍不自动合并
    （守 T15 人审边界）
  - 验收：PR body 生成逻辑 unit；实际开 PR 在 feature 分支验证后合入

### 候补池（性价比尚可，待前置条件）
- **iframe / Shadow DOM 自愈**：真实场景大空白（`healing_locator.py:73` 明示原生透传），
  但跨 frame 候选解析/存在性验证是架构级改动——先做评估 spike，再决定是否立项
- **自愈指标跨运行历史**：价值依赖持续运行（T19 先行）→ dashboard 时间序列
- **知识库运维 CLI**（list / 去重 / 失效清理）：知识库长期运行的卫生问题

### 落选记录（本轮不立项，理由存档）
- 语义化 v2 fastembed / T5 收缩标定：依赖真实场景数据沉淀，当前无数据支撑收益
- 多浏览器矩阵 / action_wait 默认翻转：待实测数据决定，现做收益不明确
- 数据驱动 / 软断言 / 多环境 profile / 版本发布管理：用例规模与协作需求上来再做

## 🛡️ 风险控制（对应 `docs/architecture.md`「预期与风险」，Phase 5 D 已完成 2026-08-05）

- [x] **T13 · 高风险页豁免配置**：`healing.exclude_url_patterns`（glob），orchestrator 开头按 URL 匹配命中即不触发自愈（支付 / 强授权 / 审计类页面）。位置：`config.py`、`agent/orchestrator.py`。验收：`test_risk_control.py` 4 项。
- [x] **T14 · dry-run"仅报告不执行"模式**：`healing.dry_run`，只生成修复建议（写 fix-proposals）、不换定位器重试、不持久化知识；返回 `proposed_selector` 供人审。验收：`test_risk_control.py` 3 项。
- [x] **T15 · 修复写回代码的人审清单**：`reporting/fix_proposals.py::write_fix_proposal` 输出「原→新」PR 化建议（Markdown + JSON，`applied=false` 不自动改库）；`healing.fix_proposals` 开启。验收：`test_risk_metrics.py`。
- [x] **T16 · 看板区分"真自愈 vs flaky 侥幸通过"**：`HealingRecord.verified`（修复后原定位器仍失效=真修复，已恢复=flaky）；metrics 增 verified/flaky/verified_rate，dashboard 增卡片与审计列。验收：`test_risk_metrics.py`。
- [x] **T17 · 多模态成本看板化**：orchestrator 计数代理统计 LLM/VLM 真实调用 → `HealingReporter.stats`/`cost_summary()`，dashboard 渲染成本卡片；`estimate_cost` 可单测。验收：`test_risk_metrics.py`。

## ✅ Phase 5 A · 知识库语义化（已完成 2026-08-05）

- [x] **A1 Embedding 抽象**：`llm/embedding.py::NgramEmbedding`（md5 确定性 n-gram 向量，零网络/零费用/<10ms）+ `EmbeddingConfig`。验收：`test_embedding.py` 6 项。
- [x] **A2 存储/检索**：`RepairCase` 扩展（page_fingerprint/repair_key/embedding/embedding_version/hit_count/is_verified/created_at）；SQLite/内存 `find_by_repair_key`（L1）+ `find_semantic`（L3，page 分桶+numpy 余弦）+ `bump_hit`/`set_verified`。验收：`test_knowledge_semantic.py` 10 项。
- [x] **A3 orchestrator 接线**：L1 硬短路 + L3 进策略链（`semantic` 升级为向量检索，LLM 兜底）+ 失败上下文三级回退提取（live→快照→静态，带缓存）+ persist 富化 + review-queue 人审清单。验收：`test_orchestrator_semantic.py` 14 项。

## 已完成（存档）

- [x] **2026-08-15 · Code Review 全量审查 + 加固（P0–P2）** ✅ 2026-08-15
  - 全量审查：`docs/reviews/2026-08-15-code-review.md`（3 个实证缺陷 + 4 项护栏 + 工程清理）
  - P0 修复：DOM parser void 元素文本污染（C1）/ SQLite NULL 指纹去重失效（C2）/ 向量维度错配崩溃（C3，embedding_version 含 dim）/ 策略链异常隔离（C4）
  - P1 加固：L3 候选失效验证（M1）/ 弹窗特征 text-aria 投影（M2）/ 资源生命周期 close 链（M3）/ 上下文缓存 URL 键 + LRU（M4）
  - 验收：unit 200 passed（+24 回归）、e2e 7 passed、ruff 全绿
- [x] Phase 3 评审加固：弹窗去"取消/cancel"、关闭态零副作用、find_repair 语义对齐、_persist 保护、wait_until_stable 总超时、SQLite 上下文管理器（2026-08-04）
