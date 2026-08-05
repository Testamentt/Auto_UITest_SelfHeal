# 合并问题清单 + 分阶段修改方案（定稿）

> 来源：2026-08-05 全项目复盘 + 两轮源码核实。活文档，随实施勾选更新（R5）。
> 状态：**Phase 1（正确性修复 P1–P4）全部完成 ✅**：P1（C1+C2+C6）· P2（C3+B2+B4+C7）· P3（B1）· P4（C4）。
> 下一步：Phase 2（架构收敛 A6→A2→A1）。实施进度见「五、下一步计划」。

## 当前目标

修复全项目复盘发现的问题：正确性组（C1–C7 中与行为相关者、B1/B2/B4）→ 架构收敛（A1/A2/A6）→ 低风险清理（A3/A5）→ 展示与一致性（C5/B3/B5/B6）。

## 关键约束

- 遵循 `RULE.md` R1–R6；每阶段结束跑 `pytest -m unit` + `ruff check .` 门禁。
- 模型调用一律经 `llm/` 抽象层；配置集中在 `config.py`。
- 不引入大拆大建：正确性修复先行，架构收敛吸收已修复行为，避免返工。

## 一、确认问题清单（18 项）

### 正确性组

| 编号 | 问题 | 判定 | 严重度 |
| --- | --- | --- | --- |
| C2 | 链式定位器盲区：`first/nth/locator/filter/get_by_*` 返回原生 Locator，动作自愈静默失效 | ✅ | 🔴 |
| B1 | 验证与沉淀顺序相反：`_persist` 先于回试验证，未验证修复可被 L1 复用 | ✅ | 🔴 |
| C3 | 诊断成本错配 + repair_key 含 text 脆弱（"降级跑 L3/L4"已修正为 L2 先跑） | ✅ | 🟠 |
| B2 | T17 成本漏计诊断 LLM 调用 | ✅ | 🟠 |
| C4 | 视觉置信度虚高：护栏只验成员资格不验正确性（空候选集不崩溃，已修正） | ⚠️ 部分 | 🟠 |
| C1 | 伪"零侵入"：无 description 时 L3-LLM/L4 硬禁用（L2 仍可跑，非全失效） | ⚠️ 部分 | 🟠 |
| C6 | auto_description 链描述膨胀稀释意图（L2 词元重叠 / L3 embedding 噪声；非"上下文溢出"） | ✅ | 🟡 |
| C7 | 低置信度成功修复（0.6–0.75）缺 LLM 归因，人审清单质量受限 | ✅ | 🟡 |

### 架构组

| 编号 | 问题 | 判定 | 已有跟踪 |
| --- | --- | --- | --- |
| A6 | Metrics 魔法字符串（`metrics["total"]` 等） | ✅ | — |
| A2 | 参数游荡 + `run()` 实例状态异味（参数个数 3 非 6，已修正） | ⚠️ 部分 | — |
| A1 | 上帝编排器 405 行，SRP 违例 | ✅ | T12 |
| A5 | dom.py 混合 4 类职责（**292 行非 760**） | ⚠️ 部分 | T10 + 评审 #6 |
| A3 | 知识库 3 条查询路（if/else 实为 2 分支；`find_repair` 是兜底非纯冗余） | ⚠️ 部分 | 评审 #21 |
| A4 | Embedding 归位（knowledge/ 已零依赖 EmbeddingClient，解耦已达成）——**已决策跳过** | ⚠️ 前提真 | — |

### 展示 / 一致性组

| 编号 | 问题 | 判定 | 严重度 |
| --- | --- | --- | --- |
| C5 | `network_logs`/`trace_path` 占位 | ✅ | 🟡 |
| B3 | HTML 看板无运行时调用点 | ✅ | 🟡 |
| B4 | L1 repair_key 读写门控不对称 | ✅ | 🟡 |
| B5 | 文档漂移（`reporting/__init__.py:5` 陈旧 TODO、diagnose docstring 虚报 not_visible） | ✅ | ⚪ |
| B6 | 内存库 `last_hit_at='now'` 失真；L3 人审清单由策略层落盘 | ✅ | ⚪ |

## 二、已确认决策

