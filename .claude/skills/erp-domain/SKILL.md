---
name: erp-domain
description: 管伊佳 ERP（jshERP）被测系统领域知识库。当用户要求「编写/调试 ERP 相关用例或 POM」「问 jshERP 的角色/权限/账号语义」「排查 ERP 测试环境问题（登录/验证码/菜单/造数）」或提到「管伊佳 ERP/jshERP/被测系统迁移」时触发。包含：角色语义铁律（租户 vs admin）、环境接入参数、勘测结论（登录 MD5/鉴权头/DOM 结构/弹窗与引导层陷阱）、造数模式与测试基建约定。不触发于：demo 演示页相关任务或与 ERP 无关的框架开发。
---

# ERP Domain（管伊佳 ERP 被测系统领域知识）

> 来源：T23 被测系统迁移的实测勘测（2026-08-31 ~ 09-01）+ 人工专家确认。
> 本文是**领域事实**，随 ERP 环境变化同步更新；与代码冲突时以代码为准并回改本文。

## 👥 角色语义（铁律，人工专家确认 2026-09-01）

| 账号 | 角色 | 能做 | 不能做 | 测试基建用途 |
|---|---|---|---|---|
| **租户**（如测试租户 `jsh`） | 业务数据的管理员 | 编辑全部业务数据（商品/供应商/单据/仓库/财务） | —— | **UI 测试与 API 造数/清理统一使用** |
| **admin** | 平台运维用户（SaaS 平台层） | 配置平台菜单（系统管理-功能管理）、创建租户 | **不能编辑任何业务数据** | **测试基建不得使用**（后端未拦截不代表可以用） |

> 教训：曾误用 admin 做 API 造数（后端未拦截但语义违规），已纠正为租户凭证统一（`SutConfig.username_env/password_env` → `ERP_USERNAME/ERP_PASSWORD`）。

## 🌐 环境接入

| 项 | 值 | 备注 |
|---|---|---|
| 前端 | `http://localhost:3001` | Vue3 + **Ant Design Vue**（非 Element-UI）；`sut.base_url` |
| 后端 API | `http://192.168.1.3:9999/jshERP-boot` | Spring Boot；`sut.api_base_url`（**非被测对象**，仅造数/清理） |
| 登录 | `POST /user/login`，body `{loginName, password}` | **password 需 MD5 后提交**（前端同款处理，明文报 `user password error`） |
| 响应 | `{code: 200, data: {msgTip, token}}` | token 为字符串 |
| 鉴权头 | `X-Access-Token: <token>` | 全部业务接口 |
| 验证码 | **已被关闭**（被测环境配置） | 若重新开启，UI 登录自动化将卡死——需先关闭 |
| 凭证 | `.env` 的 `ERP_USERNAME/ERP_PASSWORD` | 明文绝不入库（.gitignore 已忽略） |

## 🔍 勘测结论（UI 结构与陷阱）

1. **登录页**：路由 `/user/login`；输入框稳定 id `#loginName` / `#password`；登录按钮 `button.ant-btn-primary`（class 含 `login-button`）；登录成功跳 `/dashboard/analysis`——**vue-router 异步跳转必须 `wait_for_url`，立即断言 URL 会踩坑**。
2. **内容区不在 iframe**：SPA 同文档渲染，直接 goto 路由即可（如 `/material/material` 商品管理）。
3. **antd 弹窗默认不销毁**：关闭后 DOM 残留（hidden），再次打开会出现**两份同名 id**——污染定位与自愈候选。对策：场景设计避免"同一页签反复开关弹窗"，或选择器限定 `.ant-modal:visible`。
4. **intro.js 新手引导遮罩**（`.introjs-overlay` 等）会**拦截全页点击**（root_cause=covered）——每个账号首次登录触发；对策：`tests/e2e/pages/erp/__init__.py::dismiss_intro`（DOM 移除；勿用 `display:none`，会影响祖先层隐藏时其他元素的可见性判定）。
5. **商品管理页**（`/material/material`）：新增弹窗表单输入框带稳定 id（`#name/#standard/#model/#unit/#color/#brand` 等）+ placeholder 齐全（"请输入商品名称"）——heuristic 语义匹配（description 与 placeholder token 重叠）的依据。
6. **列表搜索框**：商品页 `input.ant-input[placeholder^=请输入名称]` + 查询按钮 `button.ant-btn-primary`（两字按钮文本带空格，如"新 增""查 询"——`:has-text` 按单字匹配）。
7. **菜单为 JS 动态**（无 href，不能 goto 直达未启用页面）：当前实例**未启用供应商页面**（采购管理组下仅采购订单/入库/退货）——启用入口：ERP「系统管理 → 功能管理」。

## 🧪 测试基建约定

- **fixtures**（`tests/conftest.py`）：`erp_page`（租账号 UI 登录态 + HealingPage 自愈开 + `set_default_timeout(8000)`，失效定位器较快进入自愈）、`erp_api`（租户 API 客户端）；凭证缺失自动 `pytest.skip`。
- **marker**：`erp`（pyproject 已注册）；CI 门禁 `-m "e2e and not erp"`（CI 无 ERP 环境）。
- **造数模式**（`tests/e2e/api/erp_client.py`）：`add` 响应不带 id → `list`（`search` 参数为 **JSON 字符串**）反查 id → `deleteBatch?ids=` 清理（单条 `delete` 会 500，勘测实测）。
- **Allure**：marker `erp` → feature「ERP 被测系统」（`allure_bridge._FEATURE_PRIORITY` 中 erp 优先级最高）。

## ⚠️ 已知坑位速查

| 症状 | 根因 | 对策 |
|---|---|---|
| 登录报 `user password error` | 密码未 MD5 | 提交前 `hashlib.md5` |
| 点击被拦截 / root_cause=covered | intro.js 引导遮罩 | `dismiss_intro(page)` |
| 定位器匹配 2 个 / 自愈候选同名 | antd 弹窗 DOM 残留 | 单弹窗场景设计 / `.ant-modal:visible` |
| 断言 URL 仍是登录页 | vue-router 异步跳转 | `wait_for_url("**/dashboard/**")` |
| VLM 调用超时/JSON 截断 | plus 级模型响应慢 | `VisionConfig.timeout_s=60/max_tokens=1000`（已放宽） |
| 删除接口 500 | 单条 delete 不可用 | 用 `deleteBatch?ids=` |