# 修改计划 — Code Review 修复（2026-08-15）

> 来源：`docs/reviews/2026-08-15-code-review.md`（全量审查报告）。
> 状态：✅ 已完成（2026-08-15）· P0–P2 全部实施并附回归测试。
> 验收：`ruff check .` 全绿 ✅ · `pytest -m unit` 200 passed（原 176 + 新增 24）✅ · `pytest -m e2e` 本机验证中。

## P0 · 正确性修复（必须）

| 项 | 缺陷 | 文件 | 方案 | 测试 |
|---|---|---|---|---|
| P0-1 | C1 DOM parser void 元素文本污染 | `agent/dom/parser.py` | void 标签不入栈；endtag 与栈顶不匹配时忽略 | 新建 `test_dom_parser.py` |
| P0-2 | C2 SQLite NULL 指纹去重失效 | `knowledge/sqlite_store.py` | `dom_fingerprint or ""` 归一化（查询侧对称） | `test_knowledge_sqlite.py` 补 None 指纹用例 |
| P0-3 | C3 向量维度不匹配崩溃 | `llm/embedding.py`、`knowledge/sqlite_store.py`、`knowledge/store.py` | `embedding_version` 含 dim；`find_semantic` 跳过长度不符的行 | `test_knowledge_semantic.py`、`test_embedding.py` |
| P0-4 | C4 策略链无异常隔离 | `agent/fix_generator.py` | `best_candidate` per-strategy try/except + warning | `test_strategy_short_circuit.py` 补异常用例 |

## P1 · 护栏加固

| 项 | 缺陷 | 文件 | 方案 | 测试 |
|---|---|---|---|---|
| P1-1 | M1 L3 无 selector_exists 护栏 | `agent/strategies/semantic.py` | 采纳前验证（page 注入；page=None 视为存在，与 L1 对称）；失效写人审清单 | `test_orchestrator_semantic.py` 补用例 |
| P1-2 | M2 弹窗特征仅 testid/id | `engine/popup_guard.py` | `_stable_selector` 补 text/aria-label 投影 | `test_popup_guard.py` 补用例 |
| P1-3 | M3 资源不关闭 | `agent/orchestrator.py`、`engine/healing_locator.py`、`tests/conftest.py` | `close()` 级联（owns 标记，注入资源不关）；fixture teardown | `test_knowledge_sqlite.py` close 幂等 |
| P1-4 | M4 上下文缓存键/上限 | `agent/context.py` | 键 = (url, selector)，OrderedDict LRU 上限 256 | 补缓存用例 |

## P2 · 工程清理

- m3 `config/settings.example.yaml`：healing 段补 `llm_diagnose_threshold`。
- m4 `agent/orchestrator.py`：T13 豁免命中补审计记录。
- m5 `knowledge/store.py`：删死代码。
- m6 `llm/registry.py`：更新过时 TODO。
- m7 `llm/embedding.py` + `agent/strategies/heuristic.py`：中文分词粒度差异加注释说明（单字利于 n-gram，片段利于意图重叠，有意不同）。
- m2 `reporting/fix_proposals.py`：文件名加毫秒 + `_now()` 只取一次，防同秒覆盖。
- m1 `agent/diagnose.py`：主键正则补中文片段。
- m8 `engine/healing_locator.py`：`_is_timeout_error` 类名粗判风险加注释。
- m10 `CLAUDE.md`：骨架期描述更新为 Phase 5 完成态。
- nit：`_STRATEGY_REGISTRY` 类型收窄；`wrapped.__doc__` 同步；`selector_builder` id 特殊字符转义；`dashboard._render_cost` dict 防护。
