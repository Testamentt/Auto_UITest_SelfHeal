# 项目路线图（Roadmap）

> **定位**：阶段路线与**决策记录**（D 编号）——回答"做到哪了、为什么这么定、下一步做什么"。
> **状态**：技术文档（事实源，参与文档双轨；任务明细见 [TODO.md](TODO.md)）
> **最后更新**：2026-10-08 · 关联：[architecture.md](architecture.md) 架构 · [TODO.md](TODO.md) 任务清单 · [backlog/low-priority.md](backlog/low-priority.md) 低优先项 · [reviews/2026-10-08-fix-report.md](reviews/2026-10-08-fix-report.md) 最近一轮加固

## 当前目标

**Phase 1–5 全部完成**（最小闭环 → AI 大脑 → 沉淀进阶 → 证据指标 → 语义化与风险控制），
2026-08-31 起进入「真实被测系统 + 工程化」阶段：T19 通知 / T20 A/B 实证 / T21 xdist / T22 草稿 PR / T23 管伊佳 ERP 迁移均已落地。

**当前阶段：稳定化与文档治理**（2026-10-08）：

- 全量代码审查（H1–H6 / M1–M16 / L1–L20）→ **High 与 Medium 已全部修复**，验收 `pytest -m unit` 404 passed、全量 `pytest` 427 passed / 0 failed。
- 本轮加固要点：模型层异常分级与可配置护栏（超时 / token / 图片体积）、弹窗关闭作用域与信号分级、资源所有权与生命周期、
  可观测性（降级原因入日志）、知识库双后端语义对齐、CI 门禁补齐（`ruff format --check` + `--strict-markers` + 未分类测试检测）。
- 文档治理：新增 [docs/README.md](README.md) 作为唯一入口（分区 + 写作规范），历史记录只读，低优先项移入 [backlog/](backlog/)。
- 待办与遗留：Low 20 项 + 主动延期 2 项见 [backlog/low-priority.md](backlog/low-priority.md)。

## 关键约束

- 遵循 [`RULE.md`](../RULE.md) R1–R8：先计划后写入、测试双覆盖（`-m unit` + `-m e2e`）、临时方案三步管理、
  代码质量与文档、变更沉淀、先收敛计划、文档双轨、敏感文件纪律。
- 模型调用一律经 `llm/` 抽象层（`factory` 构建 + `registry` 注册）；配置集中在 `src/selfheal/config.py`（pydantic 严格校验）。
- **默认配置（无 API key / 未装 openai）下行为与 Phase 1 等价**：诊断退规则式、语义与视觉策略跳过，启发式与知识库复用照常。
- 演示与 e2e 基于自建静态演示页；真实被测系统为管伊佳 ERP（jshERP，本地环境，CI 排除 erp）。

## 架构决策（插件化自愈）

自愈能力是**挂件 / 插件**，不侵入 Playwright 框架本身。三项硬性要求：

1. **POM 无缝切换**：POM 类只依赖 `page` 接口；开 / 关自愈同一份代码都能跑。
2. **可开关、零侵入**：由 pytest fixture 按 `settings.healing.enabled` + CLI `--selfheal/--no-selfheal` 提供；
   关闭时透传原生行为、零开销。
3. **兜底机制**：`locator(sel, fallback=..., description=...)`；AI 不确定时按 `healing.on_uncertain` 处理。

> 集成方式（D5）：Fixture 注入为骨架 + 接口兼容代理做底层拦截 + 装饰器作可选补充；**不用** import 替换 / monkeypatch。
> 兜底行为（D6）：`on_uncertain` 默认 `use_fallback`（优先人工备用定位器）；无备用则 fail；`pause` 仅交互模式可用。

## 整体路线

