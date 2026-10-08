# 修复报告 — 2026-10-08 审查 High / Medium

> **范围**：`docs/reviews/2026-10-08-code-review.md` 的 **H1–H6 + M1–M16**。
> **主动延期 2 项**：M10（分层重构，影响面横跨 8+ 文件与文档双轨）、M15 第 3 条（链式自愈 e2e，需先给演示页加 DOM 夹具）——见 [`docs/backlog/low-priority.md`](../backlog/low-priority.md)。
> **Low 20 项**：全部未开工，登记在 [`docs/backlog/low-priority.md`](../backlog/low-priority.md)。
> **验证**：`pytest -m unit` **404 passed**（修前 312，+92）· `ruff check .` **0 errors** · `ruff format --check .` **128 文件全绿** · 全量 `pytest`（unit+e2e）见第五章。
> 未提交、未推送（工作区改动待人工 review）。

---

## 一、修复总览

| 编号 | 问题（一句话） | 修法（一句话） | 主要文件 |
|---|---|---|---|
| H1 | 弹窗知识路径全页 `.first` 点击 + 关键词子串误判 → 可能点到业务按钮 | 关闭点击限定在弹窗容器内；候选改"强信号优先、弱信号兜底"分级 | `engine/popup_guard.py` |
| H2 | `HealingPage` 自建知识库无人关闭（与书面契约不符） | 加 `_owns_knowledge` 所有权标记，`close()` 负责释放 | `engine/healing_locator.py` |
| H3 | LLM 超时 15s / max_tokens 2000 硬编码，且 `extra='forbid'` 让用户无法配置 | 三个参数进 `LLMConfig`（带值域），工厂透传；VLM 侧补值域 | `config.py`、`llm/openai_client.py`、`llm/factory.py` |
| H4 | 五类"模型不可用"统一静默返回 `None`、零日志 | 每个分支记**可区分原因**的 warning；provider 未注册改为查注册表（不再用 KeyError 做控制流） | `llm/factory.py`、`llm/registry.py` |
| H5 | VLM 无图片体积护栏，`full_page` 截图原样 base64 → 视觉策略在长页面"永不生效" | 新增 `max_image_bytes` + Pillow 降质/缩放转 JPEG（无 Pillow 记 warning 原图发送） | `llm/openai_vision.py`、`config.py` |
| H6 | `flaky` 把"自愈失败"计入"侥幸通过"，`verified_rate` 可 >1 | verified/flaky 与 success 取交集，比率 clamp | `reporting/metrics.py`、`dashboard.py` |
| M1 | 401 与 429 同级、重试次数隐式继承 SDK 默认（一次失败最多 3 次付费调用） | 异常分级（Fatal / Transient / 基类）+ 错误信息带 provider/model/端点主机 + 显式 `max_retries` | `llm/_exceptions.py`、两个 client |
| M2 | 开/关自愈时异常类型不同，`except TimeoutError` 行为不一致 | `HealingFailedError` 同时继承 Playwright 与内置 `TimeoutError` | `engine/healing_locator.py` |
| M3 | `set_verified()` 有实现无入口 → L3"人审后自动采纳"闭环缺一环 | 新增人审入口脚本 `scripts/verify_case.py`（`--list` / `--repair-key` / `--selector` / `--unverify`） | `scripts/verify_case.py` |
| M4 | memory / sqlite 双后端语义漂移；Protocol 缺 `count_repairs`/`close` | 统一 upsert / 择优 / 时间格式；Protocol 补齐成员 | `knowledge/*` |
| M5 | 每次打开都全表 DELETE 去重；迁移 check-then-act 竞态 | 先探测再执行；ALTER 容忍 `duplicate column`；降级 memory 记 error | `knowledge/sqlite_store.py`、`factory.py` |
| M6 | `timeout_ms=0` 变成"不超时"；稳定窗口被可见性预算吃掉（伪失败）；忙等 | 参数值域校验 + 可见性预算让位 `stable_ms` + 轮询下限 20ms | `engine/smart_wait.py`、`config.py` |
| M7 | `launch()` 失败泄漏 Playwright 驱动进程；`__exit__` 不幂等；`assert` 校验 | `try/except` 失败即清理；引用置空幂等；explicit `RuntimeError`；统一关闭 context | `engine/browser.py` |
| M8 | 多处长 `except Exception` 静默无日志（违 R4） | 逐处补 warning/debug（含 `allure_bridge`、`collector`、`popup_guard`、`healing_locator`） | 5 个文件 |
| M9 | 暂存淘汰无日志；`_committed` 无界；二次自愈后首次暂存永久悬挂 | 淘汰/丢弃记 warning；幂等窗口有界；新增 `discard_pending()` 并在二次自愈前调用 | `agent/persistence.py`、`orchestrator.py`、`healing_locator.py` |
| M11 | 选择器字面量未转义（值含 `"` 生成非法选择器），换行导致失配 | 统一 `_escape()`：引号转义、换行折空格 | `agent/dom/selector_builder.py` |
| M12 | ERP"凭证有、服务无"时 ERROR 而非 skip；`erp_page` 不释放资源 | 连接/登录失败 → `pytest.skip` 并说明原因；补 `finally: page.close()` | `tests/conftest.py` |
| M13 | CI 装了 chromium 却因默认 `channel=chrome` 从未使用 | CI 生成的 settings.yaml 显式 `browser.channel=chromium` | `.github/workflows/ci.yml` |
| M14 | 无 `--strict-markers`（漏标 marker 两边都不跑）；无 `ruff format` 门禁 | 加 strict-markers + "未分类测试"门禁 + format check | `pyproject.toml`、`ci.yml` |
| M15 | VLM 全路径零单测；工厂测试断言恒真；链式自愈无 e2e | 新增 VLM 单测 10 条、去掉恒真断言并补透传断言；**链式 e2e 延期** | `tests/unit/*` |
| M16 | 环境页写配置值而非实际 trace 开关；`fix-proposals.md` 无表头 | `write_environment(..., trace_enabled=)` 由会话钩子传实际值；首次写入补表头 | `reporting/*`、`tests/conftest.py` |

