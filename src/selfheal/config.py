# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""集中式配置加载与校验。

所有运行时配置统一由本模块用 pydantic 从 config/settings.yaml 加载，
业务代码不得散落读取环境变量或硬编码配置。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"

# 项目根（仓库根）：`config/` 的上一级。相对路径配置的统一解析基点。
# 用本文件位置推导而非 CWD：编程式 pydantic 默认值（如 `.cache/knowledge.db`）若按 CWD
# 解析，从不同目录运行会各自新建一套知识库，命中率与沉淀结果随之分叉。
PROJECT_ROOT = DEFAULT_CONFIG_PATH.parents[1]

# 已实现的向量化方法：Literal 只负责挡拼写，能力清单在此声明——
# 未列入 _IMPLEMENTED 的取值在加载期报错，避免"配了却没实现"的死配置。
_IMPLEMENTED_EMBEDDING_METHODS = frozenset({"ngram"})

# 已知的"相对路径"配置字段：`段名.字段名 -> 相对路径是否还叠加项目根`。
# 这些字段指向**仓库内**的产物目录/文件（trace 输出、知识库 db），一律以项目根解析；
# 显式绝对路径原样保留（用户有意指向仓库外，例如共享盘或临时目录）。
_PATH_FIELDS: tuple[tuple[str, str], ...] = (
    ("browser", "trace_dir"),
    ("knowledge", "path"),
)

# 加载本地 .env（已被 .gitignore 忽略，绝不提交）；不覆盖已存在的同名环境变量。
# 未安装 python-dotenv 时静默跳过（此时仅从系统环境变量读取密钥）。
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:  # pragma: no cover - 取决于是否安装 python-dotenv
    pass


class _StrictModel(BaseModel):
    """配置模型基类：extra='forbid'（由顶层下发到全部子模型）。

    此前只有顶层 Settings 拒绝未建模字段，嵌套段（如 healing:）里拼错/多余的键
    被 pydantic 默认静默丢弃（extra='ignore'）——恰是"死配置"漂移的温床（如
    dry_run 安全开关拼错后静默失效）。统一基类后，任何层级出现未建模键都在加载期报错。
    """

    model_config = ConfigDict(extra="forbid")


class BrowserConfig(_StrictModel):
    """浏览器配置。

    - channel: 传给 launch 的浏览器渠道。"chrome" 使用系统已装的 Chrome（无需下载 Chromium），
      "chromium" 使用 Playwright 自带内核（需 playwright install chromium）。
    - trace: 是否录制 Playwright trace（供失败回放与视频追溯），用 `playwright show-trace` 回放。
    - trace_dir: trace .zip 输出目录（相对路径在加载期按项目根解析，见 load_settings）。
    - viewport：浏览器视口，**只接受 width / height 两个正整数字段**。此前是裸
      `dict[str, int]`，"wdith"/"hight" 这类拼错键会被 `extra='forbid'` 之外的路径静默放行，
      拼错后 Playwright 拿不到视口参数 → 用默认尺寸跑，截图与视觉定位的基准悄悄改变。
      形状有意保持普通 dict（`engine/browser.py` 直接把它交给 `new_context`），故用
      `field_validator` 而非子模型（子模型会变成非 dict 类型，破坏该调用点）。
    """

    headless: bool = True
    channel: str = "chrome"
    slow_mo: int = 0
    viewport: dict[str, int] = {"width": 1280, "height": 800}
    trace: bool = False
    trace_dir: str = "reports/traces"

    @field_validator("viewport", mode="before")
    @classmethod
    def _validate_viewport(cls, value: Any) -> dict[str, int]:
        """视口键名白名单 + 正整数校验：未知键 / 拼错键 / 非正尺寸都在加载期报错。

        `mode="before"` 有意为之：pydantic 的 `dict[str, int]` 会做宽松强制转换
        （`"1280"` → 1280、`True` → 1、`1280.0` → 1280），先于强转校验才能让
        "字符串 / 布尔尺寸"这类明显笔误被看见，而不是被抹平成看似合法的整数。

        未知键**直接拒绝**而非 warning：`viewport` 只喂给 Playwright 的 `new_context`，
        多出来的键必然意味着配置写错（如拼错 `wdith`、误以为支持 `device_scale_factor`），
        而 Playwright 会静默退回默认视口——用户却以为改动生效了。
        """
        if not isinstance(value, dict):
            raise ValueError(f"browser.viewport 必须是映射: {value!r}")
        unknown = sorted(set(value) - {"width", "height"})
        if unknown:
            raise ValueError(
                f"browser.viewport 含未知键 {unknown}（仅支持 width / height）——"
                "拼错键会让 Playwright 退回默认视口，属静默失效"
            )
        for key in ("width", "height"):
            if key not in value:
                raise ValueError(f"browser.viewport 缺少 {key!r}（需同时给出 width 与 height）")
            size = value[key]
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                raise ValueError(f"browser.viewport[{key!r}] 必须是正整数: {size!r}")
        return value


