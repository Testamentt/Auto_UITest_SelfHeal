# Phase 5 实现计划：知识库语义化 + 风险控制

> 日期：2026-08-05 · 状态：✅ 已实施（对应 commit `d4b5696`）· 会话沉淀见 `docs/sessions/2026-08-05-phase5-semantic-and-risk-control.md`
> 本文是批准版计划（v5）的落盘——计划评审时经四轮共 **14 条意见**吸收，此处记录最终设计及其演进依据。

## 方向与选型

| 方向 | 核心内容 | 选型 | 理由 |
|---|---|---|---|
| **A 知识库语义化** | 修复/弹窗知识向量检索，语义相似命中（跨结构复用），坐实"越用越聪明" | 本地确定性 n-gram 向量（v1）+ numpy 余弦；**绝不在热路径调 API text-embedding**；v2 升级 fastembed 本地模型 | 零网络/零费用/<10ms；热路径查询向量不挤占重试窗口；provider 无关 |
| **D 风险控制（T13–T17）** | 高风险页豁免 / dry-run / 修复写回人审 / flaky 区分 / 成本看板 | config + reporting 输出 + 看板 | 兑现「预期与风险」承诺、自洽闭环 |

## 调度链（最终设计）

```
L1 精确指纹（repair_key 硬短路）
   → L2 启发式（编辑距离 + 属性交集）
   → L3 语义向量检索（semantic 策略升级，知识库命中）
   → L4 视觉（VLM）
```

- `knowledge_first(L1)` 前置 + `strategy_order=[heuristic, semantic, visual]`；**L1 命中硬短路直接返回**，`strategy_order` 仅在 L1 未命中时作降级链。
- `semantic` 策略升级为向量检索：只在 L1/L2 都未命中后触发，避免"每个失败都跑高精度 AI"的本末倒置。

## 关键设计（评审演进后定稿）

### 1. 本地确定性向量（A1）
- 字符 n-gram 哈希 TF 向量 + numpy 余弦；特征哈希用 `hashlib.md5`，**弃用内置 `hash()`**（PYTHONHASHSEED 随机化会跨进程失效）。
- 存储用 **BLOB（`float32.tobytes()`）** + `np.frombuffer` 批量载入 + 矩阵余弦（5k 行 ~ms），反序列化不再是瓶颈。
- 升级路径：v2 = fastembed 本地模型（如 bge-small-zh），语义更强仍本地推理；`embedding_version` 列平滑迁移（`find_semantic` 只检索同版本）。

### 2. L1 键值修正
- `repair_key = md5(page_fingerprint + 元素文本 + 标签路径)`——**不是失效的 selector 字符串**；ID 从 #a→#b 但文本/结构不变 → L1 仍命中硬短路（<50ms）。
- 持久化时从成功修复的元素提取存 `repair_key`；失败时从现场提取计算同键查询。

### 3. 防御性现场提取（上下文三级回退）
- **live**：`page.evaluate` 取「元素文本 + 附近5兄弟文本 + 标签路径摘要」；try-catch，绝不先炸。
- **snapshot**：DOM 快照 + BeautifulSoup 离线解析（~20ms）稳定提取。
- **static**：description / 失败 selector 末段兜底；全空则跳过 L3 降级 L4。
- 查询输入源**绝不用动态 failed_selector 本身**。
- **失败现场缓存**：orchestrator 捕获定位异常时只提取一次（`_failure_context_cache`），L1/L3/_persist 全部复用；`page.content()` 提取有超时熔断（超时走静态兜底）。

### 4. 页面隔离
- `RepairCase` 增 `page_fingerprint`（URL 路径 + DOM 结构哈希）；`find_semantic` **先按 page 分桶过滤再余弦**；跨页无匹配宁进 L4 不乱配。

### 5. 防污染衰减 + 采纳规则
- 增 `hit_count` / `last_hit_at` / `is_verified` / `created_at`；误修复反馈降级，停止自命中。
- **L3 采纳规则**：sim>0.92 且 verified → 自动采纳；0.75<sim≤0.92 → **仅"建议修复"** 写 `reports/review-queue.md` 人审清单、当前用例降级 L4（不阻塞流水线）；人审打 `is_verified=True` 后阈值自动降为 sim>0.75 即采纳。
- **新鲜度特权**：7 天新鲜窗口 + sim>0.80 → 自动采纳（免人审）；7 天后未验证自动降级普通规则——解决冷启动"人审滞后"又不致旧知识污染。

### 6. 运行期 / 持久化分离
- 0.75<sim≤0.92 且未 verified → **当前用例立即放弃、降级 L4**（不阻塞流水线）+ 异步写人审清单；人审确认后阈值自动降为 >0.75。

## D 风险控制（T13–T17）设计

| # | 需求 | 设计 | 验收 |
|---|---|---|---|
| T13 | 高风险页豁免 | `healing.exclude_url_patterns: list[str]`（glob），orchestrator 开头按 URL 匹配命中即不触发自愈 | `tests/unit/test_risk_control.py` |
| T14 | dry-run 仅报告不执行 | `healing.dry_run`，只生成修复建议（写 fix-proposals）、不换定位器重试、不持久化；返回 `proposed_selector` 供人审 | 同上 |
| T15 | 修复写回人审清单 | `reporting/fix_proposals.py::write_fix_proposal` 输出「原→新」PR 化建议（Markdown+JSON，`applied=false` 不自动改库）；`healing.fix_proposals` 开关 | `tests/unit/test_risk_metrics.py` |
| T16 | 真自愈 vs flaky | `HealingRecord.verified`（修复后原定位器仍失效=真修复，已恢复=flaky）；metrics 增 verified/flaky/verified_rate；dashboard 增卡片与审计列 | 同上 |
| T17 | 多模态成本看板 | 计数代理统计 LLM/VLM 真实调用 → `HealingReporter.stats`/`cost_summary()`；dashboard 渲染成本卡片；`estimate_cost` 默认单价可覆盖 | 同上 |

## 新增依赖

- `numpy>=1.26`：向量运算（余弦/矩阵）。
- `beautifulsoup4>=4.12`：DOM 快照离线上下文提取（~20ms 兜底）。
均已论证必要性，进 `pyproject.toml` 核心依赖。

## 实施顺序（已按此执行）

1. **A1** embedding 抽象：`llm/embedding.py` + `EmbeddingConfig` → `tests/unit/test_embedding.py`（6 项）。
2. **A2** 存储/检索：`schema.py` 扩展 + SQLite/内存 `find_by_repair_key`/`find_semantic`/`bump_hit`/`set_verified` + `dom.py` 指纹/键 → `tests/unit/test_knowledge_semantic.py`（10 项）。
3. **A3** orchestrator 接线：L1 硬短路 + L3 进策略链 + 上下文提取 + persist 富化 → `tests/unit/test_orchestrator_semantic.py`（14 项）。
4. **D** T13–T17：config + fix_proposals + verified/metrics/dashboard + 成本 → `test_risk_control.py`（7 项）+ `test_risk_metrics.py`（10 项）。

## 验证

- unit **142** + e2e **7** 全绿；`ruff check src tests` 全过。
- 语义命中演示：`python scripts/demo_semantic_reuse.py`（Phase 5 收尾新增）。

## 升级路径（未在本轮实施）

- 语义 v2：fastembed 本地模型（bge-small-zh）替换 n-gram，`embedding_version` 平滑迁移。
- 规模大时：SQLite 全量扫描 → sqlite-vec / Chroma。
