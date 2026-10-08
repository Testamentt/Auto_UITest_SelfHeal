# Code Review — 全量审查（2026-10-08）

> **修复状态**：✅ **H1–H6 与 M1–M16 已修复**（2026-10-08 同日，见 `2026-10-08-fix-report.md`）；
> ⏸️ 主动延期 2 项：**M10**（engine/collect 分层重构，需独立计划 + 文档双轨同步）、**M15 第 3 条**（链式自愈 e2e，需先加 DOM 夹具）；
> 📋 **Low 20 项未开工**，登记在 [`docs/backlog/low-priority.md`](../backlog/low-priority.md)。
> 验收：`pytest -m unit` **404 passed**（修前 312，+92）· `ruff check .` **0 errors** · `ruff format --check .` 全绿 · 全量 `pytest` 见第七章更新。
> **本报告保持"审查当时"的原始结论**，仅对两处事实做更正：① H1 中关于 `text=` 匹配语义的表述（实测为精确匹配，风险来自未限定作用域与关键词子串）；② 7.1 的根因（不是 key 失效，而是 key 与端点平台不匹配）。

> 审查范围：`src/selfheal/` 全部 46 个模块（agent / engine / collect / knowledge / llm / reporting）、
> `tests/`（conftest + unit + e2e）、`config/`、`pyproject.toml`、`.github/workflows/ci.yml`、`scripts/`。
> 方法：核心链路（orchestrator / diagnose / strategies / persistence / dom / healing_locator / popup_guard）
> **逐行精读**；外围模块（engine 其余、collect、knowledge、reporting、llm、tests/CI）由 3 个独立子代理
> 分片深审 + 上级逐条复核取证；静态门禁与全量测试**实跑**取证。
> 基线（本次实测）：`ruff check .` **0 errors**；`pytest -m unit` **312 passed / 28 deselected（8.49s）**；
> `ruff format --check .` **2 文件待重排**（CI 未门禁，见 M14）；全量 `pytest`
> **1 failed / 334 passed / 3 xfailed / 2 errors（438.39s）**——失败与 error 均为**环境类**，
> 其中一条暴露了真实缺陷：真实 LLM 调用失败（**key 与端点平台不匹配 → 401**）导致 LLM 侧三条能力静默降级（见 7.1），
> ERP 未启动导致两个用例 ERROR 而非 skip（见 7.2）。
> 关联：上轮 `docs/reviews/2026-09-03-code-review.md`（其 5 项修复本轮已确认落地：example 配置、propose_pr 残片、
> 端点外移、erp_page 聚合登记、llm/io 归位）。

---

## 一、总体评价

工程水准**明显高于同类个人项目**：分层清晰（展示层 / Agent / LLM 抽象 / 采集 / 能力建设）、
docstring 密度高且写"为什么"、异常分支普遍有语义与注释、资源归属用 `owns_*` 显式区分自建与注入、
SQL 全参数化、知识库 schema 迁移可重复执行、防幻觉护栏（策略只能从"页面真实候选"中选）真实生效、
T5 置信度归一化 / T16 flaky 区分 / T21 xdist 分片聚合 / T13 高风险页豁免等设计可直接作为面试素材。

本轮**未发现阻断级缺陷**（无数据损坏、无静默错误结果、无不可恢复失败）。发现的问题集中在三类：

1. **安全/正确性边缘**：弹窗知识路径的"全页 `.first` 点击"可能点到业务按钮（H1）；
2. **可配置性/可观测性缺口**：LLM 超时与 token 上限硬编码且被 `extra='forbid'` 堵死（H3）、
   四类模型不可用原因统一静默降级为 `None` 且零日志（H4）、VLM 图片无体积护栏（H5）；
3. **口径与契约漂移**：flaky 指标把"失败"计入"侥幸通过"（H6）、双后端检索语义不一致（M4）、
   `is_verified` 无框架内入口导致 L3"人审后自动采纳"闭环缺一环（M3）。

另有一处**资源所有权漏洞**（H2）：`HealingPage` 自建的知识库连接在 `close()` 时无人释放——
本轮唯一"框架自身 API 语义"层面的实质缺陷。

---

## 二、High（建议本轮修复）

### H1 · 弹窗知识路径"全页 `.first` 点击"可能点到业务控件（安全/数据风险）

- 位置：`src/selfheal/engine/popup_guard.py:68`、`:136-144`、`:154-172`、`:32`、`:44-47`；
  `src/selfheal/agent/dom/selector_builder.py:23-24`