| 阶段 | 目标 | 状态 |
| --- | --- | --- |
| **Phase 1 · 最小闭环** | 端到端自愈跑通（启发式）+ 插件化骨架 | ✅ 完成 |
| **Phase 2 · AI 大脑** | LLM 智能诊断 + 语义定位 + 自愈看板 v0 | ✅ 完成 |
| **Phase 3 · 沉淀与进阶** | 知识库持久化 + 弹窗 + 视觉 + 智能等待 | ✅ 完成 |
| **Phase 4 · 证据与指标** | 真实模型验证 + 指标看板 + 加固（短路 / 二次自愈）+ 展示包装 | ✅ 完成 |
| **Phase 5 · 语义化与风险控制** | 知识库向量检索 + 风险控制（豁免 / dry-run / 人审 / flaky / 成本） | ✅ 完成 |
| **稳定化阶段** | 真实被测系统（ERP）+ 通知 / A/B / xdist / 草稿 PR（T19–T23）+ 2026-10-08 全量加固 | ✅ 进行中 |

## 已达成结论（决策记录）

| # | 决策点 | 结论 | 理由 |
| --- | --- | --- | --- |
| D1 | 实施路径 | 纵向切片优先 | 先证明价值；不卡待定项 |
| D2 | LLM/VLM | Phase 2 接入，OpenAI 兼容优先 | 避免选型阻塞最小闭环 |
| D3 | 演示对象 | 自建本地测试页（POM 化） | 可控、可复现、可无缝切换其他 POM 用例 |
| D4 | 知识库后端 | SQLite | 零依赖、可持久化、易演示 |
| D5 | 自愈集成方式 | Fixture 骨架 + 接口代理 + 装饰器补充；不用 import 魔法 | 可开关、POM 无缝、可维护（R4） |
| D6 | 不确定时兜底 | use_fallback，无备用则 fail；pause 仅交互模式 | CI 友好 + 人工兜底 |
| D7 | LLM 客户端形态 | 单一 OpenAI 兼容客户端（`base_url` + `model` 覆盖多 provider），SDK 惰性导入 | 切换 provider 只改配置；CI 无 openai 也能 import |
| D8 | DOM 公共能力 | 抽 `agent/dom/` 公共工具（解析 / 稳定定位器 / 指纹 / 候选），策略与 LLM 提示共用 | 消除循环导入、重复与私有耦合（R4） |
| D9 | LLM 降级策略 | 不依赖 `response_format`；`extract_json` 容错 + 输出白名单 + 防幻觉护栏（selector 须真实存在） | 模型不稳定时闭环不中断 |
| D10 | 知识库后端形态 | `KnowledgeBackend` 接口 + 内存 / SQLite 双实现 + factory 选择；DOM 指纹参与检索择优 | 可切换、可持久化、同结构页面复用更可靠 |
| D11 | 弹窗处理 | 知识优先（弹窗特征库）+ 关闭按钮启发式识别，成功后沉淀特征；动作超时先清弹窗再走自愈 | 直击"被遮挡"类失败 |
| D12 | 智能等待 | 先可见，再要求 `bounding_box` 连续 `stable_ms` 不变；POM 显式调用（可选增强） | 减少加载抖动误判，不改变默认行为 |
| D13 | 视觉定位 | OpenAI 兼容 VLM（`qwen3-vl-plus`，备选 qwen3.8-flash）；候选集护栏；key 走 `DASHSCOPE_API_KEY`；`base_url` 默认 DashScope 公共 compatible-mode，专属百炼 MaaS 端点经本机 `config/settings.yaml` 覆盖 | 复用 OpenAI 兼容机制；防幻觉；密钥参数化；环境端点不入代码默认值 |
| D14 | 知识库语义化 | 本地确定性 n-gram 哈希 TF 向量（零 API 费用）+ numpy 余弦；L1 `repair_key` 硬短路 → L2 启发式 → L3 语义检索 → L4 VLM；按 `page_fingerprint` 分桶；采纳规则（sim>0.92 且 verified / 7 天内 sim>0.80 自动，其余写人审清单） | 热路径不调 API embedding；ID 变但文本 / 结构不变仍可命中；防污染 + 冷启动免人审 |
| D15 | Allure 报告增强 | 轻量桥 `reporting/allure_bridge.py`（`_HAS_ALLURE` 单点探测，未装全 no-op）；环境页 + marker→标签（erp>healing>e2e>unit 取唯一 feature）+ 证据附件；CI `publish` job 发布 GitHub Pages（gh-pages，含历史趋势，仅 main） | 展示层不该侵入 agent；标签单 feature 防爆炸；闭环过程以结构化附件呈现够用 |
| D16 | 模型层护栏与异常语义 | 超时 / 输出上限 / 重试次数 / 图片体积上限**全部可配**（`LLMConfig`、`VisionConfig`）；异常分级为 `FatalUnavailableError`（鉴权 / 参数，重试无用）与 `TransientUnavailableError`（限流 / 超时 / 5xx，可重试），均继承 `UnavailableError` 保持既有降级契约；`HealingFailedError` 同时继承 Playwright 与内置 `TimeoutError` | 2026-10-08 实证：key 与端点不同平台时只报异常类名，无法定位；401 与 429 同待遇导致重试策略与成本不可控 |
| D17 | 文档治理 | `docs/README.md` 为文档唯一入口（分区 + 写作规范 + 双轨说明）；`sessions/plans/reviews` 只读；低优先待办入 `docs/backlog/`、模板入 `docs/templates/`；同一事实只允许一个归属地，其余改指针 | 2026-10-08 文档体检：格式不一、前后错位、跨文件重复、散落多处 |

