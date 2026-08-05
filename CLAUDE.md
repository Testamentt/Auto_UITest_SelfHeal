# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 开发规则（强制）

本项目遵循 `RULE.md` 中的 R1–R6 规则（先计划后写入、测试覆盖、临时方案管理、代码质量与文档、变更沉淀、先收敛计划），冲突时以 RULE.md 为准。

@RULE.md

## 沟通约定

- 始终使用**中文**回答问题、撰写文档与提交信息；代码、命令、标识符、路径保持英文。详见 `memory.md`。

## 项目概述

AutoAiSelfHeal 是一个**带 AI 自愈能力的 UI 自动化测试框架**。它在 Playwright 之上构建「感知 → 诊断 → 决策 → 修复」的智能闭环，解决传统 UI 自动化的两大痛点：

- **脚本脆弱**：强依赖 ID/XPath 定位，UI 微调即导致脚本批量失效，团队陷入"抢救式"修复循环。
- **执行不稳定**：系统弹窗、权限申请等"突袭"使脚本平均通过率长期徘徊在 ~80%。

框架在定位失败或执行异常时自动捕获现场、诊断根因、多策略修复并回试验证，成功修复沉淀进知识库，最终把过程写入报告用于审计。

## 当前方向

阶段路线、决策记录与下一步拆解见 **`docs/roadmap.md`**（活文档，随进展同步更新）。动手前先读它，避免与既定决策冲突。

## 技术选型

| 层级 | 选型 | 说明 |
| --- | --- | --- |
| 自动化框架 | Playwright (Python) | 现代、高效、原生支持 trace/截图/网络拦截 |
| 测试框架 | pytest + pytest-playwright | 用例组织与 fixture |
| 自愈定位器 | 自研 LLM 定位器 + 开源库辅助 | 灵活可控 |
| 大语言模型 | DeepSeek（`deepseek-v4-flash`，OpenAI 兼容端点），经 `llm/` 抽象层接入 | 支持 API 付费调用、可切换 provider |
| 视觉模型 | 通义 `qwen3-vl-flash`（DashScope 兼容端点），经 `llm/` 抽象层接入 | 多模态，视觉定位与控件画像 |
| 报告 | Allure + 自研 HTML | 自愈看板、视频回放 |
| CI/CD | GitHub Actions | 演示自动化流水线 |

> LLM/VLM 的具体 provider 尚未确定。所有模型调用**必须**经过 `src/selfheal/llm/` 的抽象接口，禁止在业务代码里直接 import 某个 SDK，以便后续无痛切换。

## 架构（大局）

四层结构，目录与层级一一对应——理解这套映射是快速上手的关键：

```
展示层        src/selfheal/reporting/   Allure 附件、自愈记录、HTML 看板
AI 自愈 Agent src/selfheal/agent/       大脑：orchestrator 编排闭环，diagnose 诊断，strategies/ 多策略修复
  ├ LLM 抽象  src/selfheal/llm/         provider 无关的模型接入层（LLM + VLM）
  └ 知识库    src/selfheal/knowledge/   弹窗特征库 + 修复案例库
数据采集层    src/selfheal/collect/     截图 / DOM 快照 / 网络日志 / 执行轨迹
能力建设层    src/selfheal/engine/      Playwright 封装、自愈定位器、智能等待、弹窗处理
```

### 自愈闭环（核心流程）

由 `agent/orchestrator.py` 编排，是本项目最核心的逻辑链路：

1. **执行监控**：`engine/` 执行步骤，捕获定位失败 / 超时 / 不可交互等异常。
2. **现场采集**：`collect/collector.py` 抓取截图、DOM 快照、网络日志、trace。
3. **智能诊断**：`agent/diagnose.py` 借助 LLM 判定根因（元素不存在 / 不可交互 / 超时 / 弹窗遮挡）。
4. **多策略修复**：`agent/strategies/` 依次尝试
   - `heuristic.py` 启发式匹配（多属性组合）
   - `semantic.py` 语义定位（自然语言描述）
   - `visual.py` 视觉定位（VLM 截图分析）
5. **验证与沉淀**：修复后重试原步骤；成功则把「原定位器 → 新定位器 → 置信度」写入 `knowledge/`。
6. **报告与审计**：`reporting/hooks.py` 记录每次修复过程，供 Allure / HTML 看板展示。

**知识库优先**：orchestrator 在调用 LLM 前应先查 `knowledge/` 是否已有匹配的修复案例或弹窗特征，命中则直接复用，降低成本与延迟。

## 常用命令

依赖与环境（推荐虚拟环境）：

```bash
pip install -e ".[dev]"      # 安装框架 + 开发依赖
playwright install chromium  # 安装浏览器内核
```

测试：

```bash
pytest                                       # 运行全部测试
pytest -m unit                               # 按标记跑单元测试（CI 门禁，不依赖浏览器）
pytest -m e2e                                # 按标记跑集成 / 端到端测试（需浏览器内核）
pytest tests/unit/test_x.py::test_y -v       # 运行单个测试
pytest --alluredir=allure-results            # 生成 Allure 结果
allure serve allure-results                  # 本地查看 Allure 报告
```

代码质量（使用 ruff 同时负责 lint 与格式化）：

```bash
ruff check .          # lint
ruff check . --fix    # lint 并自动修复
ruff format .         # 格式化
```

## 项目结构

```
AutoAiSelfHeal/
├── CLAUDE.md / RULE.md / README.md / memory.md
├── pyproject.toml              # 依赖 + pytest/ruff 配置（含 unit/e2e/healing marker）
├── config/settings.example.yaml# 运行时配置示例（复制为 settings.yaml 使用）
├── src/selfheal/               # 框架主体（见「架构」分层）
├── tests/                      # unit/（-m unit）与 e2e/（-m e2e），conftest.py 提供 fixture
├── docs/                       # architecture.md 架构 · roadmap.md 路线图(活文档) · session-doc-template.md · sessions/ 沉淀
└── .github/workflows/ci.yml    # CI 两阶段（unit 门禁 → e2e）
```

## 开发约定

- 当前仓库为**骨架阶段**：多数模块是带 docstring 与 `TODO` 的桩，实现时遵循既有分层与命名，不要跨层直接耦合。
- **自愈是插件，不侵入框架**：经 pytest fixture 按 `settings.healing.enabled` / CLI `--selfheal` 提供；`HealingPage` 与原生 `Page` 接口兼容（代理），POM 代码开/关自愈都能跑；关闭时为原生 Page、零开销。不用 import 替换 / monkeypatch（见 roadmap.md 决策 D5）。
- **兜底优先**：`locator(sel, fallback=...)`；AI 不确定（置信度 < 阈值）时按 `healing.on_uncertain` 处理，默认 `use_fallback`，无备用则 fail（决策 D6）。
- 新增模型能力一律走 `llm/` 抽象 + `registry` 注册，provider 相关配置放进 `config/settings.yaml`。
- 修复策略是**可插拔**的：新策略继承 `agent/strategies/base.py`，由 orchestrator 按置信度/成本排序调度。
- 配置通过 `src/selfheal/config.py` 用 pydantic 加载校验，不要在模块里散落读取环境变量。