- 证据：
```python
# popup_guard.py:136  知识命中的关闭按钮：**全页**取第一个可见者点击（未限定在弹窗容器内）
loc = self._page.locator(selector)
if loc.count() > 0 and loc.first.is_visible():
    loc.first.click(timeout=_CLICK_TIMEOUT_MS)
# selector_builder.py:23-24  纯文本按钮沉淀成 text="关闭"（Playwright text= 是大小写不敏感子串匹配）
if text := el.field("text"):
    return f'text="{text}"'
# popup_guard.py:47  关键词也是子串匹配："关闭" in "关闭订单" → True
return any(kw in haystack for kw in _CLOSE_KEYWORDS)
```
- 影响：首次关闭成功后把 `text="关闭"` 之类**未限定作用域**的选择器沉淀进知识库；此后每次弹窗检测
  都可能在整页范围内点到"关闭订单/关闭会话"等业务动作按钮（在 DOM 顺序更靠前即中招）。
  这是"自动化把业务数据改坏"的一类风险，且错误选择器会被反复复用而不会自我纠正。
- **实测校正（2026-10-08 修复阶段）**：用真浏览器验证了语义——`text="关闭"`（带引号）是**精确匹配**
  （整串相等、大小写敏感），`text=关闭`（不带引号）才是子串匹配。故风险**不在于沉淀选择器会通配**，
  而在于两点：① 点击未限定在弹窗容器内（作用域）；② `_is_close_hint` 的关键词子串匹配会把
  "关闭订单"当成关闭按钮（候选误判）。修复按这两点落地（见 `2026-10-08-fix-report.md` 组 1）。
- 修复建议：① `_try_click_selector` 限定在弹窗容器内（`container.locator(selector)`）；
  ② 文本分支改 `get_by_role("button", name=..., exact=True)` 或 `text=` 加引号+`exact`；
  ③ 关键词表按**精确标签集合**匹配（`{"关闭","close","dismiss","×","✕"}`）而非子串；
  ④ 关闭后复检弹窗是否真的消失，再决定沉淀。

### H2 · `HealingPage` 自建的知识库连接无人关闭（资源所有权漏洞）

- 位置：`src/selfheal/engine/healing_locator.py:416-422`、`:436-442`；`src/selfheal/agent/orchestrator.py:85-86`
- 证据：
```python
# healing_locator.py:418  HealingPage 自建 knowledge（未注入时）
self._knowledge = knowledge or build_knowledge_store(settings)
# :419-421  同一个对象交给 orchestrator
self._orchestrator = SelfHealOrchestrator(page, settings, self._knowledge, self._reporter)
# orchestrator.py:85-86  orchestrator 认为"传进来的就是注入的"，不据为己有
self._knowledge = knowledge or build_knowledge_store(settings)
self._owns_knowledge = knowledge is None          # ← False：不会关闭
# healing_locator.py:436-442  close() 只关 orchestrator
def close(self) -> None:
    if self._orchestrator is not None:
        self._orchestrator.close()
```
- 影响：非 fixture 用法（`HealingPage(page, settings)`，README/文档鼓励的插件用法）每创建一个页面就泄漏
  一个 SQLite 连接（WAL 模式下还持有 `-wal`/`-shm` 文件句柄）；测试因 fixture 全部**注入**会话级知识库
  而侥幸未暴露（`tests/conftest.py:79-86` 自己关闭）。该行为与 `CLAUDE.md`「自愈资源（知识库连接 / LLM 客户端）
  经 `HealingPage.close()` / `SelfHealOrchestrator.close()` 释放」的书面契约**不一致**。
- 修复建议：`HealingPage` 记录 `self._owns_knowledge = knowledge is None`，在 `close()` 中按标记关闭
  （顺序：先 orchestrator 再 knowledge），或改为"自建即注入给自己 + orchestrator 始终不拥有"的单一所有权。

### H3 · LLM 超时 / `max_tokens` 硬编码，且 `extra='forbid'` 让用户无法配置

- 位置：`src/selfheal/llm/openai_client.py:38-39`、`:70-75`；`src/selfheal/config.py:63-68`、`:27-35`；
  `src/selfheal/llm/factory.py:26-32`；对照 `src/selfheal/config.py:190-191`
- 证据：
```python
# openai_client.py:38-39  默认值写死在签名里，factory 也不透传
timeout_s: float = 15.0,
max_tokens: int = 2000,
# config.py:63-68  LLMConfig 只有 enabled/provider/model/api_key_env/base_url/temperature
# config.py:190-191 同一份配置里 VLM 侧却早就可配（T23 踩坑后修过）
timeout_s: float = 60.0
max_tokens: int = 1000
```
- 影响：`config.py:181-182` 的注释已记录 VLM 侧"20s/500 导致 ERP 场景超时/截断而自愈失败"的教训，
  LLM 侧仍是 15s/2000 且**不可配**；用户在 `llm:` 段写 `timeout_s` 会被 `extra='forbid'` 直接拒绝
  （子代理实测 `ValidationError`）。长回复模型超时 → `UnavailableError` → 诊断退规则式、语义策略静默失效，
  表现为"自愈偶尔不灵"。
- 修复建议：`LLMConfig` 增 `timeout_s: float = Field(gt=0)`、`max_tokens: int = Field(gt=0)`，
  factory 透传，`config/settings.example.yaml` 同步（并给 `VisionConfig` 的两个字段补 `Field(gt=0)`）。

