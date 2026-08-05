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
展示层        reporting/   Allure + 自研 HTML 看板 + 视频回放（Playwright Trace）
AI 自愈 Agent agent/       大脑：编排闭环 / 诊断 / 多策略修复（llm 抽象 + knowledge 知识库）
数据采集层    collect/     截图 / DOM 快照 / 网络日志 / 执行轨迹
能力建设层    engine/      Playwright 封装 / 自愈定位器 / 智能等待 / 弹窗处理
```

## 快速开始

```bash
pip install -e ".[dev,llm]"
pytest -m unit        # 单元测试（CI 门禁，无需浏览器）
pytest -m e2e         # 端到端测试（使用系统已装的 Chrome，headless）
pytest                # 全部测试
ruff check .          # 代码检查
```

> **浏览器**：默认走系统已装的 Chrome（`channel: chrome`，免下载内核）。
> 如需 Playwright 自带 Chromium：`playwright install chromium` 并把 `config/settings.yaml` 的 `browser.channel` 改为 `chromium`。

> **自愈开关**：`pytest --selfheal` / `--no-selfheal`（优先于 `settings.healing.enabled`）。
> **自愈演示**：`tests/e2e/pages/demo_page.html`（模拟 UI 改版导致定位器失效），由 `-m e2e` 覆盖三场景：自愈成功 / 兜底 / 关闭=原生。

> **视频回放（Playwright Trace）**：`pytest --trace-healing` 录制，用 `playwright show-trace reports/traces/trace-<test>.zip` 交互式回放（DOM + 操作时间线）；CI 会随 e2e 上传 traces 产物。

> ✅ **核心自愈闭环已跑通**：启发式 + 语义（DeepSeek）+ 视觉（qwen3-vl-flash）多策略、SQLite 知识沉淀、弹窗处理、智能等待；真实模型已验证（见 `docs/roadmap.md`）。

> 🧠 **Phase 5 · 知识库语义化「越用越聪明」**：修复知识经本地确定性向量（零 API 费用）检索复用——L1 精确命中（`repair_key` 硬短路，ID 变了文本/结构不变仍命中）→ L3 语义向量检索（相似场景直接复用）。演示：`python scripts/demo_semantic_reuse.py`。

> 🛡️ **Phase 5 · 风险控制**（`config/settings.yaml` `healing` 段）：高风险页豁免（`exclude_url_patterns`）、dry-run 仅报告不执行（`dry_run`）、修复写回人审清单（`fix_proposals`，不自动改库）、看板区分真自愈 vs flaky、多模态成本看板（T13–T17）。

> ⚠️ **生产使用请先读「预期与风险」**：默认**不自动乱合库**（修复建议须人审）、**多模态按图计费**、勿把 **flaky 偶发绿**当自愈成功、**高风险页**（支付/授权/审计）通常不自愈。详见 [docs/architecture.md · 预期与风险](docs/architecture.md)。

## 写你的第一个 POM + 用例

基座提供 `BasePage` 与 `pom` fixture（详见 `docs/plans/2026-08-05-base-framework.md`）。POM 只依赖 `page` 接口——注入原生 page 或带自愈的 `healing_page`，**同一份代码都能跑**（决策 D3）。

```python
from selfheal.engine.base_page import BasePage

class LoginPage(BasePage):
    url = "https://example.com/login"

    def login(self, user="tester", pwd="secret") -> None:
        self.locator('[data-testid="username"]', description="用户名输入框").fill(user)
        self.locator('[data-testid="password"]', description="密码输入框").fill(pwd)
        # description 让自愈能凭语义重定位；fallback 提供人工备用定位器（HealingPage 增强参数）
        self.locator("#submit", description="登录按钮", fallback="#submit-v2").click()
```

用例用 `pom` fixture 拿页面对象（原生 page）：

```python
def test_login(pom):
    login = pom(LoginPage)
    login.open()
    login.login()
    assert "欢迎" in login.locator("#result").text_content()
```

想要自愈时，改用 `healing_page` 注入（演示页示例见 `tests/e2e/pages/demo_page.py`）：

```python
def test_login_with_healing(healing_page):
    demo = DemoPage(healing_page)   # 带自愈：失效定位器自动修复
    demo.open()
    demo.login()
```

要点：
- `description` / `fallback` 是 HealingPage 增强参数；原生 Page 下不传即可（`BasePage.locator` 按需透传）。
- 自愈开关：`pytest --selfheal` / `--no-selfheal`。
- 录 trace：`pytest --trace-healing`。