---

## 二、分组说明：问题 · 影响 · 修法 · 设计思路

### 组 1｜安全与正确性边缘（H1、M11）

- **问题**：弹窗知识命中后用 `page.locator(sel).first` 全页点击；关键词用子串匹配（"关闭"命中"关闭订单"）；选择器字面量未转义。
- **影响**：自动化可能点到弹窗之外的业务控件（数据风险）；带引号的属性生成非法选择器直接报错。
- **修法**：点击限定在弹窗容器内；候选打分（整串命中=强，子串=弱，仅在无强候选时兜底）；字面量统一转义。
- **设计思路 / 备选**：① 备选是"直接删掉关键词启发式"——否决，弹窗自动化正是卖点；② 备选是"关键词改精确集合匹配"——也否决，会把 `aria-label="close dialog"` 这类真实标签漏掉；最终取"分级"折中：既保留覆盖面，又把不可靠命中降为兜底。
- **实测校正**：顺手用真浏览器验证了 Playwright 的 `text="关闭"` 是**精确匹配**（`text=关闭` 才是子串），故审查报告里"沉淀选择器会通配到关闭订单"的表述已更正（风险实际来自"未限定作用域"与"关键词子串"）。

### 组 2｜资源所有权与生命周期（H2、M7、M9）

- **问题**：`HealingPage` 自建的知识库没人关；`BrowserManager.__enter__` 里 `launch()` 抛错会让驱动进程泄漏；暂存淘汰/悬挂无痕。
- **影响**：非 fixture 用法每个页面泄漏一个 SQLite 连接；CI 未装浏览器时反复失败会累积 node 子进程；"这次修复没沉淀"完全不可见。
- **修法**：显式所有权标记 + 幂等清理 + 丢弃/淘汰记 warning + 二次自愈前 `discard_pending`。
- **设计思路 / 备选**：所有权沿用项目既有 `owns_*` 约定（不引入新机制）；没有选择"让 orchestrator 无条件关闭传入的 knowledge"——那会破坏"注入方负责关闭"的契约，也会让 conftest 的会话级共享库被提前关掉。

