# Code Review — T5–T23 演进审查（2026-09-03）

> **修复状态：✅ 已全部修复（2026-09-03 同日）**——C1（example 移位）/ H1（删残片）/ M1（默认值
> 外移 + 本地 settings.yaml 承载专属端点）/ M2（erp_page 登记聚合）/ m2–m7+nit 批；m1（llm/io.py
> 分层倒置）亦于同日子代理复核轮修复（`build_compact_dom` 上移 `agent/dom/compact.py`）。
> 验收：ruff 0 errors、unit 312 passed（含复核加固 +19 回归）、example 与 load_settings 双路径实证。
> 独立复核：5 个子代理（opencode-custom/glm-5.3-flash）确认 10 项结论全部成立，fresh-eyes 新增
> 12 条发现（0 high / 4 medium / 8 low）已全部修复——清单见 `docs/TODO.md` 复核加固节。

> 审查范围：自上轮审查（`2026-08-15-code-review.md`，提交 2d65417）以来的全部演进——
> 24 个提交、82 文件（+4,501/−289）：T5–T12 收官、T18–T23 新特性（Allure 桥 / 通知 / A/B 对比 /
> xdist 兼容 / 草稿 PR / 管伊佳 ERP 被测系统迁移）、R7/R8 规则引入。
> 方法：逐文件精读新增/修改源码（src 28 文件 + scripts 3 文件 + conftest + CI + 示例配置 + ERP 基建），
> 关键缺陷运行脚本**实证复现**。
> 基线：`pytest -m unit` **293 passed** 全绿；`ruff check .` **2 errors**（详见 H1，CI 门禁会红）。
> 关联：修复项已登记 `docs/TODO.md`「2026-09-03 Code Review 待修复项」。

---

## 一、总体评价

本轮演进质量整体**高于上轮**：T5 置信度归一化（可插拔注册表 + 默认恒等零回归 + 值域钳制）、
T21 xdist 分片聚合协议（worker 分片 → controller 聚合 → 清理，零回归设计）、T8 双来源交叉校验
（best-effort 降级 + R4 记 warning + 惰性导入防环）、T23 ERP 基建（角色语义专家确认入档、凭证参数化
符合 R8 精神、测试数据 finally 清理）都是可直接写进面试的设计。R3 临时方案标记（`agent/llm_io.py` 壳）
也首次按规范落地。

但存在 **1 个实证 Critical**（配置模板加载即崩）与 **1 个实证 High**（CI 门禁红），另有
2 项 Major（环境端点入 git、ERP 审计聚合缺口）。

---

## 二、Critical（实证缺陷）

### C1 · `config/settings.example.yaml`：`action_wait` 顶层段导致配置加载崩溃

L58-61 把 T6 的 `action_wait` 写成了**顶层段**，而 `config.py` 中它是 `HealingConfig` 子字段
（`healing.action_wait`，config.py L113）。`Settings` 为 `extra='forbid'`（L220）。

**实证**：`Settings.model_validate(example)` → `ValidationError: action_wait — Extra inputs are not permitted [type=extra_forbidden]`。

- **影响**：用户按注释"复制为 settings.yaml"后，`load_settings()` 直接抛异常——pytest 全部 fixture
  与脚本入口在启动期崩溃。CI 未暴露（e2e job 用 printf 单键生成 settings.yaml，不含该段）。
- **修复**：把 L58-61 四行移入 `healing:` 段内（缩进一级），或删除该段（默认 False 零侵入）并在
  healing 段注释说明。

---

## 三、High（CI 门禁红，实证）

### H1 · `scripts/propose_pr.py`：`build_pr_body` 残缺首定义（编辑遗留死代码）

L51-59 是第一次 `build_pr_body` 定义的**残片**（`lines` 列表初始化后无 return 即中断），
L68-88 是完整第二定义。ruff 报 **F841 + F811 共 2 errors** → CI unit job 的 `ruff check .` 门禁失败。

- 运行时行为暂未受影响（后者覆盖前者），但残片是提交时编辑遗留，且直接打破门禁。
- **修复**：删除 L51-60 残片（保留 L68 完整版）。

---

## 四、Major

| # | 位置 | 问题 |
|---|---|---|
| M1 | `src/selfheal/config.py` L171-173、L207；`config/settings.example.yaml` L27、L77 | **环境特定端点硬编码入 git 默认值**：① `VisionConfig.base_url` 默认 = 专属百炼 MaaS 实例端点（含实例 ID `ws-rgkuic58ghmqbq8a`）；② `SutConfig.api_base_url` 默认 = 内网 IP `http://192.168.1.3:9999/jshERP-boot`。两者同时出现在会提交的 example 模板中——别人 clone 即指向不可用端点，且内网拓扑进 git。建议：默认值改公共端点/占位（DashScope 公共 compatible-mode / `localhost` 占位），专属端点放 gitignore 的 `settings.yaml`。 |
| M2 | `tests/conftest.py` L210（erp_page fixture） | **ERP 自愈记录不进聚合**：`erp_page` 构造 HealingPage 后未执行 `_session_reporters.append(page.reporter)`（对比 `healing_page` L158 有登记）→ ERP 用例的自愈记录缺失于 dashboard / healing-records.json / T19 通知摘要（用例内断言不受影响，纯聚合审计缺口）。一行修复。 |

---

## 五、Minor