class RetryConfig(_StrictModel):
    """模型调用的指数退避重试参数（D25）。

    重试**次数**由同段落的 `max_retries` 控制（0 = 不重试）；本模型只承载退避节奏。
    仅瞬态故障（429 / 超时 / 连接失败 / 5xx）会重试，Fatal（401/403/400/404）直接抛。

    - base_delay_s / max_delay_s: 第 n 次重试等待 min(max_delay_s, base_delay_s * 2^n)；
      上限截断避免长尾重试把 CI 拖死。
    - jitter: 是否在 [0, delay] 内取随机（full jitter），避免多 worker 同时重试形成风暴。
    """

    base_delay_s: float = Field(default=0.5, ge=0.0)
    max_delay_s: float = Field(default=8.0, ge=0.0)
    jitter: bool = True

    @model_validator(mode="after")
    def _validate_bounds(self) -> RetryConfig:
        """上限不得小于起点（否则 base 永远不生效，退化为固定上限）。"""
        if self.max_delay_s < self.base_delay_s:
            raise ValueError(
                f"retry.max_delay_s ({self.max_delay_s}) 不能小于 "
                f"retry.base_delay_s ({self.base_delay_s})"
            )
        return self


class CircuitBreakerConfig(_StrictModel):
    """模型调用的熔断器参数（D25）。

    连续 `failure_threshold` 次**逻辑调用**最终失败（仅瞬态）后打开；冷却 `cooldown_s`
    内直接抛 `CircuitOpenError` 走既有降级链；冷却后放单个半开探针。
    熔断状态是**单进程**的：xdist 各 worker 各自独立（不跨进程共享）。
    """

    enabled: bool = True
    failure_threshold: int = Field(default=3, ge=1)
    cooldown_s: float = Field(default=30.0, gt=0.0)