1. **B1**：`commit_pending(attempt_id, outcome)` —— run() 生成 attempt_id，pending 按 id 存；commit 按 id 幂等（已提交/未知 id → no-op），防引擎异常重复提交。
2. **C3**：repair_key = md5(page_fingerprint|tag_path)（tag_path 先加 `nth-of-type` 索引、深度 4→8 再去 text），key 加 `v2:` 版本前缀防旧库残留误命中。
3. **C2**：第一次自愈修根+重放链；第二次自愈直接修叶子（覆盖中段断裂）。
4. **范围**：跳过 A4；A3 走折中（收敛接口、阈值/缓存验证决策留在编排器）。
5. **C6**：`_build_auto_description` 截断链描述保留最近 3 层 + `…`；**截断仅限描述，链重放全量**。
6. **C7**：诊断触发阈值 `llm_diagnose_threshold`（config，默认 0.75）；低置信成功补 LLM 归因 + 受 `healing.fix_proposals` 开关控制的 fix-proposal；加 stats 计数器事后验证降频。

## 三、修改方案（分 4 阶段 7 工作包）

### Phase 1 · 正确性修复

**P1 · 自愈触发层加固（C1 + C2 + C6）**
- 文件：`engine/healing_locator.py`（主）、`agent/strategies/semantic.py`、`visual.py`（门禁不改）
- 变更：
  1. `chain_ops: tuple[tuple[str, tuple, dict, bool], ...]`（`(name, args, kwargs, is_property)`，不可变元组派生）
  2. `__getattr__` 分 `CHAIN_METHODS`（lambda 包裹）/ `CHAIN_PROPERTIES`（直接 `_chain`）；`_chain()` 只包裹 `isinstance(result, Locator)`，FrameLocator/标量透传
  3. 引入 selector 的链环（`locator(x)`/`get_by_role(...)`）新实例 `_selector` = 子 selector；纯定位 op（`first/last/nth/filter`）保留父 `_selector`
  4. 重试：第一次修根+重放链（property 走 getattr、method 走调用）；第二次修叶子（`use_knowledge=False`，链意图入 description）
  5. `_build_auto_description`：description 为空时生成（selector + page_url + 链最近 3 层自然语言 + `…`），传入 `run()`；**重放仍全量**
- 测试：property/method 重放、链派生不污染、根断裂、中段断裂、FrameLocator 透传、auto_description 流入、深链（5+ 层）描述截断但重放正确

**P2 · 诊断与成本收敛（C3 + B2 + B4 + C7）** ✅ 已完成 2026-08-05
- 文件：`agent/orchestrator.py`、`agent/dom.py`、`config.py`（新增 `llm_diagnose_threshold`）、`knowledge/*`、`scripts/demo_semantic_reuse.py`、`tests`
- 变更：
  1. 诊断拆分：`_rule_diagnoser` 恒跑；`_llm_diagnoser` 仅当 `best is None or best.confidence < llm_diagnose_threshold(0.75)` 触发；失败路径补 `_emit_proposal`
  2. B2：`_llm_diagnoser` client 用 `_counting_client` 包裹；新增 `stats['llm_diagnoses']` 计数器
  3. repair_key：tag_path 加 `nth-of-type` 索引（live JS + bs4 同步）、深度 4→8 → 去 text → `v2:` 前缀；调用方（L1 lookup、`_persist`、demo 脚本、tests）同步
  4. B4：`_persist` 把 L1 key 写入从 embedding 门控拆出
  5. C7：低置信成功（conf∈[0.6,0.75)）→ LLM 归因入 root_cause + 受 `fix_proposals` 开关的 fix-proposal
- **实施中发现并修复的设计修正**：v2 去 text 后，静态兜底上下文（tag_path 为空）若照常计算 L1 键，会使同页所有静态失败折叠成 `md5(page_fp|"")` 同一键 → e2e 跨用例误命中（`#ghost-btn-old` 命中了 `#submit-btn-old` 的缓存修复）。修复：**L1 键只在 tag_path 非空（有结构上下文）时计算**，查询侧与写入侧门控对称；静态上下文改走旧式 `find_repair` 复用。demo 场景 2 相应改为"知识库复用（旧式检索）"。
- 测试：`test_diagnosis_reorder.py`（高置信不触发/0.62 触发/失败触发/低置信建议人审/无 LLM 降级/阈值校验/B4/B4 碰撞防护）+ repair_key 相关既有测试更新