### H4 · 四类"模型不可用"统一静默降级为 `None`，工厂层零日志

- 位置：`src/selfheal/llm/factory.py:20-24`、`:33-36`、`:42-45`、`:56-59`；`src/selfheal/config.py:255-259`
- 证据：
```python
if not llm_cfg.enabled: return None
api_key = get_api_key(llm_cfg.api_key_env)
if not api_key: return None                       # ① 缺 key
try: return get_llm(...)
except KeyError: return None                      # ② provider 名拼错
except UnavailableError: return None              # ③ SDK 缺失/不可用
```
- 影响：`enabled=false` / key 名写错 / key 过期 / provider 拼错 / SDK 未装，五种原因收敛成同一个 `None`，
  下游 `orchestrator.py:89-99` 静默继续（无日志），唯一线索是报告里 `llm_calls=0`。对照 `fix_generator.py:183-188`
  对"未知策略名"都有 warning——此处诊断能力倒挂。同理 `load_settings` 在配置文件不存在时返回全默认值且无提示
  （CI 依赖这一点，但本地也静默）。
- **实证（本轮全量跑）**：本机 key 失效（401）时，`test_semantic_llm_locate_login_button` 只报
  `assert None is not None`，日志里查不到任何原因；探针才定位到 `AuthenticationError`（见 7.1）。
  即"key 过期"这类高频故障当前**无法从框架输出中诊断**。
- 修复建议：每个 `return None` 前 `logger.warning` 并区分原因（**只打印环境变量名，绝不打印值**）；
  显式传入的配置路径不存在时直接 `raise`，默认路径不存在时才 warning。

### H5 · VLM 调用无图片体积/像素护栏，`full_page` 截图原样 base64

- 位置：`src/selfheal/llm/openai_vision.py:57`、`:68`；调用方 `src/selfheal/collect/collector.py:192`
- 证据：
```python
b64 = base64.b64encode(image).decode("utf-8")          # 无尺寸判断
"image_url": {"url": f"data:image/png;base64,{b64}"}   # 长页 PNG 可达数 MB → base64 ×1.33
```
- 影响：全仓库 grep 无任何 `resize/thumbnail/quality/jpeg/max_bytes` 补偿；超过服务端请求体上限即 400 →
  `UnavailableError` → `visual.py:52-56` 返回 `None` 被静默跳过，表现为"视觉策略在真实长页面上永不生效"
  （demo 页短，冒烟测试掩盖了该问题）。
- 修复建议：`VisionConfig` 增 `max_image_bytes`（默认如 1.5MB），超限时用 Pillow 等比缩放/转 JPEG
  （可先按 `requestImageMaxBytes` 语义实现最小版），并在跳过时记 warning。

### H6 · flaky 指标把"自愈失败"计入"侥幸通过"，`verified_rate` 可能 >1

- 位置：`src/selfheal/reporting/metrics.py:41-43`、`:59`；渲染处 `src/selfheal/reporting/dashboard.py:39`
- 证据：
```python
verified = sum(1 for r in records if r.verified)
flaky = sum(1 for r in records if not r.verified)     # ← 未与 success 取交集
verified_rate=(verified / success) if success else 0.0 # ← 分子未与 success 取交集
```
- 影响：`success=False` 的记录（T13 高风险页豁免 `orchestrator.py:142-153`、`on_uncertain=fail` 的失败修复）
  被统计为"flaky 侥幸通过"，看板对外口径失真；若出现 `success=False, verified=True`（`verified` 默认 True）
  比率会 >1。`tests/unit/test_risk_metrics.py:60-69` 已把现状固化为断言，修复需同步改测试。
- 修复建议：`flaky = sum(1 for r in records if r.success and not r.verified)`、
  `verified = sum(1 for r in records if r.success and r.verified)`、`verified_rate` 加 `min(1.0, ...)`。

---

## 三、Medium（建议排期）