class LLMConfig(_StrictModel):
    """LLM 配置。

    - enabled: 模型能力总开关。False 或缺少 API key 时，诊断退回规则式、语义策略被跳过（等价于完全不启用模型能力的基线行为）。
    - provider / model / base_url: OpenAI 兼容接口，可指向 OpenAI / DeepSeek / Qwen / 智谱等。
      **base_url 与 model 必须与 api_key_env 指向的密钥属同一平台**，否则一律 401/404
      （实测：把 CommandCode 的 key 指向 api.deepseek.com 会得到
      `模型调用失败: AuthenticationError`）。
    - api_key_env: 从环境变量名读取密钥（如 OPENAI_API_KEY），不写明文。
    - timeout_s / max_tokens / max_retries: 此前写死在客户端签名里，
      且本模型 `extra='forbid'` 使用户无法在 `llm:` 段配置它们。VLM 侧已因同类问题
      （曾用 20s/500 导致超时/截断）放宽过，LLM 侧对齐配置入口。
    - retry / circuit_breaker（D25）: 自研退避重试与熔断参数。`max_retries`
      语义明确为"自研重试次数"（openai SDK 自带重试固定关闭，避免双层重试放大付费调用）；
      仅瞬态故障重试，Fatal 直接抛；熔断打开后由既有降级链接管。详见 `llm/resilience.py`。
    - max_tokens_field: 输出上限**字段名**，`max_tokens`（默认，兼容 DeepSeek/Qwen 等
      既有端点）或 `max_completion_tokens`（新推理模型族）。两者互斥，客户端**只发其中一个**；
      选 `max_completion_tokens` 时模型族不接受自定义 temperature，客户端会省略该参数（并把
      被忽略的配置值记 warning）。切换字段名等价于声明"目标模型族不同"，属显式选择。
    """

    enabled: bool = True
    provider: str = "openai"
    model: str = "deepseek-v4-flash"
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str | None = "https://api.deepseek.com"
    temperature: float = 0.0
    timeout_s: float = Field(default=15.0, gt=0)
    max_tokens: int = Field(default=2000, gt=0)
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    max_retries: int = Field(default=2, ge=0)
    # D25：退避重试节奏与熔断参数（与 vision 各自独立，端点故障互不影响）
    retry: RetryConfig = RetryConfig()
    circuit_breaker: CircuitBreakerConfig = CircuitBreakerConfig()


class ProbeConfig(_StrictModel):
    """自愈触发前的快速探测（2026-11-12）。

    **为什么需要**：开启自愈时，定位失败要先等满 Playwright 默认超时（30s）才进入自愈闭环，
    单条失效步骤因此 +30s（用例内实测 30.63s → 0.63s），CI 场景不可接受。快速探测先以
    `timeout_ms` 的短超时判断目标是否"当下可用"：不可用即**立刻**进入自愈，不必等满默认超时。

    **为什么不改变语义**：探测只决定"多快到达同一个结论"，不改变结论本身——
    - 探测通过 → 走原生动作（与未开启本功能完全一致）；
    - 探测超时 → 走自愈闭环（与等满 30s 超时后的路径一致）。
    探测本身是 best-effort：任何异常只记 debug 并回退原生等待，不引入新的失败源。

    - enabled: 总开关，默认 True；置 False 恢复"等满默认超时"的旧行为（零回归）。
    - timeout_ms: 探测超时（毫秒）。默认 2500：足够覆盖正常慢加载，又远小于 30s 默认超时。
      **注意**：配得比页面正常加载还短，会让"慢但最终会出现"的元素提前触发自愈
      （不会直接失败——自愈无果仍按 `on_uncertain` 兜底），团队应按被测系统调优。
    """

    enabled: bool = True
    timeout_ms: int = Field(default=2500, gt=0)


class ActionWaitConfig(_StrictModel):
    """动作前置智能等待。

    - enabled: 动作（click/fill 等 HEALABLE）执行前是否先做一次短稳等待。
      **默认 False**——保持"POM 显式调用 wait_until_stable"的既有行为（不改变默认动作语义），
      零侵入；需要"默认融入"的团队开启即可。
    - timeout_ms / stable_ms: 短稳等待参数，复用 engine/smart_wait.py 的 wait_until_stable。
      等待是增强而非正确性前提：失败仅记 debug、不阻塞动作（避免把等待变成新的超时源）。
      值域在加载期校验：`timeout_ms=0` 会被 Playwright 当作"不超时"，
      让等待失去上界；`poll_ms<=0` 会变成忙等。
    """

    enabled: bool = False
    timeout_ms: int = Field(default=2000, gt=0)
    stable_ms: int = Field(default=300, ge=0)


