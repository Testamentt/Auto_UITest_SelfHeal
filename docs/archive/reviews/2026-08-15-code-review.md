# Code Review — 全量审查（2026-08-15）

> 审查范围：`src/selfheal/` 全部源码 + `tests/` 全部测试 + 工程配置与文档合规。
> 方法：逐文件精读（91 个文件 ≈ 7,300 行），关键缺陷经运行脚本**实证复现**。
> 基线：`ruff check .` 全绿；`pytest -m unit` 176 passed / 7 deselected。
> 关联：修复计划见 `docs/plans/2026-08-15-code-review-fixes.md`（待实施）。

---

## 一、总体评价

代码质量明显高于一般骨架期项目：

- **架构**：A1 重构后 orchestrator 降为 Router + 组合根，三个协作者（ContextAssembler / FixGenerator / PersistenceHandler）职责清晰；策略可插拔、知识库双实现（D10）、llm 抽象 + registry + openai SDK 惰性导入（D7）落实到位。
- **设计亮点**：`extra='forbid'` 防死配置漂移；B1"验证后才沉淀"（暂存 → commit）；T16 真自愈 vs flaky 区分；C4 跨策略一致性降权（防 VLM 自报虚高）；D9 防幻觉护栏（selector 必须真实存在）；阈值防倒挂 validator。
- **异常路径**：大量 `best-effort + logger.warning`，符合 R4"不静默吞错"。
- **测试**：176 个单测覆盖深（链式重放、B1 幂等提交、T4 二次自愈、T13/T14、L1/L3 采纳规则、C4 降权、B4 L1 键门控）；e2e 冒烟无 key 自动 skip，CI 两阶段合理。

但存在 **3 个已实证的功能缺陷**（集中在 DOM 解析与 SQLite 层，恰是两条核心数据链路），以及若干护栏不对称与资源管理问题。

---

## 二、Critical（实证缺陷）

### C1 · `agent/dom/parser.py`：void 元素残留栈 → 文本污染全部候选（影响面最大）

`_DOMParser.handle_starttag`（39–42 行）对 `<input>` 等无结束标签的 void 元素同样压栈且永不弹出，导致后续整个文档文本被反复汇聚进第一个未闭合的 void 元素。

**实证**（浏览器 `content()` 序列化风格：void 元素无自闭合斜杠）：

```
<input data-testid="username"> <button>登录</button> <div><p>说明文字</p></div>
→ input.text = '\n登录\n说明文字\n'   （正确应为 ''）
```

- 真实页面经 Playwright `content()` 抓取的 DOM 中 void 元素**必然无斜杠** → 该 bug 在真实自愈闭环中必然触发。
- **影响**：heuristic `_score` 的 text 字段失真（输入框因文本重叠被误判高相似候选）；`build_stable_selector` 为无 testid/id 的元素生成超长 `text="..."` 定位器；semantic/visual 的候选索引同样被污染。
- **漏网原因**：测试 DOM 全部用自闭合 `<input .../>`（走 `handle_startendtag` 不入栈），恰好绕开此路径。
- **修复方向**：void 标签集合（area/base/br/col/embed/hr/img/input/link/meta/param/source/track/wbr）不入栈；`handle_endtag` 校验 tag 匹配，不匹配即丢弃。

### C2 · `knowledge/sqlite_store.py`：UNIQUE 索引对 NULL 失效 → 去重承诺落空

`idx_repairs_unique ON (original_selector, new_selector, dom_fingerprint)`（40–41 行）：SQLite UNIQUE 对 NULL 不生效（NULL ≠ NULL）。

**实证**：`dom_fingerprint=None` 连续 `add_repair` 3 次 → 表内 3 行（#10 期望 1 行）；混合插入后 4 行。

- **影响**：页面无可交互元素（指纹为 None）时重复修复无界增长，违背 #10"记录不无界增长"设计。
- **修复方向**：插入时 `dom_fingerprint or ""` 归一化（查询侧对称）。

### C3 · `find_semantic` 向量维度不匹配即崩溃 + 无策略级异常隔离

`llm/embedding.py` 的 `embedding_version = "v1-ngram"`（41 行）**不含 dim**——用户改 `embedding.dim` 后同版本号下新旧向量维度不同。

**实证**：512 维入库、256 维查询 → `ValueError: matmul ... size 256 is different from 512`。

**放大链（C4）**：`find_semantic` 抛异常 → `SemanticStrategy.repair` 抛异常 → **`FixGenerator.best_candidate`（152–176 行）对 `repair()` 无任何 try/except** → 整条策略链中断、visual 被跳过 → 顶层兜底把根因污染为 `heal_internal:ValueError`。

- **修复方向**：`embedding_version` 含 dim（如 `v1-ngram-512`）；`find_semantic` 对长度不符的行跳过；`best_candidate` 加 per-strategy try/except + warning。

---

## 三、Major