| # | 位置 | 问题与影响 | 修复方向 |
|---|---|---|---|
| M1 | `llm/_exceptions.py:8-9`、`llm/openai_client.py:70-79`、`llm/openai_vision.py:59-78` | 异常**无分级**：401（key 过期）与 429/超时同级；未显式设置 `max_retries`，openai SDK 默认 2 次重试 → 一次失败最多 3 次真实付费调用，行为与成本不可控、不可见 | 按 `AuthenticationError/RateLimitError/APITimeoutError` 映射为 Fatal/Transient 两类；显式 `max_retries` 并透传配置 |
| M2 | `engine/healing_locator.py:46-47`、`:367-375` | 默认配置（`on_uncertain=use_fallback` 且未给 `fallback`）下抛 `HealingFailedError(Exception)`；关闭自愈时同一调用抛 Playwright `TimeoutError` → POM 里 `except TimeoutError:` 开/关自愈语义不同 | 让 `HealingFailedError` 同时继承 Playwright `TimeoutError`（可用时），或 `raise ... from exc` 并在文档明示 |
| M3 | `knowledge/schema.py:33`、`knowledge/store.py:110`、`knowledge/sqlite_store.py:278`、`agent/strategies/semantic.py:155-159` | `set_verified` 在 Protocol 与两个后端都实现了，但**框架内无任何调用点/CLI**（grep 仅测试调用）→ L3"人审过 → sim>0.92 自动采纳"这条防污染闭环只能靠人工直连 DB | 提供 `scripts/verify_case.py` 或 CLI 子命令作为人审入口；无入口前应在文档标注该分支"待人工标记" |
| M4 | `knowledge/store.py:19-20`、`:30-39`、`:100-108`；`knowledge/sqlite_store.py:194-204`、`:272`；`knowledge/base.py:15-75` | 双后端语义漂移：memory 不去重、指纹命中取"插入序第一个"（SQLite 取 `ORDER BY confidence DESC`）、`last_hit_at` 为 ISO 串（SQLite 为 `datetime('now')`）而 `store.py:101` 注释写"与 SQLite 端一致"（**与事实相反**）；Protocol 未声明 `count_repairs`/`close`（`store.py:126`、`sqlite_store.py:292`），`_safe_close` 走 `getattr` 会静默跳过 | 统一 upsert/排序/时间格式；Protocol 补 `count_repairs()`/`close()`，memory 后端补 no-op `close()` |
| M5 | `knowledge/sqlite_store.py:98-113`、`:131-137`；`knowledge/factory.py:26-31` | 每次构造都执行两条全表 `DELETE`（新库亦然）→ xdist 多 worker 打开时反复抢写锁；`_migrate_legacy_tables` 是 check-then-act，多进程首升级可能撞 `duplicate column name` → factory 捕获 `sqlite3.Error` 后**只记 warning 就降级 memory**（"跨重启持久化"这一核心能力丢失却与普通告警同级，表现为"自愈可用但知识不再沉淀"） | 仅在检测到重复行/旧 schema 时执行；DDL 加 `BEGIN IMMEDIATE` 或忽略 duplicate column；降级用 `logger.error` 并在报告环境页标注 |
| M6 | `engine/smart_wait.py:34-51`；`config.py:71-83` | `timeout_ms=0` 时 `wait_for` 语义是"禁用超时"（函数不再有界）；可见性与稳定判定共享同一预算，慢加载页面在 deadline 前刚可见即被判"未稳定"（伪失败）；`poll_ms/stable_ms` 无值域校验 | `timeout_ms<=0` 显式抛错或换算；稳定判定用独立窗口；`ActionWaitConfig` 加 `Field(gt=0)` |
| M7 | `engine/browser.py:23-31`、`:44-48`、`:35` | `start()` 成功后 `launch()` 抛错时 `__exit__` 不会执行 → Playwright node 驱动子进程泄漏（CI 无 Chrome 属高频场景）；`__exit__` 未把 `_browser/_pw` 置 None（重复退出会重复 stop）；`:35` 用 `assert` 做前置校验（`-O` 下失效） | `try/except` 内 `start()+launch()`，失败先清理再抛；置空属性；`assert` 换显式 `raise` |
| M8 | `reporting/allure_bridge.py:95-96`、`:110-111`、`:125-126`；`collect/collector.py:92-93`、`:105-106`；`engine/healing_locator.py:284-287`；`engine/popup_guard.py:142-143` | 多处 `except Exception` 静默返回/`pass` 且**无日志**：附件写入失败、环境页缺失、网络事件丢失、清弹窗后重试失败在 CI 日志中完全不可见 → 违反 R4"禁止静默失败"（同文件 `popup_guard.py:94/102` 已正确 warning，风格不统一） | 统一补 `logger.warning(..., exc_info=True)`；`allure_bridge` 先加 module logger |
| M9 | `agent/persistence.py:25`、`:41-42`、`:88-91`；`engine/healing_locator.py:289-303`、`:354-356` | B1 暂存细节：`_pending` 满 64 条时静默丢弃最旧（无日志，该次修复永不沉淀）；`_committed` 集合无界增长；二次自愈成功后第一次闭环的 `attempt_id` 永久悬挂在 `_pending` | 淘汰时记 warning；`_committed` 用有界结构或改 `dict` 计数；二次自愈前先丢弃上一次暂存 |
| M10 | `engine/healing_locator.py:23-30`、`:418-422`；`engine/popup_guard.py:15-17`；`collect/collector.py:25-31` | **分层倒置**：能力建设层/采集层反向 `import` 上层（agent / knowledge / reporting），且"插件装配点"（构建 orchestrator/knowledge/reporter）落在引擎层 → 导入引擎即拉起整个 Agent 层，与 CLAUDE.md 的分层叙述不符（当前无循环导入） | 装配点上移到 `agent/` 或新增 `plugin/` 层；engine 侧对编排器只依赖本层 Protocol，`FailureContext` 换中立 DTO |
| M11 | `agent/dom/selector_builder.py:19`、`:22`、`:24`、`:26` | 生成的选择器**未转义引号**：`data-testid/id/aria-label/text` 含 `"` 时产出非法选择器（运行期报错）；`text="保存"` 是子串匹配，可能命中"保存并提交"等非目标元素（自愈"修对了但点错了"） | 转义或改用 `get_by_test_id/get_by_role(name=..., exact=True)`；id 分支已有安全集判断，可复用同思路 |
| M12 | `tests/conftest.py:184-191`、`:216-224`、`:225` | `erp_api` 的 `client.login()` 与 `erp_page` 的 `open_login()` 都在 try 之外：**凭证已配置但 ERP 服务未启动时报 ERROR 而非 skip**（本轮实测 ERP `192.168.1.3:9999` 不可达，全量跑必红）；`erp_page` 还缺 `page.close()`（对比 `healing_page:161`），自建的 LLM/VLM 客户端不释放 | 把 login 纳入 `except ErpApiError/连接错误 → pytest.skip`；`erp_page` 补 `finally: page.close()` |
| M13 | `.github/workflows/ci.yml:62`、`:66-68`；`src/selfheal/config.py:48`；`src/selfheal/engine/browser.py:28-30` | CI `playwright install chromium` 装的内核**从未被使用**——默认 `channel="chrome"` 走系统 Chrome，CI 生成的 settings.yaml 不含 `browser.channel`；一旦 runner 镜像无 Chrome，报错信息与被声明的"已装 chromium"矛盾 | CI 生成配置时显式写 `browser: {channel: chromium}`，或删掉安装步骤并在 README 声明依赖系统 Chrome |
| M14 | `pyproject.toml:41`、`:50`、`:56`；`.github/workflows/ci.yml:32-33` | 门禁漏洞：`addopts = "-ra"` 无 `--strict-markers`，marker 漏写/拼错的测试被两个 job 同时 `deselected`，CI 全绿但功能零覆盖；CI 只跑 `ruff check .`，**不跑 `ruff format --check .`** → 本轮实测 2 个文件已漂移（`README.md` 内嵌 Python 代码块、`tests/conftest.py:109`）；`ignore = ["E501"]` 与 `line-length = 100` 相互抵消 | 加 `--strict-markers` + "未分类测试"断言步骤；CI 增 `ruff format --check .`；去掉 E501 ignore |
| M15 | `llm/openai_vision.py:51-84`；`tests/unit/test_review_hardening.py:107-120`；`tests/unit/test_chain_healing.py:44-63`；`tests/unit/test_llm_factory.py:32-38` | 测试缺口：VLM `analyze_image` 的**请求组装/解析/降级全路径零单测**（仅测 `close()`，e2e 模块级 skip）→ 违反 R2；链式自愈只有自写 fake，无真实 Playwright e2e；`test_llm_factory` 的 `if client is None: pytest.skip(...)` 使后续断言恒真（工厂回归会以 skipped 收场） | 镜像 `_inject_fake_openai` 补 VLM 单测（正常/异常/空 choices/content=None/did not install）；e2e 加一条链式场景；删掉 skip 分支改直接断言类型 |
| M16 | `reporting/allure_bridge.py:86`；`tests/conftest.py:106-110`；`reporting/fix_proposals.py:89-93`；`tests/unit/test_risk_control.py:96` | 交付物细节：环境页 `Browser.Trace` 记录的是**配置值**而非 CLI 实际生效值（`--trace-healing` 覆盖时报告与实况相反）；`reports/fix-proposals.md` 只追加数据行、**从不写表头与分隔行**（**实证**：本轮生成的文件首行即 `\| 时间 \| 原定位器 \| …` 数据行，Markdown 不渲染为表格），且 `strategy` 字段未转义 | `write_environment(..., trace_enabled=None)` 由 `pytest_sessionfinish` 传实际值；首次写入补表头；整行统一 `html_escape` |

