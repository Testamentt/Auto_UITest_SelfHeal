# LLM/VLM 韧性层：指数退避重试 + 三态熔断（T24）

> **定位**：本次变更的实施计划与验收口径（任务编号 T24）。事实源仍为 `docs/architecture.md`；
> 本文件记录**为什么做、怎么做、怎么验**，完成后作为历史留档。
> **状态**：已完成（2026-11-10；验收见 §5，实现与数字以代码/`reports/resilience-demo.md` 为准）
> **最后更新**：2026-11-10 · 关联：[architecture.md](../../architecture.md) 模型层护栏 · [TODO.md](../TODO.md) T24

## 1. 背景

`src/selfheal/llm/_exceptions.py` 已按可恢复性把模型故障分为：

- `TransientUnavailableError`：429 / 超时 / 连接失败 / 5xx → 可重试；
- `FatalUnavailableError`：401 / 403 / 400 / 404 → 重试无用，需人工核对配置。

但全仓**没有任何消费者**：分级只停留在异常类型上，"瞬态可重试、连续失败该熔断"从未发生。
同时 openai SDK 自带 `max_retries`（默认 2），重试次数不可见、不区分 Fatal/Transient。
本任务补上两个消费者，并顺带修正成本口径（重试的付费调用此前不计费）。

## 2. 目标 / 非目标

**目标**

1. 自研 `RetryPolicy` + `call_with_retry`：只重试 `TransientUnavailableError`，指数退避、上限截断、可选抖动。
2. `CircuitBreaker`：CLOSED → OPEN → HALF_OPEN 三态；连续 N 次**逻辑调用**失败后打开；冷却后放**单个**半开探针；打开期间抛 `CircuitOpenError`（`UnavailableError` 子类），既有降级链原样接管。
3. LLM 与 VLM 各持一个熔断器（端点独立）；配置可关。
4. 可观测：`llm_attempts/llm_retries/llm_circuit_open`、`vlm_*` 写进 `reporter.stats`；成本按真实尝试次数估算。
5. 关闭 openai SDK 自带重试，避免双层重试（3×3=9 次付费调用）。

**非目标**

- 不做 POM 装饰器（另行评估）；
- 不做跨进程 / 分布式熔断（xdist 每 worker 独立，文档写明）；
- 不修改「阈值裁决 / selector 唯一性」两个既有缺陷；
- 不在 CI 调用真实付费模型。

## 3. 设计

### 3.1 新模块 `src/selfheal/llm/resilience.py`

| 组件 | 职责 |
| --- | --- |
| `RetryPolicy` | `max_retries`、`base_delay_s`、`max_delay_s`、`jitter`；`delay_for(attempt) = min(cap, base * 2^attempt)`，抖动取 `uniform(0, delay)` |
| `call_with_retry` | 只捕获 `TransientUnavailableError`；耗尽抛最后一次；Fatal 与其它异常原样抛 |
| `CircuitBreaker` | 三态状态机 + `threading.Lock`；`clock` 可注入；`before_call / record_success / record_failure / reset` |
| `ResilientLLMClient` | 实现 `LLMClient`（`chat/complete/close`），内部走熔断 + 重试 |
| `ResilientVisionClient` | 实现 `VisionClient`（`analyze_image/close`），前缀 `vlm` |
| `wrap_llm_client / wrap_vision_client` | 按配置组装；`max_retries=0 且熔断关闭` 时返回原生 client（零开销） |

**语义约定**

- 熔断失败计数按**逻辑调用**计：一次 `chat()` 内部重试 3 次仍失败只记 1 次失败——衡量的是服务是否持续不可用。
- Fatal 不重试、不参与熔断计数（配置错误应立即暴露，不能被熔断掩盖）。
- 半开状态只允许一个探针在飞，其余调用直接 `CircuitOpenError`。
- 单进程状态：xdist 各 worker 各自独立。

### 3.2 配置（`config.py`，继续 `extra='forbid'`）

```yaml
llm:
  max_retries: 2              # 自研重试次数（0 = 不重试）；SDK 重试固定关闭
  retry:
    base_delay_s: 0.5
    max_delay_s: 8.0
    jitter: true
  circuit_breaker:
    enabled: true
    failure_threshold: 3
    cooldown_s: 30.0
vision:
  # 同结构，独立实例
```

校验：`max_delay_s >= base_delay_s`；`failure_threshold >= 1`；`cooldown_s > 0`。
不设 `retry.enabled`：关闭由 `max_retries=0` 表达，避免两套开关语义打架。

### 3.3 接线

- `llm/factory.py`：底层 client 固定 `max_retries=0`，外层包 `Resilient*`；`get_llm_for_settings(settings, *, stats=None)` 新增可选 stats。
- `agent/orchestrator.py`：把 `self._reporter.stats` 传给两个 factory（reporter 先于 client 创建）。
- `reporting/hooks.py`：`cost_summary()` 优先用 `*_attempts` 估算费用，并附带重试/熔断计数。
- `counting_proxy` 不动：`llm_calls` 仍是逻辑调用数，`*_attempts` 是真实计费次数。

## 4. 测试计划

- **新增 `tests/unit/test_resilience.py`**：退避计算 / 重试语义 / Fatal 不重试 / 熔断状态机 / 半开单探针 / 统计计数 / close 委托 / 工厂与配置校验（约 25–30 条，注入 fake clock 与 sleep，不真睡）。
- **新增 `tests/e2e/test_resilience_e2e.py`**：真浏览器 + 假 LLM（必瞬态失败），验证一次自愈内重试、熔断打开后第二次不再调用 inner、快速降级、无异常泄漏。
- **更新既有单测**：`test_llm_factory.py`、`test_review_fixes_1008.py` 等因工厂返回包装对象需调整断言。

## 5. 验收

| 项 | 标准 |
| --- | --- |
| unit | `pytest -m unit` 全绿，新增用例 ≥25 |
| e2e | `pytest -m "e2e and not erp and not live"` 全绿 |
| lint | `ruff check .` + `ruff format --check .` 全绿 |
| 行为 | 401 不重试；429 重试到上限；熔断打开后立即降级；既有降级链零回归 |
| 证据 | `reports/resilience-demo.md`：无熔断 vs 有熔断的耗时/调用次数对比（脚本生成，不入库） |

## 6. 风险与回滚

- **双层重试**：SDK 固定 `max_retries=0`，否则 3×3=9 次付费调用（本计划核心风险）。
- **默认开启熔断**：阈值 3 / 冷却 30s、只统计瞬态，长跑 CI 影响可控；配置可关。
- **成本口径变化**：从「逻辑调用」改为「真实尝试」，数字会略高——修正而非回归。
- 回滚：按提交粒度 revert；紧急情况改配置即可关闭。