### 组 3｜模型层可配置性与护栏（H3、H5、M1）

- **问题**：LLM 超时/token 硬编码且被 `extra='forbid'` 堵死配置；VLM 无图片体积上限；异常不分级、重试隐式。
- **影响**：长回复超时 → 自愈静默失效（VLM 侧踩过同样的坑）；长页截图超服务端上限 → 视觉策略永不生效；401 与 429 被同样对待，一次失败最多 3 次付费调用且不可见。
- **修法**：参数进配置并在工厂透传；`max_image_bytes` + Pillow 压缩（无 Pillow 时 warning + 原图）；异常按 `status_code`/类名分级，错误信息带 provider/model/端点主机。
- **设计思路 / 备选**：① 分类**不 import openai**（SDK 是可选依赖，决策 D7），按 `status_code` + 类名归类，保持"纯逻辑环境可单测"；② 新异常全部继承 `UnavailableError`，既有 `except UnavailableError` 的降级契约零回归——这是"加能力不改语义"的关键取舍；③ 图片压缩选 Pillow **可选依赖**（进 `llm` extra），不把 Pillow 变成硬依赖，缺失时降级可诊断。

### 组 4｜可观测性（H4、M8、M16）

- **问题**：五类模型不可用原因收敛成同一个 `None`；多处 `except Exception` 无日志；环境页记录的是配置值而非实际生效值。
- **影响**：本轮实测就是活证据——key 与端点不匹配（401）时，唯一症状是 `assert None is not None`，日志里查不到任何原因（详见第八章）。
- **修法**：每个降级点带原因 warning（只打印变量**名**，绝不打印值）；静默分支补日志；环境页接受实际生效值。
- **设计思路 / 备选**：没有引入结构化诊断对象（如 `llm_unavailable_reason` 字段进报告），因为那会改动报告 schema 与多处消费方；先用日志把"可诊断"补齐，结构化留给后续（已在 Low 清单 L2/L15 一带留痕）。

### 组 5｜口径、契约与知识库（H6、M2、M4、M5、M3）

- **问题**：`flaky` 口径错；异常类型随自愈开关变化；双后端语义漂移；迁移/去重的性能与竞态；人审标记无入口。
- **影响**：看板对外数字失真（成果展示直接受影响）；POM 的 `except TimeoutError` 时灵时不灵；内存/ SQLite 跑出不同结论；知识库在多进程首升级时静默失去持久化；L3 防污染规则形同虚设。
- **修法**：口径与 success 取交集；异常双继承；后端对齐 upsert/排序/时间格式并补齐 Protocol；迁移先探测；新增 `scripts/verify_case.py` 人审入口。
- **设计思路 / 备选**：M2 选了"双继承"而不是"文档声明异常类型会变"——`except TimeoutError` 是 POM 里的常见写法，兼容成本只有一行基类元组。M3 选"独立脚本"而不是"在 orchestrator 里自动标记"——自动标记会绕过人工审核，违背 T15 的人审边界。

### 组 6｜门禁与测试资产（M6、M12、M13、M14、M15）