---

## 四、Low / 建议

- **L1** `engine/healing_locator.py:191-200`：`__getattr__` 在 `_locator/_enabled` 未初始化时（`copy/deepcopy/pickle`）
  会无限递归成 `RecursionError`；建议开头用 `object.__getattribute__` 取值，缺失即 `raise AttributeError(name)`。
- **L2** `engine/healing_locator.py:396-464`：自愈只覆盖 `HealingPage.locator()` 与 HEALABLE 动作；
  `page.get_by_role/get_by_text`、`page.click(sel)`、`expect()` 均不自愈——建议在文档明确边界
  （README/CLAUDE.md 的"POM 无缝切换"表述容易被读成"全部 API 都自愈"）。
- **L3** `collect/collector.py:69-80`：`page.on("request"/"response")` 只挂不摘，同一 page 重复构造采集器会重复计数；
  建议提供 `detach()/close()` 或在挂载前去重。
- **L4** `collect/collector.py:140`：内联 trace 依赖"未录制时 `tracing.stop()` 必抛错"探测录制状态，
  该假设依赖 Playwright 版本；建议改为显式状态位而不是异常探测。
- **L5** `reporting/dashboard.py:47-56`：成本卡片 `.get(key, 0.0)` 只防缺键不防 `None`，
  外部 JSON 给 `null` 时整张看板渲染失败；改 `float(cost.get("total_cost") or 0.0)`。