class HealingConfig(_StrictModel):
    """自愈行为配置。

    - enabled: 插件总开关。False 时 HealingPage 透传为原生行为，不触发任何修复。
    - on_uncertain: AI 不确定（置信度 < confidence_threshold）时的兜底策略（决策 D6）。
      use_fallback=用人工备用定位器（默认，CI 友好）；pause=交互模式下暂停等人工；fail=快速失败。
    - early_accept_threshold: "早接受"阈值（策略短路）。某策略置信度达到该值即立即采纳，
      不再尝试后续（更贵的）策略，省 LLM/VLM 调用。应 > confidence_threshold。
    - exclude_url_patterns: 高风险页豁免——URL 匹配任意 glob 模式则不触发自愈
      （支付 / 强授权 / 审计类页面误改代价高，通常不自愈）。如 ["*://*/pay*", "*://*/admin*"]。
    - dry_run: 仅报告不执行——只生成修复建议（写 fix-proposals），不实际换定位器重试、
      不持久化知识，供人审后手动采纳。
    - fix_proposals: 修复成功后输出「原→新」PR 化建议清单（Markdown/JSON，不自动改库），
      人确认后才合入代码。默认关闭；开启后每次成功自愈追加一条建议。
    - llm_diagnose_threshold: LLM 归因触发阈值。低于该值的"成功"修复（置信度落在
      [confidence_threshold, llm_diagnose_threshold) 区间）也补一次 LLM 诊断，丰富审计与人审清单；
      应 > confidence_threshold。策略链全失败时无条件触发 LLM 归因（不依赖本阈值）。
    - strategy_thresholds / strategy_early_accept: 按策略独立阈值。键为策略名
      （heuristic / semantic / visual，可插拔注册表），缺省回退全局 confidence_threshold /
      early_accept_threshold。**采纳裁决逐候选进行**：每个候选先按自己策略的阈值过滤，合格者
      才参与"取最优"——高置信但未达自身阈值的候选不会遮蔽低置信但已达标的候选；未知名键宽待
      （回退全局，便于先调配置再看行为），但由 `agent/fix_generator.py` 在调度时记一次
      warning（与 strategy_order 的处理对称）。
    - shrink_self_reported: 置信度归一化的可选保守收缩。开启后对自报型策略
      （LLM 语义自报段）做 raw² 收缩，缓解自报虚高被直接采纳；默认关闭（保持既有采纳行为
      不变），待真实模型校准数据沉淀后再启用（见 agent/confidence.py）。
    - probe: 自愈触发前的快速探测（`ProbeConfig`）。默认开启——定位失败不必等满 Playwright
      默认超时（30s）才进自愈，改为先以短超时（默认 2.5s）探测；探测不改变"是否自愈"的
      判断口径，只缩短到达结论的时间。见 `ProbeConfig` 与 engine/healing_locator.py。
    """

    enabled: bool = True
    strategy_order: list[str] = ["heuristic", "semantic", "visual"]
    knowledge_first: bool = True
    confidence_threshold: float = 0.6
    early_accept_threshold: float = 0.85
    llm_diagnose_threshold: float = 0.75
    strategy_thresholds: dict[str, float] = {}
    strategy_early_accept: dict[str, float] = {}
    shrink_self_reported: bool = False
    on_uncertain: Literal["use_fallback", "pause", "fail"] = "use_fallback"
    exclude_url_patterns: list[str] = []
    dry_run: bool = False
    fix_proposals: bool = False
    action_wait: ActionWaitConfig = ActionWaitConfig()  # 动作前置智能等待（默认关闭）
    probe: ProbeConfig = ProbeConfig()  # 自愈触发前的快速探测（默认开启）

    def accept_threshold(self, strategy: str) -> float:
        """该策略的采纳阈值：strategy_thresholds 命中则用之，否则回退全局。"""
        return self.strategy_thresholds.get(strategy, self.confidence_threshold)

    def early_accept_for(self, strategy: str) -> float:
        """该策略的"早接受"短路阈值：strategy_early_accept 命中则用之，否则回退全局。"""
        return self.strategy_early_accept.get(strategy, self.early_accept_threshold)

    @model_validator(mode="after")
    def _validate_thresholds(self) -> HealingConfig:
        """阈值层级约束：值域 [0,1]、early_accept > confidence、llm_diagnose > confidence、按策略同构（防配置倒挂）。"""
        # 全局三阈值补 [0,1] 值域校验——confidence_threshold 配成负数会使所有
        # 候选（含 0 置信度垃圾修复）被无条件采纳，>1 则自愈永不采纳，均为静默错误门控。
        for name in ("confidence_threshold", "early_accept_threshold", "llm_diagnose_threshold"):
            value = getattr(self, name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{name} 超出 [0, 1] 值域: {value}")
        if self.early_accept_threshold <= self.confidence_threshold:
            raise ValueError("early_accept_threshold 应 > confidence_threshold")
        if self.llm_diagnose_threshold <= self.confidence_threshold:
            raise ValueError("llm_diagnose_threshold 应 > confidence_threshold")
        # 按策略阈值——值域 [0,1] 且同一策略的 early_accept 必须 > accept（防倒挂）
        for key, value in list(self.strategy_thresholds.items()) + list(
            self.strategy_early_accept.items()
        ):
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"按策略阈值 {key!r} 超出 [0, 1] 值域: {value}")
        for key, accept in self.strategy_thresholds.items():
            early = self.strategy_early_accept.get(key)
            if early is not None and early <= accept:
                raise ValueError(
                    f"strategy_early_accept[{key!r}] 应 > strategy_thresholds[{key!r}] "
                    f"({early} <= {accept})"
                )
        return self


