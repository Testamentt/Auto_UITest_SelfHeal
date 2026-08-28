# TODO — 优化与待办清单

> 来源：`docs/reviews/2026-08-04-phase3-retrospective.md` · 全量评审 `docs/reviews/2026-08-15-code-review.md` · 随进展勾选/更新（R5 活文档）
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

## 🟡 中优先级

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
- [ ] **T7 · 知识二次命中 e2e（坐实"越用越聪明"）**（P9）
  - 修复一次 → 第二次同场景命中知识缓存直接复用
  - 位置：`tests/e2e/`
- [ ] **T8 · Playwright 原生定位替代/校验 HTMLParser 重解析**（P7）
  - 用原生查询替代或交叉校验自写 DOM 解析，提升真实 DOM 鲁棒性
  - 位置：`src/selfheal/agent/dom.py`（现已拆为 `agent/dom/` 子模块）
  - 🟠 部分缓解：2026-08-15 修复 HTMLParser void 元素文本污染（审查 C1）+ endtag 错配防御，原生定位替代仍待办

## ⚪ 低优先级（清理 / 可维护性）

- [x] **T9 · 对齐 config 与 example yaml**（P6）✅ 2026-08-05（即评审 #16）
  - 移除 example 的 execution/reporting 死配置段；`Settings` 加 `extra='forbid'`，未来漂移加载期报错
  - 位置：`src/selfheal/config.py`、`config/settings.example.yaml`
- [ ] **T10 · 工具模块归位**（P8）🟠 部分完成（A5 已拆包 2026-08-04）
  - `agent/dom.py` → `agent/dom/` 子模块（解析/指纹/定位器/提取，A5）已完成；`agent/llm_io.py` 未动
  - 完整目标（迁至 `collect/` 或独立 `utils/`）仍待办
- [ ] **T11 · collector 补 trace / network 采集** 🟠 部分完成
  - network（C5）✅ 已实现 2026-08-05：`Scene.network_logs` 随现场带出近期请求/响应（限长 200）
  - trace 内联 ⬜ 待办：`Scene.trace_path` 仍为占位；录制已由 conftest `--trace-healing` 完成
  - 位置：`src/selfheal/collect/collector.py`
- [x] **T12 · orchestrator 职责瘦身**（P8）✅ 2026-08-05（A1 重构）
  - 已落地：拆分 ContextAssembler（context.py）/ FixGenerator（fix_generator.py）/ PersistenceHandler（persistence.py），
    orchestrator 降为 Router + 组合根
  - 位置：`src/selfheal/agent/orchestrator.py`

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
