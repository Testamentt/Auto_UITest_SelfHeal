# Phase 4 实现方案：T1–T4（策略短路 · 真实模型验证 · 指标看板 · 二次自愈）

> 日期：2026-08-04 · 依据：`docs/reviews/2026-08-04-phase3-retrospective.md`、`docs/TODO.md`
> 原则：默认行为兼容、全量测试零回归、每项可独立落地与验证。实施顺序见文末。

---

## T1 · 策略短路（省 LLM/VLM 调用）— 修 P1

### 现状
`orchestrator._best_candidate` 遍历 `strategy_order` 全部策略取最高置信度：即使启发式已 0.95 命中，仍会再调 semantic(LLM) 与 visual(VLM)。无 key 时二者秒返 None 无感；配真实 key 后每次自愈都多花一次 LLM + 一次 VLM。

### 方案
新增「早接受阈值」`early_accept_threshold`：某策略置信度达到该阈值即**立即返回**，不再尝试后续（更贵的）策略。

- `config.py::HealingConfig` 增 `early_accept_threshold: float = 0.85`。
- `_best_candidate` 改造（核心逻辑）：

```python
def _best_candidate(self, scene, selector, description):
    best = None
    early_accept = self._settings.healing.early_accept_threshold
    for name in self._settings.healing.strategy_order:
        strategy_cls = _STRATEGY_REGISTRY.get(name)
        if strategy_cls is None:
            continue
        candidate = self._build_strategy(strategy_cls).repair(scene, selector, description)
        if candidate is None:
            continue
        # 短路：已达"早接受"阈值，不再尝试后续（更贵的）策略
        if candidate.confidence >= early_accept:
            return candidate
        if best is None or candidate.confidence > best.confidence:
            best = candidate
    return best
```

### 行为说明
- 启发式 0.95（≥0.85）→ 直接返回，semantic/visual 不被调用（省钱省时）。
- 启发式 0.7（<0.85 但 ≥confidence_threshold 0.6）→ 仍会继续尝试 semantic/visual 求更优（与现状一致）。
- `strategy_order` 已是"便宜→贵"（heuristic→semantic→visual），短路天然先省贵的。

### 测试（unit）
- `test_short_circuit_skips_costly_strategies`：DOM 含强匹配按钮（启发式 ~0.95），注入 `FakeLLMClient`/`FakeVisionClient`，断言 `best.strategy=="heuristic"` 且 `fake_llm.calls==[]`、`fake_vision.calls==[]`。
- `test_no_short_circuit_when_below`：启发式弱匹配，断言 `FakeLLMClient.calls` 非空（semantic 仍被尝试）。

### 验收
配真实 key 后，启发式命中的自愈不再触发 LLM/VLM；新增 2 项单测通过。

---

## T2 · 真实模型验证 + 证据留存 — 修 P3

### 现状
diagnose/semantic/visual 全用 Fake 测试；真实 LLM/VLM 冒烟因无 key 一直 skip。**核心"AI 自愈"卖点零真实证据。**

### 方案
1. **依赖**：`pip install openai`（llm extra 已含 openai>=1.30）。
2. **配置对齐可用 key**（当前仅有 DashScope key）：本地 `config/settings.yaml`（gitignore）把 LLM 与 VLM 都指向 DashScope 兼容端点：

```yaml
llm:
  provider: openai
  model: qwen-plus                    # DashScope 文本模型（可按账号可用模型调整）
  api_key_env: DASHSCOPE_API_KEY
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
vision:
  provider: openai
  model: qwen3-vl-flash
  api_key_env: DASHSCOPE_API_KEY
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
```

3. **运行**：`pytest -m e2e -k "llm_smoke or visual"`。
4. **证据留存**（代码小增强）：在两个冒烟测试中把**截图 + 模型返回的定位结果**写入 `reports/evidence/`（PNG + JSON），作为展示素材。例如 visual 冒烟末尾：

```python
evidence_dir = Path("reports/evidence"); evidence_dir.mkdir(parents=True, exist_ok=True)
(evidence_dir / "visual_scene.png").write_bytes(scene.screenshot)
(evidence_dir / "visual_result.json").write_text(
    json.dumps({"selector": cand.selector, "confidence": cand.confidence}, ensure_ascii=False))
```

### 验收
semantic/visual 至少一个真实模型用例通过；`reports/evidence/` 留有真实命中截图与结果（可作面试素材）。

### 注意
- 需要真实 API key（🔴 阻断项）。key 仅走环境变量，绝不入库。
- 若账号无对应模型权限，需先确认可用模型名再配 `model`。

---

## T3 · 自愈指标看板（价值叙事落点）— 修 P4

### 现状
`reporting/dashboard.py` 只是审计表（逐条记录），无聚合指标；"提升通过率"无量化支撑。

### 方案（分两步）

**T3a · 指标聚合 + 看板增强**（本期落地）
- 新增 `reporting/metrics.py`：

```python
def compute_metrics(records: list[HealingRecord]) -> dict:
    total = len(records)
    success = sum(1 for r in records if r.success)
    strategy_dist, rootcause_dist = {}, {}
    for r in records:
        if r.strategy:
            strategy_dist[r.strategy] = strategy_dist.get(r.strategy, 0) + 1
        if r.root_cause:
            rootcause_dist[r.root_cause] = rootcause_dist.get(r.root_cause, 0) + 1
    return {
        "total": total,
        "success": success,
        "success_rate": (success / total) if total else 0.0,
        "strategy_distribution": strategy_dist,
        "root_cause_distribution": rootcause_dist,
    }
```

