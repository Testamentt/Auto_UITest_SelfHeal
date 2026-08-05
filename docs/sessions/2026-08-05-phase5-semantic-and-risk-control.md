# Phase 5 会话沉淀：知识库语义化 + 风险控制

> 日期：2026-08-05 · 关联路线图 `docs/roadmap.md` · TODO `docs/TODO.md`（T13–T17 / A1–A3）

## 当前目标

完成 Phase 5 两大方向：
- **A 知识库语义化**——把修复知识从"精确 selector + 指纹择优"升级为向量检索，让"越用越聪明"真正成立（语义相似命中，跨结构复用）。
- **D 风险控制（T13–T17）**——兑现架构文档「预期与风险」承诺：高风险页豁免、dry-run、修复写回人审、flaky 区分、多模态成本看板。

## 关键约束

- R1–R6：先计划后写入（计划 v5 经四轮共 14 条评审意见吸收后批准）、单元/集成测试双覆盖、临时方案三步管理、文档五要素。
- 模型调用一律经 `llm/` 抽象层；**绝不在自愈热路径调用 API text-embedding**（延迟 500ms–1.5s + Token 成本失控）。
- 默认配置（无 key / 未装 openai）下行为与旧版**完全等价**，LLM/VLM/Embedding 能力全部优雅降级。
- 新增依赖 `numpy`（向量运算）+ `beautifulsoup4`（DOM 快照离线上下文提取），均已论证必要性。
- 调度链既有结构（knowledge_first + strategy_order）不可破坏：现有 142 项单测 + 7 项 e2e 必须全绿。

## 已达成结论

**A 知识库语义化**
1. **本地确定性向量（A1）**：`llm/embedding.py::NgramEmbedding`——字符 n-gram 哈希 TF 向量，`hashlib.md5` 确定性（弃用内置 `hash()`，避免 PYTHONHASHSEED 跨进程失效）；BLOB（`float32.tobytes()`）+ numpy 矩阵余弦（5k 行 ~ms）。升级路径 v2 = fastembed 本地模型（如 bge-small-zh），`embedding_version` 列平滑迁移。
2. **调度链 L1–L4**：L1 精确指纹 `repair_key = md5(page_fingerprint + 元素文本 + 标签路径)` 命中**硬短路直接返回**（ID 变化但文本/结构不变仍命中，<50ms）→ L2 启发式 → **L3 语义向量检索**（`semantic` 策略升级，只在 L1/L2 未命中后触发）→ L4 VLM。
3. **防污染采纳规则（L3）**：sim>0.92 且 `is_verified` 自动采纳；7 天新鲜窗口内 sim>0.80 自动采纳（冷启动免人审）；其余 0.75<sim≤0.92 写 `reports/review-queue.md` 人审清单、返回 None 降级 L4（不阻塞流水线）。
4. **失败上下文三级回退提取**：live `page.evaluate`（元素文本+附近5兄弟+标签路径，try-catch 绝不先炸）→ DOM 快照 BeautifulSoup 离线解析（~20ms）→ 静态属性/描述兜底；提取一次缓存（`_failure_context_cache`），L1/L3/_persist 复用。
5. **页面隔离**：`page_fingerprint`（URL 路径 + DOM 结构哈希），`find_semantic` 先按 page 分桶再余弦，跨页不匹配宁进 L4 不乱配。

**D 风险控制（T13–T17）**
6. T13 `healing.exclude_url_patterns`（glob）命中即不触发自愈；T14 `healing.dry_run` 只生成建议（写 fix-proposals）、不换定位器/不持久化，返回 `proposed_selector`；T15 `healing.fix_proposals` 成功后输出「原→新」PR 化建议（Markdown+JSON，`applied=false` 不自动改库）。
7. T16 `HealingRecord.verified`——修复后原定位器仍失效=真自愈（verified=True），已恢复=flaky（verified=False），metrics 增 verified/flaky/verified_rate，dashboard 增卡片与审计列。
8. T17 计数代理统计 LLM/VLM 真实调用 → `HealingReporter.stats`/`cost_summary()`，dashboard 渲染成本卡片；策略短路省下的调用不计数，如实反映降本。

**验证**：unit 142 + e2e 7 全绿；ruff 全过；`find_semantic` 同页命中 / 跨页分桶 / 版本过滤 / 阈值拒绝均有单测覆盖。

## 待解决问题

- 语义向量 v1 为本地 n-gram，对中文/跨语言语义表达能力弱于预训练模型；v2 fastembed 升级路径已设计（`embedding_version` 平滑迁移）但未实施。
- 真实 LLM/VLM 冒烟需 API key（`OPENAI_API_KEY` / `DASHSCOPE_API_KEY`）才能跑；未在本轮验证真实模型的 L3 命中质量。
- 弹窗特征仍为文本归一化签名；结构指纹（DOM 结构哈希）可视化需要时增强。
- 智能等待默认融入动作前置待实测决定。

## 下一步计划

- T5 置信度归一化 / 按策略阈值；T6 智能等待默认融入；T7 知识二次命中 e2e；T8 Playwright 原生定位替代 HTMLParser（见 `docs/TODO.md`）。
- 语义化 v2：评估 fastembed 本地模型替换 n-gram；知识库规模大时评估 sqlite-vec / Chroma。
- 用真实模型冒烟验证 L3 语义命中质量与成本估算校准（默认单价为粗估值，可配置化）。