| # | 位置 | 问题 |
|---|---|---|
| M1 | `agent/strategies/semantic.py::_knowledge_semantic`（96–125 行） | **L3 命中无 `selector_exists` 护栏**：知识库 `new_selector` 可能已再次失效，直接返回必然重试失败、依赖二次自愈白兜一轮。与 L1 路径（`fix_generator.lookup_knowledge` 有护栏，136 行）**不对称**。 |
| M2 | `engine/popup_guard.py::_stable_selector`（151–167 行） | 只投影 `data-testid`/`id`，**未投影 text/aria-label** → 纯文本"关闭"按钮永远无法沉淀弹窗特征（`build_stable_selector` 返回 None），每次都要启发式重找。demo 页关闭按钮恰好有 testid 故 e2e 未暴露。 |
| M3 | 全局 | **资源生命周期缺失**：`SqliteKnowledgeStore` / `OpenAICompatibleLLM` / `OpenAICompatibleVLM` 从不 close——`HealingPage`（`healing_locator.py` 372–393 行）、`SelfHealOrchestrator`、conftest session 级 `knowledge` fixture（`tests/conftest.py` 60–65 行）均无关闭路径。长进程下 fd/sqlite 锁累积（`scripts/demo_semantic_reuse.py` 注释自认"Windows 下不关会锁文件"）。 |
| M4 | `agent/context.py::ContextAssembler._failure_context_cache`（93、117–123 行） | 缓存**只以 selector 为键**：SPA 多页面流程同 selector 不同元素 → 误用旧上下文；**无上限**，长流程内存膨胀。 |

---

## 四、Minor / Nit

**Minor**
- m1 `agent/diagnose.py`（23、34–36 行）：主键正则 `[A-Za-z][\w-]*` 不支持中文 id（`#登录按钮` → 恒判 unknown）；子串匹配对短 key（如 `id`）误判率高。
- m2 `reporting/fix_proposals.py::write_fix_proposal`（67–82 行）：文件名秒级精度 + selector 截断 → 同秒同 selector 覆盖；`stamp` 与 `record["created_at"]` 取两次 `_now()` 不一致。
- m3 `config/settings.example.yaml`：healing 段缺 `llm_diagnose_threshold`（T9 对齐不完整）。
- m4 `agent/orchestrator.py::run`（110–111 行）：T13 豁免命中直接 return，**无审计记录**——"只报告不动作"的"报告"未落报告。
- m5 `knowledge/store.py`（59 行）：死代码 `return None`（不可达）。
- m6 `llm/registry.py`（4 行）：TODO 注释过时（provider 早已确定）。
- m7 `llm/embedding.py`（21 行）与 `agent/strategies/heuristic.py`（24 行）：`_TOKEN_RE` 中文分词粒度不一致（单字 vs 连续片段），两处重复定义易漂移。
- m8 `engine/healing_locator.py::_is_timeout_error`（50–54 行）：按类名含 "Timeout" 粗判，业务异常可能误触发自愈。
- m9 `collect/collector.py`（47–58 行）：网络监听挂载后从不卸载，同 page 多 collector 时重复监听累积。
- m10 文档：`CLAUDE.md`（114 行）仍写"当前仓库为骨架阶段"——与 Phase 5 完成态不符（R5 文档同步违规）。

**Nit**
- `agent/orchestrator.py`（42 行）：`_STRATEGY_REGISTRY: dict[str, type]` 应收窄为 `Type[RepairStrategy]`。
- `engine/healing_locator.py`（299 行）：`wrapped.__name__` 未同步 `__doc__`。
- `agent/dom/selector_builder.py`（16 行）：含 CSS 特殊字符的 id 未转义。
- `reporting/dashboard.py::_render_cost`（44–46 行）：直接下标访问 cost dict（依赖 `estimate_cost` 返回契约）。

---

## 五、测试体系评价

**优点**：覆盖深、隔离好（monkeypatch `FIX_PROPOSALS_DIR`/`_STRATEGY_REGISTRY`）、fake LLM/VLM 简洁、e2e 冒烟不阻塞 CI。

**缺口（与缺陷一一对应）**：

| 缺口 | 对应缺陷 |
|---|---|
| parser 测试数据全用自闭合 void 元素 | C1 漏网 |
| upsert 去重测试只用非 NULL 指纹（`test_add_repair_upsert_dedup`） | C2 漏网 |
| L1 有缓存失效测试但 L3 无对应测试 | M1 漏网 |
| popup_guard 只测纯函数（`_normalize_signature`/`_is_close_hint`） | M2 漏网 |
| 无"策略抛异常不中断链"测试 | C4 漏网 |
| 无资源关闭相关测试 | M3 漏网 |

**建议**：为 C1–C3 与 M1/M2 各补一个回归测试（尤其 C1 用无斜杠 `<input>` 构造 DOM）。

---

## 六、规则合规检查

| 规则 | 结论 |
|---|---|
| R2 测试双覆盖 | ✅ unit/e2e 分层与 marker 正确，176 单测全绿 |
| R3 临时方案管理 | ✅ 无未回收 workaround（无 `TODO(workaround)` 标记） |
| R4 代码质量 | ✅ docstring 覆盖好、best-effort 均记日志；⚠️ `_attach_allure`（`reporting/hooks.py` 58 行）与 `pytest_sessionfinish`（`tests/conftest.py` 178 行）有裸 pass |
| R5 变更沉淀 | ⚠️ roadmap/TODO 同步良好，但 CLAUDE.md 停留在骨架期 |

---

## 七、修复优先级

1. **C1** parser void 元素 → 影响所有策略候选解析，优先修 + 回归测试；
2. **C2** NULL 去重 → 一行归一化；
3. **C4 + C3** 策略链隔离 + 向量维度校验 → 防御纵深，一次改动；
4. **M1** L3 护栏 → 与 L1 对称；
5. **M2** 弹窗特征沉淀 → 补 text/aria 投影；
6. **M3** 资源管理 → 补 close/上下文管理器 + fixture finalizer；
7. 其余 minor 随迭代清理（m4 豁免审计、m3 example 补键、m10 CLAUDE.md 更新）。