class SemanticKnowledgeConfig(_StrictModel):
    """知识库语义检索（L3）的阈值配置（2026-11-12）。

    此前这 4 个阈值是 `agent/strategies/semantic.py` 里的模块级常量，调一次要改代码。
    外置为配置后可按被测系统的真实分布标定，不必发版。

    - min_sim: 进入检索的最低相似度。低于它的案例根本不参与（省一次人审清单写入）。
    - fresh_sim / fresh_days: 「新鲜窗口」内的自动采纳阈值与窗口天数。冷启动阶段放宽
      （未人审的案例也能复用），窗口期外必须已人审才采纳。
    - verified_sim: 已人审案例的自动采纳阈值（人审过的可以放宽，但仍要求较高相似度）。

    **层级约束**：`0 <= min_sim <= fresh_sim <= verified_sim <= 1`。倒挂（如 verified 比
    fresh 还低）意味着"更严的信任条件反而更容易采纳"，属配置错误，加载期直接报错。
    """

    min_sim: float = Field(default=0.75, ge=0.0, le=1.0)
    verified_sim: float = Field(default=0.92, ge=0.0, le=1.0)
    fresh_sim: float = Field(default=0.80, ge=0.0, le=1.0)
    fresh_days: int = Field(default=7, ge=0)

    @model_validator(mode="after")
    def _validate_ordering(self) -> SemanticKnowledgeConfig:
        """阈值不得倒挂：min_sim <= fresh_sim <= verified_sim。"""
        if self.min_sim > self.fresh_sim:
            raise ValueError(
                f"knowledge.semantic.min_sim ({self.min_sim}) 应 <= fresh_sim ({self.fresh_sim})："
                "否则「进入检索」的门槛高于「自动采纳」门槛，后者永远不可达"
            )
        if self.fresh_sim > self.verified_sim:
            raise ValueError(
                f"knowledge.semantic.fresh_sim ({self.fresh_sim}) 应 <= verified_sim "
                f"({self.verified_sim})：未人审的案例不应比已人审的更容易被采纳"
            )
        return self


class KnowledgeConfig(_StrictModel):
    """知识库配置。

    - backend: memory=进程内（测试/临时）；sqlite=持久化（默认，重启后仍可命中复用）。
    - path: sqlite 数据库文件路径（.cache/ 已 gitignore）。
    - semantic: L3 语义检索阈值（`SemanticKnowledgeConfig`）。
    """

    backend: Literal["memory", "sqlite"] = "sqlite"
    path: str = ".cache/knowledge.db"
    semantic: SemanticKnowledgeConfig = SemanticKnowledgeConfig()


