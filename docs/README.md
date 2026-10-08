# 文档地图与写作规范

> **定位**：本仓库文档**唯一入口**——先看这里，再决定读哪一篇；新增、移动、改名文档时必须同步更新本文件。
> **状态**：活文档（唯一入口索引 + 全仓写作规范）
> **最后更新**：2026-10-08 · 关联：[RULE.md](../RULE.md) §R7（文档双轨）、[TODO.md](TODO.md)（任务清单）

## 一、文档分区（按"能不能改"分类）

| 分区 | 文件 / 目录 | 可否修改 | 职责 |
| --- | --- | --- | --- |
| 规则 | [RULE.md](../RULE.md) | 活文档（改规则须人工确认） | R1–R8 **规则源头**；冲突时以本文件为准 |
| 协作约定 | [CLAUDE.md](../CLAUDE.md) | 活文档 | 面向 AI / 新协作者的上手说明：规则索引、常用命令、分层速览 |
| 跨会话偏好 | [memory.md](../memory.md) | 活文档 | 语言约定、领域知识指针、环境事实 |
| 技术文档（事实源） | [README.md](../README.md)、[architecture.md](architecture.md)、[roadmap.md](roadmap.md) | 活文档（须与实现同步） | 对外展示 / 架构与决策 / 阶段路线；**参与文档双轨** |
| 任务清单 | [TODO.md](TODO.md) | 活文档 | 全项目**唯一待办入口**：待办、已完成存档、踩坑记录 |
| 沉淀与历史 | [sessions/](sessions/)、[plans/](plans/)、[reviews/](reviews/) | **只读，禁止修改** | 当时的结论留档；不作为当前事实依据 |
| 模板 | [templates/session-doc-template.md](templates/session-doc-template.md) | 可改 | 新建 session 文档时复制 |
| 学习镜像 | `E:\Project\AutoAiSelfHeal-docs-study\AutoAiSelfHeal\` | 只改表达形式 | 独立仓库（面试学习版），见 §四 |
| 运行时产物 | `reports/**`、`allure-results/**`、`.cache/**` | 自动生成，可删 | 已 gitignore，**不是文档**（看板 / 建议清单 / 知识库） |

## 二、读什么（按目的索引）

| 我想… | 读这篇 | 备注 |
| --- | --- | --- |
| 30 秒了解项目卖点 | [README.md](../README.md) | 面试导向 11 段结构 |
| 搞清四层架构与自愈闭环 | [architecture.md](architecture.md) | 含关键设计决策与风险边界 |
| 知道现在做到哪、下一步做什么 | [roadmap.md](roadmap.md) | 阶段状态 + 决策记录（D 编号） |
| 领一个待办 / 看历史踩坑 | [TODO.md](TODO.md) | T 编号任务、踩坑记录、候补池 |
| 看某次审查发现了什么 | [reviews/](reviews/) | 按日期，只读 |
| 查 ERP（管伊佳）接入细节 | [`.claude/skills/erp-domain/SKILL.md`](../.claude/skills/erp-domain/SKILL.md) | 领域知识唯一归属 |
| 查同步学习文档的流水线 | [`.claude/skills/doc-study-sync/SKILL.md`](../.claude/skills/doc-study-sync/SKILL.md) | 规则源头仍是 RULE.md §R7 |
| 看自愈运行结果 | `reports/dashboard.html`、`reports/fix-proposals.md` | 运行时产物，非文档 |

## 三、写作规范（全仓统一格式）

### 3.1 头部元信息（所有活文档必须有）

紧接着 H1 写三行引用块，字段固定、顺序固定：

```markdown
> **定位**：一句话说清"这篇是什么、给谁看、边界在哪"。
> **状态**：事实源 / 活文档 / 只读历史（三选一）
> **最后更新**：YYYY-MM-DD · 关联：<相对链接或"无">
```

- 日期只写 `YYYY-MM-DD`；禁止"最近 / 刚刚 / 昨天"这类相对时间。
- 关联字段用相对路径链接；跨仓库（如学习镜像）写仓库名。

### 3.2 结构

1. 一篇文档**只有一个 H1**；小节用 H2，不再往下超过 H4。
2. 列表统一 `-`；有序步骤才用数字。
3. 表格首行必须是表头 + `| --- |` 分隔行，不用空格对齐。
4. 代码块标语言（`bash` / `python` / `yaml` / `text`）。
5. 命令、路径、配置键、类名、函数名一律反引号包裹，且**逐字照抄实现**（R7.1 零幻觉提取）。

### 3.3 事实与引用

- 引用实现必须能落到 `路径:行号` 或模块路径（例：`src/selfheal/config.py` 的 `LLMConfig`）。
- 写"已实现"必须有代码；写"规划中"必须给 [TODO.md](TODO.md) 的任务编号——**禁止无编号的计划**。
- 不确定的事写 `[待确认: xxx]`，不要编。

### 3.4 历史不可写改

- `sessions/`、`plans/`、`reviews/` 是留档，**只读**；结论变化写进新文档，并在 [TODO.md](TODO.md) 登记。
- 需要更正旧文档中的事实时：不改旧文，改"当前事实源"（architecture / roadmap / TODO），并在 [reviews/](reviews/) 新增一篇说明差异。

## 四、文档双轨（RULE.md §R7）

| | 技术文档（源，本仓库） | 学习文档（镜像，独立仓库） |
| --- | --- | --- |
| 位置 | `README.md`、`docs/architecture.md`、`docs/roadmap.md` | `E:\Project\AutoAiSelfHeal-docs-study\AutoAiSelfHeal\`（路径一一镜像） |
| 读者 | 开发与维护者 | 面试准备的初级测试工程师 |
| 规范 | R7.1 考古挖掘（只记录、不发挥） | R7.2 翻译官 9 条（只改表达） |
| 同步 | 源文档改动后**同批**更新镜像 | 流水线：`.claude/skills/doc-study-sync/SKILL.md` |

- 不参与双轨：`docs/sessions/`、`docs/plans/`、`docs/reviews/`、`docs/TODO.md`、`docs/templates/`。
- 镜像头部必须带 `> 源版本: <commit> | 同步日期: <UTC>`；事实冲突时**熔断**交人工，不得自行折算。

## 五、新增 / 移动 / 改名文档的检查清单

1. 位置对不对？——按 §一 分区表放；散落的新说明优先并入既有文档，不新建同级文件。
2. 头部元信息三行齐了吗？——按 §3.1。
3. 事实核对了吗？——路径 / 键名 / 数字与实现一致（§3.3）。
4. 是否重复？——同一事实只允许一个"归属地"，其他位置改为指针。
5. 本文件（`docs/README.md`）的 §一 / §二 更新了吗？
6. 涉及双轨三篇时，学习镜像同批同步了吗？（§四）
