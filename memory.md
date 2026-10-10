# memory.md

> **定位**：跨会话持久化偏好与环境事实——回答"这个项目怎么跟我配合、本机环境是什么样"。
> **状态**：活文档（环境事实变化时必须同步；读者：AI 协作者与本人）
> **最后更新**：2026-10-08 · 关联：[CLAUDE.md](CLAUDE.md)（上手说明）、[docs/README.md](docs/README.md)（文档地图）

## 沟通语言

- 始终使用**中文**回答用户问题、撰写说明与提交信息。
- 代码、命令、标识符、文件路径保持英文原样，不做翻译。

## 文档约定

- 文档入口：**`docs/README.md`**（文档地图 + 写作规范 + 双轨说明）；动手改文档前先读它。
- `docs/sessions`、`docs/plans`、`docs/reviews` 是**只读历史**，禁止修改；结论变化写新文档并在 `docs/TODO.md` 登记。

## 被测系统与领域知识

- 正式被测系统为**管伊佳 ERP（jshERP）**。领域知识（角色语义铁律、接入参数、勘测结论、造数模式、坑位速查）
  **唯一归属**：`.claude/skills/erp-domain/SKILL.md`——编写 / 调试 ERP 任务前先读它，本文件不再复述。
- 一句话铁律：**租户（jsh）= 业务数据管理员**（UI 测试与 API 造数共用其凭证）；**admin = 平台运维用户**，
  不能编辑业务数据，测试基建不使用。
- 敏感文件（`.env` 等）操作遵守 `RULE.md` §R8：先读后改、增量优先、写后掩码核对、事故即报。

## 本机环境事实（2026-10-08 更新）

- **DSH 模型路由**：`commandcode / deepseek/deepseek-v4.1-flash`，思考档 = **max**
  （配置在 `~/.dsh/profiles/desktop/cordis.patch.yml` 的 `llm-pi-ai` 路由 + `reasoning: max`）。
  旧记载的 `opencode-custom / glm-5.3-flash` 与"`DEEPSEEK_API_KEY` 失效"**均已作废**。
- **模型密钥与端点必须同平台**：本机 `.env` 的 `COMMANDCODE_API_KEY`（2026-10-10 由 `DASHSCOPE_API_KEY` 改名而来）是
  **CommandCode 平台**的 key（对 `api.deepseek.com` / `api.openai.com` 一律 401）；`OPENAI_API_KEY` 仍保留为同值副本
  （代码默认值指向它）。项目 LLM 与 VLM 都走 CommandCode 网关（`config/settings.yaml` 的 `llm.*` / `vision.*` 覆盖代码默认值）。
- **VLM 现况（2026-10-10 起）**：`deepseek/deepseek-v4.1-flash`（实测支持图片输入），与 LLM 同网关同凭据；
  旧选型通义 `qwen3-vl-plus` + 百炼 MaaS 专属端点已停用（端点留在本机 settings 备份里，可回滚）。
- **ERP 环境**：前后端默认未启动（`192.168.1.3:9999` 与 `localhost:3001`）；erp 用例会自动 skip（2026-10-08 起）。
- **子代理约定**：启动子代理（`subagent` / workflow `agent()`）前，先确认本次使用哪个 provider/model；
  workflow `agent()` 一律显式传 `{provider, model}`，不依赖默认路由。