- **L6** `llm/embedding.py:52-66`：空文本 `embed("")` 产出全零向量（余弦未定义，`nan`），
  `to_vector` 不校验维度；建议零向量快速返回"不可比较"而非进入 `find_semantic`。
- **L7** `llm/io.py:19`、`:33-35`、`:52-66`：`extract_json` 贪婪 `{.*}` 兜底在多 JSON 对象时静默丢弃候选
  （单对象/fence/嵌套均正确，触发概率低）；`safe_float` 接受 `"nan"/"inf"`（建议 `math.isfinite` 过滤）。
- **L8** `llm/openai_client.py:62`、`llm/openai_vision.py:51`：声明 `**kwargs` 却完全忽略，
  而 `base.py:22-23` 与 `fix_generator.py:37-45` 都在转发 → 静默丢弃；建议显式拒绝或透传。
- **L9** `llm/openai_client.py:73-74`：硬编码 `max_tokens` + `temperature=0.0`，
  切到要求 `max_completion_tokens` / `temperature=1` 的新模型族会 400，与"切 provider 只改 yaml"冲突（待验证）。
- **L10** `config.py:50`、`:202`：`viewport: dict[str, int]`（`{"wdith":1280}` 可通过）、
  `embedding.method: str`（写成 `fastembed` 通过校验但 `get_embedding_for_settings` 返回 None → 语义检索静默失效）；
  建议改 `Literal[...]`/`TypedDict` + 未知值 warning。
- **L11** `config.py:52`、`:171`：`reports/traces`、`.cache/knowledge.db` 等相对路径基于 **CWD** 而非项目根，
  不同目录运行会分叉知识库；建议在 `load_settings` 内以项目根 `resolve`。
- **L12** `config.py:117-118`：`strategy_thresholds/strategy_early_accept` 的键名不做校验（文档称"宽待"），
  与 `fix_generator.py:183` 对 `strategy_order` 未知名 warning 的处理相反；建议同样 warning。
- **L13** `notify.py:166-168`、`scripts/propose_pr.py:140-142`：失败仅 `print` 且 `return 0`（设计上不炸流水线），
  webhook 密钥过期/token 权限不足等长期故障只能翻日志；建议输出 `::warning::` 或写 `$GITHUB_STEP_SUMMARY`。
- **L14** `scripts/ab_compare.py:195-214`：只校验 B 组非空，A 组为空时静默产出 `reports/ab-compare.md` 并 `return 0`
  → 对外"自愈价值"证据可能是一份作废表格；建议加 A 组守卫 + 报告中"未运行"告警行。
- **L15** `docs/TODO.md:302`（用例计数，实测漂移：写"14 项"，实际 `def test_` 17 个）与 `README.md:139/183`：
  README 的 `pytest -m e2e` 会带上 erp 用例，与 CI 的 `-m "e2e and not erp"` 不一致
  → 建议 README 补 `pytest -m "e2e and not erp"`，计数改为"见 `pytest --collect-only -q`"。
- **L16** `pyproject.toml:45` + `tests/conftest.py:31-39`：`healing` marker 已注册且实现"自愈 > e2e > unit"
  标签优先级，但**全仓库无用例使用** → README 宣称的 Allure"AI 自愈"分组永不出现；
  建议给 5 个自愈闭环 e2e 补 marker，或删除 marker 并同步文档。
- **L17** `.github/workflows/ci.yml:16`、`:233`：无 `timeout-minutes`（卡死最长 6 小时）、
  无 `concurrency`（两次 main push 并发推 gh-pages）、`allure-report-action@master`（分支引用，供应链风险）；
  建议加超时/并发组，action 固定 tag 或 SHA。
- **L18** `tests/conftest.py:73-75`：session 级 `settings` fixture 读取本机 gitignored `config/settings.yaml`
  → e2e 结果依赖开发机状态（`dry_run: true` 会让 e2e 集体失败）；建议 e2e 层加"测试基线开关"断言。
- **L19** `tests/e2e/test_llm_smoke.py`、`test_visual_smoke.py`：真实模型调用与普通 e2e 混在一起
  （`.env` 在 `config.py:19-22` import 期自动加载），本机跑常规 e2e 就会外呼付费模型；
  建议加 `live` marker，默认 `-m "e2e and not live"`。
