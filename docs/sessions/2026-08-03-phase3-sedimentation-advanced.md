# 会话沉淀 · Phase 3 沉淀与进阶（知识库持久化 + 弹窗 + 智能等待 + 视觉）

> 适用前提见 `RULE.md` R5：多轮推进任务，随进展同步更新。
> 日期：2026-08-03 · 关联分支/PR：feat/phase3-sedimentation

## 当前目标

Phase 3：补齐"沉淀与进阶"能力——知识库 SQLite 持久化（含 DOM 指纹）、弹窗自动处理（PopupGuard）、智能等待（wait_until_stable）、视觉定位（qwen3-vl-flash 候选护栏式）。原则：默认行为与现状兼容、全量测试零回归、不引入新第三方依赖（SQLite 用 stdlib，openai 惰性导入）。

## 关键约束

- 遵循 R1–R6；模型调用经 `llm/` 抽象层；配置集中 `config.py`。
- **密钥参数化**：视觉 key 走 `DASHSCOPE_API_KEY` 环境变量，绝不硬编码 / 提交 git；`.gitignore` 增 `.cache/`、`*.env`。
- 无 key / 无 openai 时全链路优雅降级，行为与 Phase 2 等价。

## 已达成结论

- **知识库持久化**（`knowledge/`）：`KnowledgeBackend` 接口 + 内存（KnowledgeStore）/ SQLite（SqliteKnowledgeStore）双实现 + `factory.build_knowledge_store` 按 `settings.knowledge.backend` 选择；`dom_fingerprint`（可交互元素稳定定位器排序哈希）参与 `find_repair` 择优（同结构页面复用更可靠）；e2e 用会话级临时 SQLite，验证持久化且不污染仓库。
- **弹窗处理**（`engine/popup_guard.py`）：`dismiss_if_present` 知识优先（弹窗特征库）→ 关闭按钮启发式识别（aria-label/testid/文本含 关闭/close/取消/×）→ 成功后 `add_popup` 沉淀特征；接入 HealingLocator——动作超时先清弹窗再走自愈（处理"被遮挡"类失败）。
- **智能等待**（`engine/smart_wait.py`）：`wait_until_stable` 先可见、再要求 bounding_box 连续 stable_ms 不变，超时抛 TimeoutError；经 HealingLocator `wait_until_stable()` 暴露为 POM 可选增强（不改默认行为）。
- **视觉定位**（`agent/strategies/visual.py` + `llm/openai_vision.py`）：`OpenAICompatibleVLM` 走 DashScope OpenAI 兼容端点（base64 图片经 chat completions）；VisualStrategy 让 VLM 从 DOM 候选稳定定位器中选择，**候选护栏**拒绝编造 selector；`factory.get_vision_for_settings` 四级判定；无 key/无 openai 时策略自动跳过。
- **测试**：`-m unit` 62 项 + `-m e2e` 5 项全绿；`FakeVisionClient` 覆盖护栏/解析；LLM/VLM 真实冒烟 `skipif` 保护（无 key 自动跳过）。
- **代码评审驱动加固**（评审发现并修复）：① 弹窗关键词移除"取消/cancel"，避免误关业务确认对话框；② 关闭态（enabled=False）零副作用——不再构建知识库/闭环/弹窗处理，不产生 `.cache` 文件；③ 内存/SQLite `find_repair` 语义对齐（指纹优先 + 最高置信度）；④ `_persist` 沉淀失败不中断已成功的自愈（contextlib.suppress）；⑤ `wait_until_stable` 总超时自函数入口起算（修正最坏 2×timeout）；⑥ SQLite 支持上下文管理器。

## 待解决问题

- 真实 VLM 校准：`pip install openai` + 设 `DASHSCOPE_API_KEY` 后跑 `-k visual` 冒烟，校准识别准确率/置信度。
- 真实 LLM 校准：`OPENAI_API_KEY`（或改 DashScope 文本模型）后跑 `-k llm_smoke`。
- 知识库相似度检索当前为"精确 selector + 指纹择优"，向量/模糊检索视需要增强。
- 弹窗签名基于文本归一化，结构指纹视需要增强。
- 评审遗留（低优，Phase 4 视情况处理）：弹窗清除成功未写入审计记录（可补 strategy="popup_guard" 记录）；知识沉淀无去重（可加 UNIQUE(signature)+ON CONFLICT 更新）；弹窗清除后重试的异常链可保留以便诊断；DOM 指纹含文本选择器，动态文案或致漂移（结构指纹为增强项）。

## 下一步计划

1. Phase 4 展示包装：自愈看板增强（统计/趋势）、视频回放、CI 产物上传、README 打磨。
2. 有 key 后跑真实 LLM/VLM 冒烟校准。