class VisionConfig(_StrictModel):
    """视觉模型配置（VLM）。

    model=qwen3-vl-plus（备选 qwen3.8-flash）；key 从 api_key_env
    指定的环境变量读取（参数化，绝不硬编码 / 提交 git）。
    - base_url 默认为 **DashScope 公共 compatible-mode 端点**（可移植默认）；专属百炼 MaaS
      实例端点属环境特定配置，放 gitignore 的 `config/settings.yaml`（勿写进代码默认值）。
    - timeout_s / max_tokens: plus 级模型响应慢于 flash、视觉描述更长，默认放宽
      （实测 20s/500 曾导致 ERP 场景 VLM 调用超时/截断而自愈失败）。
    - max_retries: 显式重试次数，0 表示不重试；此前静默继承
      openai SDK 默认值（2 → 一次失败最多 3 次付费调用）。
    - max_image_bytes: 单次请求图片体积护栏。`full_page` 长截图可达数 MB，
      base64 再 ×1.33，超服务端上限即 400 → 视觉策略"永不生效"。超限时经 Pillow 降质/缩放转 JPEG
      （Pillow 未安装则记 warning 后按原图发送）。
    """

    enabled: bool = True
    provider: str = "openai"
    model: str = "qwen3-vl-plus"
    api_key_env: str = "DASHSCOPE_API_KEY"
    base_url: str | None = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    timeout_s: float = Field(default=60.0, gt=0)
    max_tokens: int = Field(default=1000, gt=0)
    max_retries: int = Field(default=2, ge=0)
    max_image_bytes: int = Field(default=1_500_000, gt=0)
    # D25：VLM 独立退避/熔断（与 LLM 端点隔离：一方故障不影响另一方）
    retry: RetryConfig = RetryConfig()
    circuit_breaker: CircuitBreakerConfig = CircuitBreakerConfig()


class EmbeddingConfig(_StrictModel):
    """Embedding 配置（知识库语义化的向量来源）。

    - method：向量化实现。`ngram`=本地确定性向量（v1，零网络/零费用/<10ms，已实现）；
      `fastembed`=本地模型（v2，**预留未实现**）。此前是裸 `str`，写成 `fastembed` 能通过校验，
      而 `get_embedding_for_settings` 只返回 None → 语义检索（L3：知识库向量召回）**静默失效**，只表现为
      "知识库命中率下降"。现在：Literal 挡住拼写错误，未实现的取值在**加载期**直接报错
      （选"报错"而非 warning：配了却跑不起来属死配置，不允许静默失败）；
      v2 真正落地时把 fastembed 从下面的拒绝分支移入实现分支即可。
    - 绝不在热路径调用 API text-embedding（延迟 + 成本）。
    """

    enabled: bool = True
    method: Literal["ngram", "fastembed"] = "ngram"
    dim: int = 512

    @model_validator(mode="after")
    def _validate_dim(self) -> EmbeddingConfig:
        """dim 必须为正整数：0 在 NgramEmbedding 取模处除零崩溃，负数产生负索引错向量。"""
        if self.dim < 1:
            raise ValueError(f"embedding.dim 必须为正整数: {self.dim}")
        return self

    @model_validator(mode="after")
    def _validate_method(self) -> EmbeddingConfig:
        """未实现的 method 在加载期报错，不留"配置生效但能力静默缺失"的状态。"""
        if self.method not in _IMPLEMENTED_EMBEDDING_METHODS:
            raise ValueError(
                f"embedding.method={self.method!r} 尚未实现（当前可用: "
                f"{sorted(_IMPLEMENTED_EMBEDDING_METHODS)}）——配成未实现方法会让语义检索"
                "静默失效，故直接拒绝；需要本地模型向量请等 v2（见 llm/embedding.py 升级路径）"
            )
        return self


