# memory.md

本文件记录跨会话的持久化偏好与约定。

## 沟通语言

- 始终使用**中文**回答用户问题、撰写说明与提交信息。
- 代码、命令、标识符、文件路径保持英文原样，不做翻译。

## 被测系统与领域知识

- 正式被测系统为**管伊佳 ERP（jshERP）**：领域知识（角色语义铁律、接入参数、勘测结论、
  造数模式、坑位速查）见 `.claude/skills/erp-domain/SKILL.md`，编写/调试 ERP 相关任务时先读它。
- **角色语义**：租户（jsh）= 业务数据管理员，UI 测试与 API 造数共用其凭证；
  admin = 平台运维用户（仅平台菜单配置/创建租户），**不能编辑业务数据**，测试基建不使用。
- 敏感文件（`.env` 等）操作遵守 `RULE.md` §R8：先读后改、增量优先、写后掩码核对、事故即报。

## DSH 环境与子代理约定（2026-09-03）

- **默认模型路由**：DSH `agent-default-model = opencode-custom / glm-5.3-flash`
  （`C:\Users\Administrator\.dsh\settings.yaml`），**保持不动**。若出现"主会话正常、
  子代理失败"，先检查此段与 `llm-pi-ai.providers.opencode-custom` 定义是否仍在。
- **DEEPSEEK_API_KEY 已失效**（回落 deepseek-official 即 401 "API key is invalid"，
  2026-09-03 基线冒烟实测确认）：处置 = 用户在 GUI 模型设置中更新该凭据（2.A），
  更新后由助手以 1-agent 冒烟（显式 provider/model）做前后对照验证；验证通过前
  不得让任何任务回落到 deepseek-official。勿把 opencode 的 key 粘入该条目（端点不同）。
- **子代理模型选择约定**：每次启动子代理（`subagent` / workflow `agent()`）前，
  **先询问用户本次使用哪个模型**（默认推荐 opencode-custom/glm-5.3-flash）；
  workflow `agent()` 一律显式传 `{provider, model}`，不依赖默认路由。
- 背景：2026-09-03 两轮 code review 时子代理全数失败（当时默认路由缺省，回落到
  失效 key），审查由主会话人工逐文件完成，事后以 5 个子代理独立复核，
  两轮 10 项结论全部成立（详见 `docs/reviews/2026-09-03-code-review.md`）。
