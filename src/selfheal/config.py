"""集中式配置加载与校验。

所有运行时配置统一由本模块用 pydantic 从 config/settings.yaml 加载，
业务代码不得散落读取环境变量或硬编码配置。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"

# 加载本地 .env（已被 .gitignore 忽略，绝不提交）；不覆盖已存在的同名环境变量。
# 未安装 python-dotenv 时静默跳过（此时仅从系统环境变量读取密钥）。
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:  # pragma: no cover - 取决于是否安装 python-dotenv
    pass


class _StrictModel(BaseModel):
    """配置模型基类：extra='forbid'（V4 复核：由顶层下发到全部子模型）。

    此前只有顶层 Settings 拒绝未建模字段，嵌套段（如 healing:）里拼错/多余的键
    被 pydantic 默认静默丢弃（extra='ignore'）——恰是"死配置"漂移的温床（如
    dry_run 安全开关拼错后静默失效）。统一基类后，任何层级出现未建模键都在加载期报错。
    """

    model_config = ConfigDict(extra="forbid")


class BrowserConfig(_StrictModel):
    """浏览器配置。

    - channel: 传给 launch 的浏览器渠道。"chrome" 使用系统已装的 Chrome（无需下载 Chromium），
      "chromium" 使用 Playwright 自带内核（需 playwright install chromium）。
    - trace: 是否录制 Playwright trace（Phase 4 视频回放选型），用 `playwright show-trace` 回放。
    - trace_dir: trace .zip 输出目录。
    """

    headless: bool = True
    channel: str = "chrome"
    slow_mo: int = 0
    viewport: dict[str, int] = {"width": 1280, "height": 800}
    trace: bool = False
    trace_dir: str = "reports/traces"


class LLMConfig(_StrictModel):
    """LLM 配置。

    - enabled: 模型能力总开关。False 或缺少 API key 时，诊断退回规则式、语义策略被跳过（等价 Phase 1）。
    - provider / model / base_url: OpenAI 兼容接口，可指向 OpenAI / DeepSeek / Qwen / 智谱等。
    - api_key_env: 从环境变量名读取密钥（如 OPENAI_API_KEY），不写明文。
    """

    enabled: bool = True
    provider: str = "openai"
    model: str = "deepseek-v4-flash"
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str | None = "https://api.deepseek.com"
    temperature: float = 0.0


class ActionWaitConfig(_StrictModel):
    """动作前置智能等待（T6）。

    - enabled: 动作（click/fill 等 HEALABLE）执行前是否先做一次短稳等待。
      **默认 False**——保持"POM 显式调用 wait_until_stable"的既有行为（决策 D12：
      不改变默认动作语义），零侵入；需要"默认融入"的团队开启即可。
    - timeout_ms / stable_ms: 短稳等待参数，复用 engine/smart_wait.py 的 wait_until_stable。
      等待是增强而非正确性前提：失败仅记 debug、不阻塞动作（避免把等待变成新的超时源）。
    """

    enabled: bool = False
    timeout_ms: int = 2000
    stable_ms: int = 300


class HealingConfig(_StrictModel):
    """自愈行为配置。

    - enabled: 插件总开关。False 时 HealingPage 透传为原生行为，不触发任何修复。
    - on_uncertain: AI 不确定（置信度 < confidence_threshold）时的兜底策略（见 RULE.md 决策 D6）。
      use_fallback=用人工备用定位器（默认，CI 友好）；pause=交互模式下暂停等人工；fail=快速失败。
    - early_accept_threshold: "早接受"阈值（T1 策略短路）。某策略置信度达到该值即立即采纳，
      不再尝试后续（更贵的）策略，省 LLM/VLM 调用。应 > confidence_threshold。
    - exclude_url_patterns（T13）: 高风险页豁免——URL 匹配任意 glob 模式则不触发自愈
      （支付 / 强授权 / 审计类页面误改代价高，通常不自愈）。如 ["*://*/pay*", "*://*/admin*"]。
    - dry_run（T14）: 仅报告不执行——只生成修复建议（写 fix-proposals），不实际换定位器重试、
      不持久化知识，供人审后手动采纳。
    - fix_proposals（T15）: 修复成功后输出「原→新」PR 化建议清单（Markdown/JSON，不自动改库），
      人确认后才合入代码。默认关闭；开启后每次成功自愈追加一条建议。
    - llm_diagnose_threshold（C7）: LLM 归因触发阈值。低于该值的"成功"修复（置信度落在
      [confidence_threshold, llm_diagnose_threshold) 区间）也补一次 LLM 诊断，丰富审计与人审清单；
      应 > confidence_threshold。策略链全失败时无条件触发 LLM 归因（不依赖本阈值）。
    - strategy_thresholds / strategy_early_accept（T5）: 按策略独立阈值。键为策略名
      （heuristic / semantic / visual，可插拔注册表），缺省回退全局 confidence_threshold /
      early_accept_threshold。未知名键宽待（回退全局），便于先调配置再看行为。
    - shrink_self_reported（T5）: 置信度归一化的可选保守收缩。开启后对自报型策略
      （LLM 语义自报段）做 raw² 收缩，缓解自报虚高被直接采纳；默认关闭（行为与 T4 后
      完全一致），待真实模型校准数据沉淀后再启用（见 agent/confidence.py）。
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
    action_wait: ActionWaitConfig = ActionWaitConfig()  # T6：动作前置智能等待（默认关闭）

    def accept_threshold(self, strategy: str) -> float:
        """该策略的采纳阈值（T5）：strategy_thresholds 命中则用之，否则回退全局。"""
        return self.strategy_thresholds.get(strategy, self.confidence_threshold)

    def early_accept_for(self, strategy: str) -> float:
        """该策略的"早接受"短路阈值（T5/T1）：strategy_early_accept 命中则用之，否则回退全局。"""
        return self.strategy_early_accept.get(strategy, self.early_accept_threshold)

    @model_validator(mode="after")
    def _validate_thresholds(self) -> HealingConfig:
        """阈值层级约束：值域 [0,1]、early_accept > confidence、llm_diagnose > confidence、按策略同构（防配置倒挂）。"""
        # V4 复核：全局三阈值补 [0,1] 值域校验——confidence_threshold 配成负数会使所有
        # 候选（含 0 置信度垃圾修复）被无条件采纳，>1 则自愈永不采纳，均为静默错误门控。
        for name in ("confidence_threshold", "early_accept_threshold", "llm_diagnose_threshold"):
            value = getattr(self, name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(f"{name} 超出 [0, 1] 值域: {value}")
        if self.early_accept_threshold <= self.confidence_threshold:
            raise ValueError("early_accept_threshold 应 > confidence_threshold")
        if self.llm_diagnose_threshold <= self.confidence_threshold:
            raise ValueError("llm_diagnose_threshold 应 > confidence_threshold")
        # T5：按策略阈值——值域 [0,1] 且同一策略的 early_accept 必须 > accept（防倒挂）
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


class KnowledgeConfig(_StrictModel):
    """知识库配置。

    - backend: memory=进程内（测试/临时）；sqlite=持久化（默认，重启后仍可命中复用）。
    - path: sqlite 数据库文件路径（.cache/ 已 gitignore）。
    """

    backend: Literal["memory", "sqlite"] = "sqlite"
    path: str = ".cache/knowledge.db"


class VisionConfig(_StrictModel):
    """视觉模型配置（VLM）。

    model=qwen3-vl-plus（备选 qwen3.8-flash，2026-09-01 用户确认）；key 从 api_key_env
    指定的环境变量读取（参数化，绝不硬编码 / 提交 git）。
    - base_url 默认为 **DashScope 公共 compatible-mode 端点**（可移植默认）；专属百炼 MaaS
      实例端点属环境特定配置，放 gitignore 的 `config/settings.yaml`（勿写进代码默认值）。
    - timeout_s / max_tokens: plus 级模型响应慢于 flash、视觉描述更长，默认放宽
      （2026-09-01：20s/500 曾导致 ERP 场景 VLM 调用超时/截断而自愈失败）。
    """

    enabled: bool = True
    provider: str = "openai"
    model: str = "qwen3-vl-plus"
    api_key_env: str = "DASHSCOPE_API_KEY"
    base_url: str | None = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    timeout_s: float = 60.0
    max_tokens: int = 1000


class EmbeddingConfig(_StrictModel):
    """Embedding 配置（知识库语义化 A）。

    - method: ngram=本地确定性向量（v1，零网络/零费用/<10ms）；fastembed=本地模型（v2，见文档）。
    - 绝不在热路径调用 API text-embedding（延迟 + 成本）。
    """

    enabled: bool = True
    method: str = "ngram"
    dim: int = 512

    @model_validator(mode="after")
    def _validate_dim(self) -> EmbeddingConfig:
        """dim 必须为正整数（V4 复核）：0 在 NgramEmbedding 取模处除零崩溃，负数产生负索引错向量。"""
        if self.dim < 1:
            raise ValueError(f"embedding.dim 必须为正整数: {self.dim}")
        return self


class SutConfig(_StrictModel):
    """被测系统（system under test）配置（T23：管伊佳 ERP 迁移）。

    - base_url: 前端地址（UI 自动化打开的入口，jshERP 前端独立部署于 3001 端口）。
    - api_base_url: 后端 API 地址——**非被测对象**，仅用于测试数据造数/清理（勘测确认）。
    - username_env / password_env: **租户账号**凭证的环境变量名（与 LLMConfig.api_key_env
      同款参数化约定，明文绝不入库）。

    角色语义（专家确认，2026-09-01）：
    - **租户 = 业务数据的管理员**（如测试租户 jsh）——UI 测试与 API 造数/清理都应使用
      租户账号（业务数据的合法所有者）；
    - **admin = 平台运维用户**——仅能配置平台菜单/创建租户，**不能编辑任何业务数据**，
      测试基建不得使用（此前误用 admin 造数已纠正）。
    """

    name: str = "jshERP"
    base_url: str = "http://localhost:3001"
    # 占位默认（可移植）；实际后端地址（如内网 IP）属环境特定配置，放 gitignore 的
    # `config/settings.yaml`——内网拓扑不应进代码默认值 / 提交 git（2026-09-03 评审 R3）。
    api_base_url: str = "http://localhost:9999/jshERP-boot"
    username_env: str = "ERP_USERNAME"
    password_env: str = "ERP_PASSWORD"


class Settings(_StrictModel):
    """顶层配置模型。

    extra='forbid'（#16/T9）：yaml 里有但 Settings 未建模的字段会在加载期报错，
    而非被 pydantic 静默丢弃，杜绝"死配置"漂移。execution / reporting 段经 T9
    决策**不建模**（无对应运行时需求），配置示例中也不得出现相关键。
    """

    # extra='forbid' 继承自 _StrictModel（#16/T9 → V4 复核全层下发）
    browser: BrowserConfig = BrowserConfig()
    llm: LLMConfig = LLMConfig()
    healing: HealingConfig = HealingConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()
    vision: VisionConfig = VisionConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    sut: SutConfig = SutConfig()  # T23：被测系统（管伊佳 ERP）


def load_settings(path: Path | str = DEFAULT_CONFIG_PATH) -> Settings:
    """加载并校验配置；文件不存在时返回默认配置（便于骨架阶段运行）。"""
    path = Path(path)
    if not path.exists():
        return Settings()
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Settings.model_validate(raw)
