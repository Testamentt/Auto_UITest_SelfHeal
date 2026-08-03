# TODO — 优化与待办清单

> 来源：`docs/reviews/2026-08-04-phase3-retrospective.md` · 随进展勾选/更新（R5 活文档）
> 图例：⬜ 待办 · 🟡 进行中 · ✅ 完成 · 🔴 阻断项 · 🟠 部分完成

## 🔥 高优先级（Phase 4 核心：证据 + 指标 + 加固）

- [ ] **T1 · 策略短路，省 LLM/VLM 调用**（P1）
  - `_best_candidate` 达到"早接受阈值"即停，或按 `strategy_order` 首个达标即返回
  - 位置：`src/selfheal/agent/orchestrator.py`
  - 验收：配真实 key 后，启发式命中的自愈不再触发 semantic/visual；补单测
- [ ] **T2 · 真实模型跑通 + 证据留存**（P3）🔴 需 API key
  - 设 `OPENAI_API_KEY` / `DASHSCOPE_API_KEY`，跑 `pytest -m e2e -k "llm_smoke or visual"`
  - 留存真实命中的截图/日志作为展示素材
  - 验收：semantic/visual 至少一个真实模型用例通过并留证
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
