# 归档区（docs/archive）

> **定位**：历史评审、计划、会话沉淀、待办与模板的归档；供维护者查证，不属于项目主线。
> **状态**：历史材料（`reviews/`、`plans/`、`sessions/`、`roadmap.md` 只读；`TODO.md`、`backlog/` 仍可由维护者更新）
> **最后更新**：2026-11-11 · 关联：[../architecture.md](../architecture.md) 架构 · [../decisions.md](../decisions.md) 决策记录 · [../../README.md](../../README.md) 项目展示

## 主线文档（不在本目录）

| 文档 | 内容 |
| --- | --- |
| [README.md](../../README.md) | 项目展示：定位、核心能力、快速开始 |
| [architecture.md](../architecture.md) | 架构、自愈闭环、风险边界、已落地 vs 仍规划 |
| [decisions.md](../decisions.md) | 架构决策记录（ADR）：9 个关键决策 + D1–D19 / T24 索引 |

主线只有这三篇。根 README 不链接本目录；维护者通过 `CLAUDE.md` 或本文件进入。

## 目录内容

| 路径 | 内容 | 状态 |
| --- | --- | --- |
| [TODO.md](TODO.md) | 任务清单、验收记录、踩坑记录（含被 AI 辅助流程坑过的现场记录） | 维护者使用 |
| [backlog/low-priority.md](backlog/low-priority.md) | 低优先级待办与残余项明细 | 维护者使用 |
| [reviews/](reviews/) | 历次代码审查、复盘与修复报告 | 只读 |
| [plans/](plans/) | 各阶段实施计划 | 只读 |
| [sessions/](sessions/) | 阶段会话沉淀 | 只读 |
| [templates/session-doc-template.md](templates/session-doc-template.md) | 会话沉淀五要素模板 | 可复制 |
| [roadmap.md](roadmap.md) | 路线图历史快照（决策已抽取到 [../decisions.md](../decisions.md)） | 只读 |

## 写作约定（维护者）

- 头部三行元信息：定位 / 状态 / 最后更新（`YYYY-MM-DD`）；关联字段用仓库内相对链接。
- 一篇文档只有一个 H1；小节用 H2，不超过 H4；表格首行必须是表头 + `| --- |`。
- 命令、路径、配置键、类名一律反引号包裹，且逐字照抄实现。
- 历史文档不改结论；结论变化写新文档，并在 [TODO.md](TODO.md) 登记。
- 根目录 `README.md` 不新增对归档区的链接；需要对外解释历史时，先在主线文档里写清结论，再在这里留证据。