- **问题**：ERP 外部依赖缺失让全量跑必红；CI 的浏览器内核装了不用；marker 漏写会同时躲过两个 job；VLM 全路径无单测。
- **影响**：本地 `pytest -m e2e` 长期红 → 团队会习惯性忽略红灯（最危险的信号衰减）。
- **修法**：外部依赖缺失一律 skip 并说明；CI 固定 `channel=chromium`；加 strict-markers + 未分类测试门禁 + format check；补 VLM 单测。
- **设计思路 / 备选（重要取舍）**：审查建议"去掉 E501 的 ignore"，实测启用后全仓 52 处告警、绝大多数是**中文注释与长字符串**（`ruff format` 无法换行的那类），修它只产生噪音。故本轮**保留 E501 ignore**，把"换行约束"交给新增的 `ruff format --check` 兜住——用真正有效的门禁替代冗余告警，是对审查建议的**有意偏离**。同时给 ruff 加 `extend-exclude = ["*.md"]`：实测格式化 Markdown 里的示意代码会把 `timeout_s: float = 15.0` 改成 `(15.0,)`，把文档改坏。

---

## 三、反思与归因

1. **"优雅降级"被用成了"静默降级"，是这批缺陷的共同母体**。原设计意图（无 key 也要能跑）正确，但落地时把"降级"与"不可见"绑定了：工厂不记日志、策略吞异常、异常不分级。教训：**降级必须留痕**——降级是产品决策，静默是实现偷懒。已通过 H4/M8/M1 系统性补齐。
2. **同类问题重复出现，说明缺"对称检查"**。VLM 侧早就因为 20s/500 踩坑而把超时做成了可配置，LLM 侧却仍是硬编码——**修了一个 provider 就该横向扫另一个**。教训：涉及多 provider/多策略的改动，落地时强制做"对称性核对"（本轮起在审查清单里固定列出）。
3. **"能配置"与"配置项被校验拒绝"是两件事**。`extra='forbid'` 的价值（防死配置）与代价（用户无法覆盖硬编码值）此前没有一起评估，导致 `llm.timeout_s` 写了就被拒。教训：加 forbid 的同批要为每个运行期常量提供配置入口，否则等于把常量冻死。
4. **测试通过 ≠ 功能可用**：真实 LLM 冒烟之所以失败，根因不在代码而在**密钥与端点平台不匹配**（CommandCode 的 key 指向了 `api.deepseek.com`）。而框架当时无法把这句"人话"输出出来 —— 这条正好证明了 H4 的必要性。已在报告与修复中记录处置：`config/settings.yaml` 显式指向 CommandCode 网关。
5. **门禁的洞比缺陷更贵**：ERP 用例该 skip 却 ERROR、marker 漏写两边都不跑、format 从不检查——这三条都属于"缺陷没被发现"的原因，而不是缺陷本身。已优先补门禁，避免下一批缺陷静默溜过。
6. **修好一个能力会掀开下一层问题（本轮实证）**：LLM 端点修好后，全量跑出现一个新失败——`test_fallback_when_unhealable` 点到了 `#ghost-btn`。排查结论：**不是回归**（单文件跑 3 passed、无模型调用的探针也走 fallback、e2e 复跑 23 passed），而是**语义 LLM 段没有视觉段那样的 L2 交叉校验**，模型对"与页面完全无关的描述"仍会给出高置信候选并被采纳 → "故意不可自愈"的场景变成模型心情驱动。处置：① 用例改用新的 `offline_healing_page` 夹具（关 LLM/VLM/embedding + 断言模型调用为 0），把"确定性不可自愈"写成显式前提；② 产品侧护栏单独立项（Low 清单 R5），因为照搬视觉段的融合公式会误伤合法场景（实测会让真实 LLM 冒烟的候选跌破阈值）。教训：**"降级路径"和"不可自愈路径"的测试必须自带确定性前提**，否则真实模型一上线，红灯的含义就模糊了。

---

## 四、遗留与下一步