class SutConfig(_StrictModel):
    """被测系统（system under test）配置。

    - base_url: 前端地址（UI 自动化打开的入口，jshERP 前端独立部署于 3001 端口）。
    - api_base_url: 后端 API 地址——**非被测对象**，仅用于测试数据造数/清理。
    - username_env / password_env: **租户账号**凭证的环境变量名（与 LLMConfig.api_key_env
      同款参数化约定，明文绝不入库）。

    角色语义：
    - **租户 = 业务数据的管理员**（如测试租户 jsh）——UI 测试与 API 造数/清理都应使用
      租户账号（业务数据的合法所有者）；
    - **admin = 平台运维用户**——仅能配置平台菜单/创建租户，**不能编辑任何业务数据**，
      测试基建不得使用。
    """

    name: str = "jshERP"
    base_url: str = "http://localhost:3001"
    # 占位默认（可移植）；实际后端地址（如内网 IP）属环境特定配置，放 gitignore 的
    # `config/settings.yaml`——内网拓扑不应进代码默认值 / 提交 git。
    api_base_url: str = "http://localhost:9999/jshERP-boot"
    username_env: str = "ERP_USERNAME"
    password_env: str = "ERP_PASSWORD"


class Settings(_StrictModel):
    """顶层配置模型。

    extra='forbid'：yaml 里有但 Settings 未建模的字段会在加载期报错，
    而非被 pydantic 静默丢弃，杜绝"死配置"漂移。execution / reporting 段
    **有意不建模**（无对应运行时需求），配置示例中也不得出现相关键。
    """

    # extra='forbid' 继承自 _StrictModel，由顶层统一下发到全部子模型
    browser: BrowserConfig = BrowserConfig()
    llm: LLMConfig = LLMConfig()
    healing: HealingConfig = HealingConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()
    vision: VisionConfig = VisionConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    sut: SutConfig = SutConfig()  # 被测系统（jshERP）


def _resolve_relative(raw: str) -> str:
    """把已知路径字段的**相对**取值解析到 `PROJECT_ROOT`；绝对路径原样返回。

    - 空串 / 空白：原样返回（空值由消费方判断，不在这里编造路径）；
    - `~` 展开后再判断绝对性（`Path.is_absolute()` 不认 `~`）；
    - 解析失败（非法路径）抛 ``ValueError`` 并带上原值：宁可加载失败，也不返回一个
      看似正常却指向 CWD 的路径（避免静默失败）。
    """
    if not raw or not raw.strip():
        return raw
    try:
        candidate = Path(raw.strip()).expanduser()
        if candidate.is_absolute():
            return str(candidate)
        return str((PROJECT_ROOT / candidate).resolve())
    except (OSError, ValueError) as exc:
        raise ValueError(f"配置路径无法解析为绝对路径: {raw!r}（项目根 {PROJECT_ROOT}）") from exc


def _resolve_known_paths(settings: Settings) -> Settings:
    """就地归一 `_PATH_FIELDS` 声明的相对路径，返回同一实例便于链式使用。"""
    for section_name, field_name in _PATH_FIELDS:
        section = getattr(settings, section_name)
        setattr(section, field_name, _resolve_relative(getattr(section, field_name)))
    return settings


def load_settings(path: Path | str = DEFAULT_CONFIG_PATH) -> Settings:
    """加载并校验配置；文件不存在时返回默认配置（便于骨架阶段运行）。

    相对路径解析：`browser.trace_dir`、`knowledge.path` 这类**指向仓库内产物**的
    路径，若配置里写的是相对路径，则在此统一以 `PROJECT_ROOT`（仓库根，由本文件位置
    推导）解析为绝对路径；显式绝对路径不做改动。

    为什么在加载期做：此前相对路径由各消费方按**进程当前工作目录（CWD）**解析，
    于是"在项目根跑"与"在 tests/ 下跑"会各自生成一份 `reports/traces` 与
    `.cache/knowledge.db`——知识库分叉后跨目录命中率骤降，且现象隐蔽（像是"没学到"）。
    对显式传参的构造（如 `Settings()` 的类默认值）不做隐式改造：那里没有"项目根"语境，
    保持字面值最不意外；需要绝对路径请走本函数。
    """
    path = Path(path)
    if not path.exists():
        return _resolve_known_paths(Settings())
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return _resolve_known_paths(Settings.model_validate(raw))