- 增强 `dashboard.py::render_dashboard`：顶部渲染**指标摘要卡片**（自愈总数 / 成功率 / 策略分布 / 根因分布），下方保留现有审计表。仍为纯 HTML 无依赖。
- `HealingReporter` 增 `metrics()` 便捷方法（内部调 `compute_metrics(self.records)`）。

**T3b · 通过率 A/B 对比**（增强项，本期可后置）
- 建一个含多个可失效定位器的演示套件，分别以 `--no-selfheal`（统计失败数）与开启自愈（统计自愈数）运行，产出"通过率 X%→Y%"对比。需专用演示套件，工作量较大，标为 T3b 跟进。

### 测试（unit）
- `test_metrics_aggregation`：构造若干 `HealingRecord`，断言 total/success/success_rate/分布正确；空列表返回 rate 0。
- `test_dashboard_contains_metrics`：`render_dashboard` 输出含成功率与策略分布字段。

### 验收
`compute_metrics` 单测通过；看板渲染出指标摘要 + 审计表。

---

## T4 · 二次自愈 / 缓存验证 — 修 P2

### 现状
`run()` 返回（知识缓存或策略）新定位器后，`HealingLocator` 重试一次；若该选择器**也失效**，异常直接上抛、无二次自愈。且知识缓存被无条件信任（页面可能已变，缓存选择器已失效）。

### 方案（两部分）

**T4a · 缓存验证**（修"缓存无条件信任"）
`orchestrator._lookup_knowledge` 命中后，先校验 `case.new_selector` 在当前页面仍存在，否则视为缓存失效、转策略重新修复：

```python
def _lookup_knowledge(self, scene, selector, dom_fingerprint=None):
    case = self._knowledge.find_repair(selector, dom_fingerprint)
    if not case or case.confidence < self._settings.healing.confidence_threshold:
        return None
    # 缓存验证：新定位器须在当前页面仍存在，否则视为失效
    if self._page is not None and not self._selector_exists(case.new_selector):
        return None
    return HealOutcome(success=True, new_selector=case.new_selector, ...)

def _selector_exists(self, selector) -> bool:
    try:
        return self._page.locator(selector).count() > 0
    except Exception:  # noqa: BLE001
        return False
```

**T4b · 有界二次自愈**（修"重试失败即硬挂"）
`orchestrator.run` 增 `use_knowledge: bool = True` 参数；`_healing_action` 的重试包一层有界二次自愈（跳过知识缓存，防重复命中同一失效项）：

```python
# healing_locator._healing_action 的 wrapped 内
relocated = self._heal_and_resolve(exc)
try:
    return getattr(relocated, name)(*args, **kwargs)
except Exception as retry_exc:
    if not _is_timeout_error(retry_exc):
        raise
    # 二次自愈：新定位器也失效 → 重走闭环（跳过知识缓存，最多一次，防循环）
    relocated2 = self._heal_and_resolve(retry_exc, use_knowledge=False)
    return getattr(relocated2, name)(*args, **kwargs)
```
- `_heal_and_resolve(exc, use_knowledge=True)` 把 `use_knowledge` 透传给 `orch.run`。
- 第二次结果是裸动作调用（不再包裹），失败即上抛——**有界、无死循环**。

### 测试（unit）
- `test_cache_validation_stale`：knowledge 有案例但 `new_selector` 在 fake page 不存在（`count()==0`）→ `_lookup_knowledge` 返回 None。
- `test_cache_validation_valid`：`count()>0` → 返回 outcome。
- `test_run_skips_knowledge_when_disabled`：`run(..., use_knowledge=False)` 不查知识库。
- （e2e 拉伸项）构造"修复后选择器又失效"场景验证二次自愈。

### 验收
陈旧缓存不再被盲信；修复选择器失效时能二次自愈而非硬失败；新增单测通过、既有测试零回归。

---

## 实施顺序与依赖

1. **T1 策略短路**（纯代码、无 key 依赖、风险低）→ 先做。
2. **T3a 指标看板**（纯代码、无 key 依赖）→ 价值叙事落点。
3. **T4 二次自愈/缓存验证**（纯代码、无 key 依赖，改动 healing_locator+orchestrator，需仔细回归）。
4. **T2 真实模型验证**（🔴 需 API key，最后做；可与 T1/T3/T4 并行，取决于 key 可用性）。

每项独立提交 + 全量回归（`pytest -m unit` + `-m e2e` + `ruff`），并更新 `docs/TODO.md` 勾选状态。

## 风险与注意

- T4 触及自愈核心链路（healing_locator + orchestrator），改动后必须跑全量 e2e 确认既有三场景 + 弹窗 + 智能等待不回归。
- T2 依赖真实 key 与账号模型权限；key 仅走环境变量，严禁入库。
- T1 的 `early_accept_threshold` 默认 0.85 需与 `confidence_threshold` 0.6 区分（早接受 > 接受阈值）；若设置不当（≤confidence_threshold）会退化为"首个达标即停"，需在配置注释中说明。