- m1 `src/selfheal/llm/io.py` L18：T10 归位后 `llm/` 反向依赖 `selfheal.agent.dom`（llm 是支撑层、
  agent 依赖 llm，现 llm/io 又依赖 agent.dom——分层倒置；无模块级环、功能无碍）。建议 dom 公共工具
  下沉独立包（T10 原计划 utils/ 方向）或 DOM 相关函数留在 agent 侧。
- m2 `reporting/allure_bridge.py` L9/L46 docstring 写优先级"healing > e2e > unit"，实现
  `_FEATURE_PRIORITY`（L36）已是 `erp > healing > e2e > unit`——注释漂移。
- m3 `scripts/notify.py` L52：`Counter(str(r.get("strategy")))` 把 `strategy=None`（如 T13 豁免记录）
  计成 `"None"` 键；`metrics.compute_metrics` 是 `if r.strategy:` 过滤——两处统计口径不一致。
- m4 `src/selfheal/agent/llm_io.py`：兼容壳 docstring 自称"调用方已全部更新为新路径"，却仍保留壳——
  既已无引用应直接删除（R3 回收），避免 workaround 永久化。
- m5 `config/settings.example.yaml` L81-82：尾部注释仍写 execution/reporting"规划中…尚未建模"——
  T9 已决策**不建模**（config.py L216-217 已正确表述），注释过时。
- m6 `collect/collector.py` L145：内联 trace 后恢复录制的 `tracing.start(screenshots=True, snapshots=True)`
  参数与 conftest context fixture 的 start 参数硬编码重复——将来 conftest 调整参数（如加 sources）
  会漂移。
- m7 `agent/dom/parser.py` L193：`interactive_candidates` 用 `if native:` 判断——原生解析成功但结果为空
  列表时回退静态解析，与 docstring"有原生解析结果优先"语义略偏（行为无害，页面无交互元素时静态通常也空）。

---

## 六、Nit

- `scripts/ab_compare.py` L163：B 组"需人工修复" = total − passed，把 skipped 也计入（口径略粗）。
- `reporting/allure_bridge.py` L114-123：`attach_file(type_name: str)` 用字符串 getattr 取
  attachment_type（弱类型 API，拼错静默 False）。
- `tests/e2e/api/erp_client.py` L112：`_search_param` pageSize 固定 20——同名数据超 20 条时
  `find_material_id` 查不到（用例用 uuid 唯一名规避，风险低）。
- `collect/collector.py` L51：`Scene.native_elements: list | None` 裸类型注解（collect 已顶层 import dom，
  可精确为 `list[Element]`）。
- T22 CI：force push 更新 `selfheal/proposals` 分支，但已存在 open PR 的 body 不随之更新
  （body 是创建时快照，diff 会更新）——语义可接受，注释说明即可。

---

## 七、亮点（值得在面试中讲）

1. **T5 置信度归一化**（`agent/confidence.py`）：可插拔 `CALIBRATORS` 注册表、默认恒等保证零回归、
   `shrink_self_reported` 显式开关 + raw² 收缩、出口值域钳制（L74-75）、标尺契约表文档化——
   "把不可比的数统一到一把尺子"的教科书式落地。
2. **T21 xdist 分片聚合**（conftest L224-303）：worker 分片 → controller 聚合 → 清理，无 xdist 时
   单进程路径零回归；配套 SQLite `timeout=30 + busy_timeout + WAL`。
3. **T8 双来源交叉校验**（parser.py + collector.py）：原生解析失败降级静态、两来源口径漂移记 warning
   不静默（R4）、惰性导入防环有注释说明。
4. **T11 内联 trace**（collector.py L119-148）：stop/restart 拆片设计、未录制时的"安全探测"有实测
   注释、恢复失败不丢已保存现场 trace。
5. **T23 ERP 基建**：租户/admin 角色语义专家确认入档（config.py docstring）、凭证全参数化
   （R8 精神）、`test_erp_healing` 改版注入 → 自愈 → API 落库断言 → finally 清理的完整闭环。
6. **T7 `fresh_knowledge_page`**：函数级内存知识库使 e2e 场景自闭环，天然 xdist 安全。
7. 测试规模 200 → **293 passed**，新增覆盖（test_confidence_normalize 19 项、test_xdist_compat 12 项、
   test_propose_pr 10 项等）均针对性强。

---

## 八、规则合规检查

| 规则 | 结论 |
|---|---|
| R2 测试覆盖 | ✅ 新功能均带单测；unit 293 全绿；e2e 新增 8 文件（ERP 用例 marker 隔离，CI 排除） |
| R3 临时方案 | ✅ 首个规范 `TODO(workaround)`（agent/llm_io.py 壳，含原因+回收方式）；⚠️ 应按计划回收（m4） |
| R4 代码质量 | ✅ docstring 覆盖好、异常路径基本都有日志；⚠️ allure_bridge/propose_pr 个别注释漂移 |
| R7 文档双轨 | ✅ 本报告落 `docs/reviews/`（沉淀/历史记录，R7.4 明示不参与双轨） |
| R8 敏感文件 | ✅ 本轮未触碰 `.env`/密钥；ERP 凭证经 env 参数化；⚠️ 内网 IP 与专属端点入 git（M1，环境特定信息建议外移） |

---

## 九、修复优先级建议

1. **C1** example `action_wait` 段移入 healing（一行移位，消除配置加载崩溃）；
2. **H1** propose_pr.py 删除残片（恢复 ruff 门禁绿）；
3. **M1** 环境端点默认值外移（公共端点 + settings.yaml 承载专属值）；
4. **M2** erp_page 登记 `_session_reporters`（一行）；
5. minor/nit 随批清理（m1 分层倒置可单独立项讨论）。