1. **延期**：M10（engine/collect 反向依赖）与 M15 第 3 条（链式自愈 e2e）——见 Low 清单第二章的启动方式。
2. **Low 20 项 + 修复轮新发现的 5 条残余项**（含 R5：语义 LLM 段缺 L2 交叉校验，有实测证据）：全部未开工，登记在 [`docs/backlog/low-priority.md`](../backlog/low-priority.md)。
3. **需人工介入**：`reports/fix-proposals.md` 既有历史产物按"仅首次/空文件补表头"设计不会追溯补表头，如需整洁请删除后重跑（该文件在 gitignore 的 `reports/` 下，不影响仓库）。
4. **环境遗留**：ERP 后端（`192.168.1.3:9999`）与前端未启动，本机跑 ERP 用例仍是 skip（符合预期）；`OPENAI_API_KEY` 沿用 CommandCode 的 key，环境变量名与 provider 不匹配属命名债（Low 清单未列，建议下次一并改名或加注释说明）。
5. **验收命令**：`python -m pytest -m unit -q` → 404 passed；`python -m ruff check . && python -m ruff format --check .` → 全绿；全量 `python -m pytest -q` 见第五章。

---

## 五、收尾实测（全量 `pytest`，含 e2e）

| 轮次 | 命令与结果 |
|---|---|
| 修复前（审查阶段） | `python -m pytest -q` → **1 failed / 334 passed / 3 xfailed / 2 errors（438.39s）** |
| **修复后（收尾）** | `python -m pytest -q` → **427 passed / 2 skipped / 3 xfailed / 0 failed（412.76s，exit 0）** |
| 单元门禁 | `python -m pytest -m unit -q` → **404 passed / 28 deselected（8.5s）**（修前 312） |
| 静态门禁 | `python -m ruff check .` → **0 errors**；`python -m ruff format --check .` → **128 文件全绿**（此前 CI 没有这道门禁） |

三处关键变化（都能对应到具体修复项）：
1. **2 errors → 2 skipped**：ERP 后端/前端未启动时 fixture 改为 `pytest.skip` 并打印原因（M12）——外部依赖缺失不再把 `pytest -m e2e` 判红。
2. **真实 LLM 冒烟由失败转为通过**：`config/settings.yaml` 把 `base_url/model` 指向 key 所属平台（CommandCode 网关），`test_llm_smoke.py` **2 passed**；同时 401 现在会被归为 `FatalUnavailableError` 并打印 provider/model/端点主机（M1），同类配置错配下次可直接从日志定位。
3. **无遗留失败**：新增/修改用例（单元 +92、e2e 若干）与既有 427 项全绿，`3 xfailed` 是 T20 A/B 对照的设计预期。

## 六、测试资产增量（R2 对照）

| 主题 | 新增/修改用例 | 关键断言（防回归点） |
|---|---|---|
| 弹窗安全（H1/M11） | `test_review_fixes_1008.py` 4 条 + 既有 `_is_close_hint` 兼容用例 | 关闭点击只发生在容器内；强信号优先于弱信号；引号/换行被转义 |
| 资源所有权（H2/M7/M9） | 同上 6 条 + `test_wrapped_healing.py` 2 条 | 自建知识库必被关闭、注入的不动；launch 失败回收驱动；重复退出幂等；二次自愈丢弃首次暂存 |
| 模型层（H3/H4/H5/M1/M15） | `test_openai_vision.py` 10 条 + `test_openai_client.py` +4 + `test_llm_factory.py` 改 1 增 5 | 参数由配置透传；401→Fatal、429→Transient；超限图片压缩到上限内；不可用原因可区分 |
| 配置值域（H3/M6） | `test_review_fixes_1008.py` 9 条（含参数化） | 非法 timeout/token/图片上限在加载期被拒 |
| 知识库（M3/M4/M5） | `test_knowledge_backend_parity.py` 16 条 + `test_knowledge_sqlite.py` +5/改 1 + `test_verify_case.py` 6 条 | 双后端逐条一致；新库不发 DELETE；重复列竞态被忽略；人审脚本可标记/撤销 |
| 展示与指标（H6/M8/M16） | `test_reporting_hardening.py` 14 条 + `test_risk_metrics.py`/`test_dashboard.py` 改 1 增 2 | 失败不计 flaky、比率 ≤1；失败分支有日志；表头只写一次 |
