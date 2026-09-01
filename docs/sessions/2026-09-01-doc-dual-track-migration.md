# 会话沉淀：文档双轨制导入（doc-system-kit）

> 日期：2026-09-01 · 关联规则 `RULE.md` R7 · 技能 `.claude/skills/doc-study-sync/SKILL.md`

## 当前目标

把 TestPlatform 的「文档双轨制」体系（技术文档 = 事实源 + 学习文档 = 面试镜像）从 `E:\Project\TestPlatform\doc-system-kit` 迁移包导入本项目，完成规则并入、技能安装与镜像仓库首跑初始化。

## 关键约束

- R1：迁移方案（占位符取值 / 镜像路径 / 首批范围 / 提交策略四项决策）经人工确认后执行。
- R5：§A「文档沉淀」与既有 R5 逐条重复，不重复搬运，仅在 R7 开头注明映射关系。
- 镜像仓库与源代码仓库**完全隔离**：镜像放 `E:\Project\AutoAiSelfHeal-docs-study`（独立 git 仓库），源仓库不提交它、不构建它、不引用它。
- 镜像远端与 TestPlatform 共用 `https://github.com/Testamentt/docs_study.git`，顶层文件夹区分项目。

## 已达成结论

1. **占位符八项定值**：源文档目录 `docs`；参与双轨 `docs/architecture.md`、`docs/roadmap.md`、`README.md`；镜像本地路径 `E:\Project\AutoAiSelfHeal-docs-study`；项目名 `AutoAiSelfHeal`；镜像远端 `https://github.com/Testamentt/docs_study.git`；规则文件 `RULE.md`；入口文件 `CLAUDE.md`；历史记录目录 `docs/sessions`、`docs/plans`、`docs/reviews`、`docs/TODO.md`、`docs/session-doc-template.md`（不参与双轨）。
2. **规则并入**：`RULE.md` 新增 **R7 · 文档双轨制**（R7.0 铁律 / R7.1 技术文档准则 / R7.2 翻译官 9 条 / R7.3 同步 SOP / R7.4 仓库与路径）；底稿 §B.5 索引行消费进入口文件，不留在规则正文。
3. **入口文件**：`CLAUDE.md` 规则清单更新为 R1–R7，并新增「规则索引」表（含 `| 文档双轨（铁律/双准则/同步SOP） | R7 | 改技术文档或同步学习文档时 |`）。
4. **技能安装**：`.claude/skills/doc-study-sync/SKILL.md` 落地（内嵌规则执行副本，冲突时以 RULE.md 为准）。
5. **首跑初始化**：镜像仓库克隆后建 `AutoAiSelfHeal/` 文件夹，3 篇源文档走强制流水线（Diff → 熔断 → 翻译官 9 条 → 版本印记 → 三重校验 → 提交推送）。

## 待解决问题

- 镜像推送依赖 GitHub 网络与凭据；失败则保留本地 commit 并回报（SKILL 约定重试不超过 1 次）。
- 双轨为纯文档约定，无自动化校验；后续可考虑 CI 抽查镜像版本印记与源 HEAD 偏差。

## 下一步计划

- 后续每次修改 `docs/architecture.md`、`docs/roadmap.md`、`README.md` 时，按 R7.3 SOP 同批同步镜像并推送。
- 增量同步触发语：「同步学习文档 <篇名>」（doc-study-sync 技能）。
