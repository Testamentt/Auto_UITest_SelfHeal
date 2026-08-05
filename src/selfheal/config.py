"""集中式配置加载与校验。

所有运行时配置统一由本模块用 pydantic 从 config/settings.yaml 加载，
业务代码不得散落读取环境变量或硬编码配置。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"

# 加载本地 .env（已被 .gitignore 忽略，绝不提交）；不覆盖已存在的同名环境变量。
# 未安装 python-dotenv 时静默跳过（此时仅从系统环境变量读取密钥）。
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:  # pragma: no cover - 取决于是否安装 python-dotenv
    pass


class BrowserConfig(BaseModel):
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


class LLMConfig(BaseModel):
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


class HealingConfig(BaseModel):
    """自愈行为配置。

    - enabled: 插件总开关。False 时 HealingPage 透传为原生行为，不触发任何修复。
    - on_uncertain: AI 不确定（置信度 < confidence_threshold）时的兜底策略（见 RULE.md 决策 D6）。
      use_fallback=用人工备用定位器（默认，CI 友好）；pause=交互模式下暂停等人工；fail=快速失败。
    - early_accept_threshold: "早接受"阈值（T1 策略短路）。某策略置信度达到该值即立即采纳，
      不再尝试后续（更贵的）策略，省 LLM/VLM 调用。应 > confidence_threshold。
    """

    enabled: bool = True
    strategy_order: list[str] = ["heuristic", "semantic", "visual"]
    knowledge_first: bool = True
    confidence_threshold: float = 0.6
    early_accept_threshold: float = 0.85
    on_uncertain: Literal["use_fallback", "pause", "fail"] = "use_fallback"


class KnowledgeConfig(BaseModel):
    """知识库配置。

    - backend: memory=进程内（测试/临时）；sqlite=持久化（默认，重启后仍可命中复用）。
    - path: sqlite 数据库文件路径（.cache/ 已 gitignore）。
    """

    backend: Literal["memory", "sqlite"] = "sqlite"
    path: str = ".cache/knowledge.db"


class VisionConfig(BaseModel):
    """视觉模型配置（VLM）。

    qwen3-vl-flash 走 DashScope 的 OpenAI 兼容端点；key 从 api_key_env 指定的
    环境变量读取（参数化，绝不硬编码 / 提交 git）。
    """

    enabled: bool = True
    provider: str = "openai"
    model: str = "qwen3-vl-flash"
    api_key_env: str = "DASHSCOPE_API_KEY"
    base_url: str | None = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class Settings(BaseModel):
    """顶层配置模型。TODO: 补全 execution / reporting 子模型。

    extra='forbid'（#16）：yaml 里有但 Settings 未建模的字段会在加载期报错，
    而非被 pydantic 静默丢弃，杜绝"死配置"漂移。
    """

    model_config = ConfigDict(extra="forbid")

    browser: BrowserConfig = BrowserConfig()
    llm: LLMConfig = LLMConfig()
    healing: HealingConfig = HealingConfig()
    knowledge: KnowledgeConfig = KnowledgeConfig()
    vision: VisionConfig = VisionConfig()


def load_settings(path: Path | str = DEFAULT_CONFIG_PATH) -> Settings:
    """加载并校验配置；文件不存在时返回默认配置（便于骨架阶段运行）。"""
    path = Path(path)
    if not path.exists():
        return Settings()
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Settings.model_validate(raw)