## 待解决问题

- **低优先与延期项**（[backlog/low-priority.md](backlog/low-priority.md)，全部未开工）：L1–L20、
  M10（engine/collect 反向依赖 agent/knowledge/reporting，需独立计划 + 文档双轨）、M15 第 3 条（链式自愈 e2e 需先加 DOM 夹具）。
- **语义 LLM 段缺 L2 交叉校验**：视觉段有 C4 融合降权，语义段没有——实测模型会给"与页面无关的描述"高置信候选并被采纳
  （证据与备选方案见 backlog R5）。照搬视觉段公式会误伤合法场景，需先设计"意图重叠下限"判据并用真实数据标定。
- **真实模型标定**：T5 置信度收缩（`shrink_self_reported`）仍是经验值 raw²；已采数据点（LLM 自报 1.0 / VLM 0.921），
  待多场景数据后按段标定（当前默认关闭，零回归）。
- **视觉控件画像**：当前只做"候选集内选择"，画像能力（模板 + 语义）仍属 TBD。
- **长页面截图成本与体积**：已加 `vision.max_image_bytes` 护栏（超限降质 / 缩放转 JPEG），但未做像素预算与分层截图的成本实验。
- **语义向量 v2**：本地 n-gram 跨语言（尤其中文）偏弱，升级 fastembed 本地模型（如 bge-small-zh）需先有数据支撑收益；
  规模大时评估 sqlite-vec / Chroma。
- **知识库运维**：`scripts/verify_case.py` 已提供人审标记入口；去重 / 失效清理尚未提供（backlog R1–R4 记录残余项）。

## 下一步计划

> 任务级明细与验收统一登记在 **[TODO.md](TODO.md)**（唯一待办入口），本节只列近期三件事与长线项。

1. **文档双轨收口**：本轮技术文档（README / architecture / roadmap）改动后，按 [`.claude/skills/doc-study-sync/SKILL.md`](../.claude/skills/doc-study-sync/SKILL.md) 同批同步学习版镜像并更新版本印记。
2. **backlog 分批清理**：优先 R5（语义段护栏，有实测证据）→ L1–L4（生命周期与等待有界性）→ CI 工程项（timeout / concurrency / live marker）。
3. **ERP 场景补全**：供应商菜单场景（需菜单点击导航 POM）与 ERP 自愈记录进看板的回归验证。

**长线（待数据或待决策）**：fastembed 语义化 v2、T5 按段标定收缩、`healing.action_wait` 默认值是否翻转、
iframe / Shadow DOM 自愈评估 spike、自愈指标跨运行时间序列。