- **L20** `reporting/fix_proposals.py:98-100`、`reporting/allure_bridge.py:114-123`：`attach_file(type_name: str)`
  用字符串 `getattr` 取附件类型（拼错静默 False）；建议改枚举/常量。

---

## 五、亮点（值得在面试中讲）

1. **防幻觉护栏是真的**（`agent/strategies/semantic.py:175-199`、`visual.py:39-63`）：
   模型只能从"页面真实候选稳定定位器"集合中挑，编造的 selector 与越界置信度一律拒绝——
   这是"让 LLM 参与自动化但不给它越权能力"的标准做法。
2. **策略短路 + 置信度归一化**（`fix_generator.py:172-203`、`agent/confidence.py`）：
   按 `strategy_order` 逐个尝试，达该策略"早接受"阈值即短路，省下更贵策略的调用；
   可插拔 `CALIBRATORS` 注册表把不可比的置信度统一到一把尺子，默认恒等保证零回归。
3. **B1 两阶段沉淀**（`persistence.py:83-103` + `healing_locator.py:289-303`）：
   修复先暂存、**引擎层重试成功后**才落库——避免把"没生效的修复"写进知识库造成后续误复用，
   配合 `attempt_id` 幂等提交，是"证据驱动沉淀"的好例子。
4. **T16 真自愈 vs flaky 的判定**（`persistence.py:158-176`）：
   用"修复后原定位器是否仍失效"区分真修复与偶发绿（口径问题见 H6，但设计思路有洞察）。
5. **T13/T14/T15 风险控制三件套**：高风险页豁免（`context.py:113-120`）、dry-run 只出建议、
   修复建议写回人审清单（`fix_proposals.py`）——把"AI 改代码"关进人工审核的笼子里。
6. **知识库工程**：L1 `repair_key` 精确 → legacy 指纹 → L3 向量检索的三级优先级
   （`knowledge/*`），schema 迁移"补列 + 清重 + 重建唯一索引"可重复执行且顺序有注释说明；
   `is_verified` 刻意不进 `DO UPDATE` 列以免静默清除人工审核状态（`sqlite_store.py:155-156`）。
7. **测试资产扎实**：312 个 unit 用例几乎都是带精确数值的行为断言（如融合置信度 `0.854`、raw² 收缩边界），
   `test_layering.py` 用源码扫描固化分层约束，`test_xdist_compat.py` 用分片协议验证并行聚合。

---

## 六、规则合规检查

| 规则 | 结论 |
|---|---|
| R1 先计划后写入 | ✅ 本轮为只读审查；仅新增本报告（经人工确认后写入） |
| R2 测试覆盖 | ⚠️ 整体覆盖优秀（312 unit），但 `llm/openai_vision.py` 请求/解析/降级全路径无单测（M15）；链式自愈无 e2e（M15） |
| R3 临时方案 | ✅ 未发现新的未标记 workaround（`_is_timeout_error` 的类名兜底、`text=` 选择器语义均已在注释中标注风险） |
| R4 代码质量与文档 | ⚠️ docstring 密度高、异常语义清楚；但多处长 `except Exception` 无日志（M8）、`store.py:101` 注释与实现相反（M4）、`TODO.md` 计数漂移（L15） |
| R5 变更沉淀 | ✅ 本报告落 `docs/reviews/2026-10-08-code-review.md`，含五要素（见第八章） |
| R6 先收敛计划 | ✅ 审查为只读任务，边界清晰，无需计划模式 |
| R7 文档双轨 | ✅ `docs/reviews/` 属沉淀/历史记录，R7.4 明示不参与双轨；本轮未改技术文档，无需镜像同步 |
| R8 敏感文件 | ✅ 未修改任何密钥/用户数据文件；探针脚本只在进程内使用密钥并全程掩码，未写入仓库；未进行任何 git 推送 |

---

## 七、耗时测试实测结果（全量 `pytest`）

- 命令：`python -m pytest -q`（unit + e2e + erp 全量）
- 结果：**1 failed / 334 passed / 3 xfailed / 2 errors，耗时 438.39s（7 分 18 秒）**
- 快速门禁（同轮实测）：`pytest -m unit` → **312 passed, 28 deselected（8.49s）**；
  `ruff check .` → **0 errors**；`ruff format --check .` → **2 文件待重排**（README.md、tests/conftest.py，见 M14）。

### 7.1 唯一实质失败：**key 与端点平台不匹配** → LLM 路径静默降级（已修复并复验通过）

- 失败用例：`tests/e2e/test_llm_smoke.py::test_semantic_llm_locate_login_button`
  （`assert cand is not None`；3 次重试全部返回 `None`；复跑仍在 5.91s 内稳定失败）
