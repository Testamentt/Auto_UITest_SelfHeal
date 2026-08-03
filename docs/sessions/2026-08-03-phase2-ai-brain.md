# 会话沉淀 · Phase 2 AI 大脑（LLM 智能诊断 + 语义定位）

> 适用前提见 `RULE.md` R5：多轮推进任务，随进展同步更新。
> 日期：2026-08-03 · 关联 PR：Phase 2 AI 大脑

## 当前目标

Phase 2：给自愈闭环接入 **LLM（AI 大脑）**——智能诊断根因 + 语义定位，并新增自愈看板 v0。核心原则：**默认配置（无 key / 未装 openai）下行为与 Phase 1 完全等价，全量测试零回归**。

## 关键约束

- 遵循 R1–R6；模型调用一律经 `llm/` 抽象层，业务代码不直接 import provider SDK。
- **优雅降级**：LLM 不可用时诊断退规则式、语义策略返回 None 被跳过，闭环不中断。
- **防幻觉护栏**：LLM 返回的 selector 必须在精简 DOM 索引中真实存在，否则拒绝。
- 不依赖 `response_format=json_object`（兼容实现行为不一致），靠 prompt + 容错解析 + 白名单。
- openai SDK 惰性导入，无 openai 的 CI 单测仍可 import。

## 已达成结论

- **LLM 抽象层落地**（`llm/`）：`OpenAICompatibleLLM`（base_url+model 覆盖 OpenAI/DeepSeek/Qwen/智谱）+ `factory.get_llm_for_settings`（四级可用性判定：enabled→key→provider→SDK）+ `UnavailableError`。
- **智能诊断**（`agent/diagnose_llm.py`）：`LLMDiagnoser` 继承规则式 `Diagnoser`，LLM 判定根因（白名单 not_found/not_visible/covered/timeout/unknown），调用异常退规则式、越界返回 unknown。
- **语义定位**（`agent/strategies/semantic.py`）：注入可选 `LLMClient`，prompt 含描述+精简 DOM，输出 JSON（selector+confidence），防幻觉护栏校验 selector 真实存在。
- **公共基础设施**（`agent/llm_io.py`）：`build_compact_dom`（精简 DOM 索引，控 token）+ `extract_json`（容错剥 fence/正则兜底）+ `safe_str/safe_float`。
- **DOM 公共模块**（`agent/dom.py`，决策 D8）：`parse_interactive_elements` + `build_stable_selector`，heuristic/llm_io/semantic 共用，消除循环导入与私有耦合。
- **自愈看板 v0**（`reporting/dashboard.py`）：零依赖纯 HTML 审计表，消费 `reporter.records`，`html.escape` 防注入。
- **失败上下文接线**：`FailureContext` 由 healing_locator 捕获异常构造，经 orchestrator 透传给诊断器（LLM prompt 含真实失败信息）。
- **测试**：`-m unit` 43 项 + `-m e2e` 3 项通过；`FakeLLMClient`（canned/抛错/垃圾三模式）覆盖诊断降级、语义命中/护栏拒绝、JSON 容错。e2e 冒烟（真实 key）`skipif` 保护。

## 待解决问题

- 真实 LLM provider 未配置（走 `OPENAI_API_KEY`）；有 key 时跑 `pytest -m e2e -k smoke` 验证语义定位真实命中。
- 知识库仍为内存实现，SQLite 持久化留 Phase 3。
- 弹窗自动处理、视觉定位（VLM）、智能等待留 Phase 3。
- 启发式打分权重未配置化（同 Phase 1 遗留）。

## 下一步计划

1. **Phase 3 规划**（R6 先收敛）：知识库 SQLite 持久化 + 弹窗特征库/自动关闭 + 视觉定位（VLM）+ 智能等待。
2. 有 API key 后跑真实 LLM 冒烟，校准语义定位置信度与诊断准确率。
