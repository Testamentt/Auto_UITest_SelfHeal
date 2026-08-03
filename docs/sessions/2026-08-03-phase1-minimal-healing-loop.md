# 会话沉淀 · Phase 1 最小自愈闭环

> 适用前提见 `RULE.md` R5：多轮推进任务，随进展同步更新。
> 日期：2026-08-03 · 关联提交：dc52206 / bb32756 / d7ea423

## 当前目标

Phase 1：用最小代价跑通**一条端到端自愈闭环**（不依赖 LLM），产出首个"自愈成功"可演示成果，并落地插件化骨架。

## 关键约束

- 遵循 R1–R6；Phase 1 不引入 LLM/VLM（provider 后置到 Phase 2，见决策 D2）。
- 演示基于**自建 POM 化测试页**，可无缝切换到其它 POM 用例（决策 D3）。
- 自愈是**插件**：Fixture 注入 + 接口兼容代理，可开关、不侵入 Playwright（决策 D5）。
- 兜底（决策 D6）：AI 不确定时 `use_fallback`（默认）/ `pause`（仅交互模式）/ `fail`。
- 浏览器：**默认系统已装 Chrome**（`channel: chrome`，免下载内核）。

## 已达成结论

- **自愈闭环跑通**：定位失败（TimeoutError）→ 采集（截图+DOM）→ 知识优先（内存库）→ 规则诊断 → 启发式策略 → 新定位器重试成功 → 沉淀记录。语义/视觉仍为桩、自动跳过。
- **启发式策略**（`agent/strategies/heuristic.py`）：stdlib HTMLParser 解析 DOM + 意图相似度打分；描述与候选文本/aria 互为子串为强信号（置信度 ~0.85 起），词元重叠封顶 0.8；新定位器优先级 `data-testid > id > 文本 > aria-label`。
- **插件化代理**（`engine/healing_locator.py`）：`HealingPage`/`HealingLocator` 接口兼容代理，仅 HEALABLE 动作（click/fill/check…）包"失败→自愈→重试"，其余 API 透传；`enabled=False` 时行为等同原生。
- **开关**：CLI `--selfheal/--no-selfheal` 优先于 `settings.healing.enabled`（conftest fixture）。
- **修复两个真实 bug**：
  1. `BrowserManager` 曾用 `getattr(pw, channel)` 选浏览器类型 → 改 `channel` 作为 `launch(channel=...)` 参数；
  2. `HealingReporter.__init__` 误用 `dataclasses.field()` → 改 `= []`。
- **测试**：`-m unit` 13 项 + `-m e2e` 3 项（自愈成功 / 兜底 / 关闭=原生），全量 16 项通过。
- **演示页**：`tests/e2e/pages/demo_page.html`（`file://` 零依赖）+ POM `demo_page.py`。

## 待解决问题

- 启发式打分权重为手工设定，尚未配置化；候选较多时缺可见性/位置二次筛选。
- 知识库目前为内存实现（session 内复用），SQLite 持久化留 Phase 3。
- 弹窗自动处理（`popup_guard`）留 Phase 3；演示页弹窗仅为占位。
- 链式定位器 / iframe 内的自愈暂为透传（未包裹）。
- `pause` 模式依赖 `stdin.isatty()`，交互式调试体验待实测。

## 下一步计划

1. **Phase 2 规划**（R6 先收敛）：LLM/VLM provider 选型 → `llm/` 接入 → 语义诊断 + 语义定位。
2. 启发式策略参数配置化（`config/settings.yaml`），为 Phase 2 提供稳定基线。
3. 自愈记录可视化（Allure 附件已就绪，补自愈看板 v0）。
4. Phase 3：SQLite 知识库持久化 + 弹窗处理。