- **根因实证**（探针脚本 `get_llm_for_settings(load_settings()).chat(...)`）：
```
chat raised: UnavailableError 模型调用失败: AuthenticationError
cause: AuthenticationError | Error code: 401 - {'error': {'message': 'Authentication Fails,
       Your api key: ****23do is invalid (request_id: ...)'}}
```
- **进一步定位（多端点探测）**：该 key 对 `api.deepseek.com` / `api.openai.com` 均 401，但对
  `https://api.commandcode.ai/provider/v1` 返回 **200** —— 即 `.env` 里放的是 **CommandCode 平台**的 key，
  而代码默认 `base_url=https://api.deepseek.com` + `model=deepseek-v4-flash`。**不是代码缺陷，而是配置错配**。
- **修复**：`config/settings.yaml`（gitignore 的本机配置）新增 `llm:` 段指向 CommandCode 网关与对应模型名；
  复验 `pytest tests/e2e/test_llm_smoke.py -q` → **2 passed（38.75s）**。
- **影响面（当时）**：LLM 相关三条能力全部静默失效——`diagnose_llm` 精判退规则式、`semantic` L4 语义定位不可用、
  策略链失败时的 LLM 归因缺失；而因为 `factory.py` 与 `semantic.py` 把异常吞成 `None`（无日志、无报告字段），
  失败现场只剩一句 `assert None is not None`——**H4/M1/M8 的可诊断性缺口由此获得直接实证**（修复后同类故障会打印
  "模型调用失败: AuthenticationError provider='openai' model='…' endpoint='api.deepseek.com'（配置或密钥类故障…）"）。
- VLM 侧（`DASHSCOPE_API_KEY`）当时即正常：全量跑 **0 skipped**，且 `reports/evidence/visual_result.json`
  于本轮 `18:40` 写入（`strategy=visual, confidence=0.921`）→ 视觉链路当日实证可用，**只有 LLM 站点的配置错配**。

### 7.2 两个 ERROR：ERP 外部依赖缺失，但 fixture 选择 fail 而非 skip（M12 实证）

- 失败用例：`tests/e2e/test_erp_smoke.py::test_erp_login_smoke`、
  `tests/e2e/test_erp_healing.py::test_erp_material_add_with_healing`
- 报错：
```
tests.e2e.api.erp_client.ErpApiError: POST /user/login 请求失败：ERP 未启动或网络不可达
    [WinError 10061] 由于目标计算机积极拒绝，无法连接。
playwright._impl._errors.Error: Page.goto: net::ERR_CONNECTION_REFUSED at http://localhost:3001/user/login
```
- 结论：ERP 后端（`192.168.1.3:9999`）与前端（`localhost:3001`）均未启动。按 R2/测试纪律，
  "外部依赖缺失"应 skip；当前实现（`tests/conftest.py:184-191`、`:216-224`）在**凭证已配置**
  的情况下直接 ERROR，使 `pytest -m e2e` / 全量跑必然红——与本地 README 推荐命令冲突（M12）。

### 7.3 其余

- `3 xfailed`：`tests/e2e/test_ab_scenarios.py` 的 A 组（关闭自愈）三个用例按设计预期失败
  （T20 A/B 对照），非缺陷。
- 回归结论：**除上述环境类失败外，334 项全绿**，无源码回归；本章数据取自**审查阶段**（当时未改任何源码），
  H/M/L 各条的修复与复验见 `2026-10-08-fix-report.md`。

---

## 八、五要素速览（R5）

- **当前目标**：对 AutoAiSelfHeal 全量代码做一次独立、可复核的审查，产出分级问题清单，并以耗时测试验证现状。
- **关键约束**：只读审查（不动源码）；不推送远端仓库；沉淀文档与结论一并交付；ERP/真实模型等外部依赖可能缺失。
- **已达成结论**：无阻断缺陷；6 项 High（H1 弹窗误点风险、H2 知识库所有权漏洞、H3 LLM 超时不可配、
  H4 模型不可用静默降级、H5 VLM 无图片护栏、H6 flaky 口径错误）；16 项 Medium、20 项 Low；
  静态门禁与 unit 全绿，format 门禁与 marker 门禁存在漏洞；**环境事实：本机 `OPENAI_API_KEY` 已失效（401），
  LLM 侧三条能力当前静默降级**（需人工换 key），ERP 未启动使两个用例 ERROR（应为 skip）。
- **待解决问题**：H1–H6 修复排期；M12（ERP fixture 应 skip）与 M13/M14（CI 门禁）建议与 H 系列同批；
  M3（人审标记入口）需要产品决策——是否提供 CLI 作为"人审 → 自动采纳"的正式入口；
  本机 LLM key 失效需人工更换后复跑 `tests/e2e/test_llm_smoke.py` 复验。
- **下一步计划**：① 先修 H1/H2（安全与资源语义，改动小）；② 再修 H3/H4/H5（可观测性与护栏，约几十行）；
  ③ 随批补 M14 门禁（防回归）与 M15 测试缺口；④ 修复后用 `pytest -m unit && ruff format --check .` 验收；
  ⑤ 需要的话把待修复项按惯例登记 `docs/TODO.md` 并同步 `docs/roadmap.md`。
