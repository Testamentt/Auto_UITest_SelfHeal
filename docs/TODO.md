# TODO — 优化与待办清单

> 来源：`docs/reviews/2026-08-04-phase3-retrospective.md` · 随进展勾选/更新（R5 活文档）
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
- [ ] **T3 · 自愈指标看板（价值叙事落点）**（P4）
  - 聚合：自愈成功率、策略命中分布、根因分布、（理想）通过率前后对比
  - 位置：`src/selfheal/reporting/`（在 dashboard.py 基础上增强）
  - 验收：一张能说明"通过率提升"的看板 + 单测
- [ ] **T4 · 修复失败二次自愈 / 缓存验证**（P2）
  - 重试仍失败 → 标记缓存失效并重新走完整自愈；知识缓存"验证后再用"
  - 位置：`src/selfheal/engine/healing_locator.py`、`agent/orchestrator.py::_lookup_knowledge`
  - 验收：缓存选择器失效时能再次自愈而非硬失败；补单测/e2e

## 🟡 中优先级

- [ ] **T5 · 置信度归一化 / 按策略阈值**（P5）
  - 跨策略置信度标定统一，或为每策略设独立接受阈值
  - 位置：`src/selfheal/agent/orchestrator.py`、`strategies/`
- [ ] **T6 · 智能等待默认融入动作前置**
  - 当前 `wait_until_stable` 仅 POM 显式调用；评估作为动作前默认可选步骤
  - 位置：`src/selfheal/engine/smart_wait.py`、`healing_locator.py`
- [ ] **T7 · 知识二次命中 e2e（坐实"越用越聪明"）**（P9）
  - 修复一次 → 第二次同场景命中知识缓存直接复用
  - 位置：`tests/e2e/`
- [ ] **T8 · Playwright 原生定位替代/校验 HTMLParser 重解析**（P7）
  - 用原生查询替代或交叉校验自写 DOM 解析，提升真实 DOM 鲁棒性
  - 位置：`src/selfheal/agent/dom.py`

## ⚪ 低优先级（清理 / 可维护性）

- [ ] **T9 · 对齐 config 与 example yaml**（P6）
  - `execution`/`reporting` 段在 `Settings` 建模，或从 example yaml 移除
  - 位置：`src/selfheal/config.py`、`config/settings.example.yaml`
- [ ] **T10 · 工具模块归位**（P8）
  - `agent/dom.py`、`agent/llm_io.py` 迁至 `collect/` 或独立 `utils/`
- [ ] **T11 · collector 补 trace / network 采集**
  - 兑现架构承诺（当前 TODO）；失败现场含 trace 便于回放定位
  - 位置：`src/selfheal/collect/collector.py`
- [ ] **T12 · orchestrator 职责瘦身**（P8）
  - 若继续膨胀，拆分策略调度 / 持久化 / 诊断为独立协作者（DI）
  - 位置：`src/selfheal/agent/orchestrator.py`

## 已完成（存档）

- [x] Phase 3 评审加固：弹窗去"取消/cancel"、关闭态零副作用、find_repair 语义对齐、_persist 保护、wait_until_stable 总超时、SQLite 上下文管理器（2026-08-04）
