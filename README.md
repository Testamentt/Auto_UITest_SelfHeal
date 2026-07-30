# AutoAiSelfHeal

带 **AI 自愈能力**的 UI 自动化测试框架。在 Playwright 之上构建「感知 → 诊断 → 决策 → 修复」智能闭环，
自动修复因 UI 变动失效的定位、化解弹窗/权限"突袭"，显著提升脚本稳定性并降低人工维护成本。

## 它解决什么

- **脚本脆弱**：不再强依赖单一 ID/XPath，定位失败时自动多策略自愈。
- **执行不稳定**：自动识别并处理系统弹窗、权限申请等干扰。
- **人工维护重**：成功修复沉淀进知识库，越用越聪明。

## 架构速览

四层结构（详见 [CLAUDE.md](CLAUDE.md) 与 [docs/architecture.md](docs/architecture.md)）：

```
展示层        reporting/   Allure + 自研 HTML 看板、视频回放
AI 自愈 Agent agent/       大脑：编排闭环 / 诊断 / 多策略修复（llm 抽象 + knowledge 知识库）
数据采集层    collect/     截图 / DOM 快照 / 网络日志 / 执行轨迹
能力建设层    engine/      Playwright 封装 / 自愈定位器 / 智能等待 / 弹窗处理
```

## 快速开始

```bash
pip install -e ".[dev,llm]"
playwright install chromium
pytest                      # 运行测试
ruff check .                # 代码检查
```

> ⚠️ 当前为**骨架阶段**：模块多为接口与桩实现，核心闭环逻辑待逐步填充。