**P3 · 验证与沉淀闭环（B1）** ✅ 已完成 2026-08-05
- 文件：`agent/orchestrator.py`、`engine/healing_locator.py`、`tests/unit/test_persist_after_verify.py`、适配 `test_persist_failure`/`test_risk_metrics`/`demo`
- 变更：`HealOutcome` 加 `attempt_id`；run() 成功路径把 `(outcome, scene, selector, 指纹, ctx)` 暂存 `self._pending`（不立即写库，上限 64 条防膨胀）；新增 `commit_pending(attempt_id)` 幂等（`_committed` 集合 + 未知 id no-op）执行 `_persist`+`_record`；`_heal_and_resolve` 记录 `_last_heal_outcome`，`_healing_action` 在**任意一次重试成功后** `_commit_success()` 提交，全部重试失败不提交 → 未验证修复不入库。L1 缓存命中路径不暂存（仍即时 `_record`）、dry_run 不受影响。
- **契约变更**：直接调用 `run()` 的调用方（测试/脚本）须在成功后显式 `commit_pending(outcome.attempt_id)`（demo 已适配）。
- 测试：`test_persist_after_verify.py`（run 暂存→commit 落库、重复 commit 幂等、未知 id no-op、L1 命中不暂存、引擎重试成功提交、全部重试失败不提交）

**P4 · 视觉置信度一致性（C4）** ✅ 已完成 2026-08-05
- 文件：`agent/strategies/heuristic.py`、`agent/strategies/visual.py`、`tests/unit/test_visual_consistency.py`
- 变更：heuristic 暴露公共 `score_selector(dom, selector, original_selector, description) -> float`（内部反查 Element 再 `_score`）；visual 融合 `final_conf = round(conf * (0.4 + 0.6 * l2_score), 3)`（反查不到 → 按 0，保守拒绝）；既有 `test_visual_llm.py::test_hit_returns_candidate` 置信度断言同步为融合值 0.854
- 测试：VLM 高置信+L2 低分→0.36 被拒；元素移动属性不变→0.873 不误伤；score_selector 意图匹配/偏离/反查不到；semantic 的 LLM 挑选同样适用（记入 T5，本次未做）

### Phase 2 · 架构收敛（吸收 Phase 1，顺序 A6→A2→A1）

**P5 · 架构收敛（A6 + A2 + A1）**
- A6：`reporting/metrics.py` 定义 `MetricsSnapshot`（dataclass frozen 或 pydantic），`compute_metrics` 返回它，dashboard 改点属性
- A2：`agent/context.py` 定义不可变 `HealingContext`，替换 orchestrator 实例状态与参数列表
- A1（T12）：run() 拆 `ContextAssembler` / `FixGenerator` / `PersistenceHandler`，orchestrator 降 Router

### Phase 3 · 低风险清理

**P6 · 清理（A5 + 评审#6 + A3）**
- A5 + #6：dom.py 拆 `dom/` 包（parser/fingerprint/selector_builder/extractor），popup_guard 重复 selector 统一收编，保留 re-export
- A3：KnowledgeBackend 收敛 `query(QueryRequest)`（内部 L1→legacy 择优），阈值/缓存验证留编排器；`find_semantic` 仍由语义策略直调

### Phase 4 · 展示与一致性

**P7 · 展示与一致性（C5 + B3 + B5 + B6）**
- C5：collector 接 `on_request/on_response` 填 `Scene.network_logs`；`Scene.trace_path` 由 conftest 回填；`_record` 附 trace 进 Allure
- B3：conftest teardown / CI 调 `write_dashboard(records, cost_summary)`，产物上 artifact
- B5：修 `reporting/__init__.py:5` 陈旧 TODO、diagnose docstring
- B6：`store.py` bump_hit 用真实 UTC；L3 人审清单落盘收敛到 orchestrator 出口

## 四、待解决问题

- C7 降频"80%"未经数据验证：上线后以 `stats['llm_diagnoses']` 核对，据此回调 `llm_diagnose_threshold`。
- C6 截断保留"最近 3 层"为经验值，若实际链深度分布不同可调。
- P1 叶子修复目标的生成：`locator(x)`/`get_by_role` 的子 selector 提取规则需在实现时对 Playwright 链语义做边界测试（含 `filter` 无新 selector 的情形）。

## 五、下一步计划

1. 实施顺序：P1 → P2 → P3 → P4（每包后跑门禁）→ 交用户 review → Phase 2（P5）→ Phase 3（P6）→ Phase 4（P7）。
2. 文档与代码同批提交（R5）。
3. 完成后把本清单勾选项同步回 `docs/TODO.md`。
