"""集中式配置加载与校验。

所有运行时配置统一由本模块用 pydantic 从 config/settings.yaml 加载，
业务代码不得散落读取环境变量或硬编码配置。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"


class BrowserConfig(BaseModel):
    """浏览器配置。

    channel: 传给 launch 的浏览器渠道。"chrome" 使用系统已装的 Chrome（无需下载 Chromium），
    "chromium" 使用 Playwright 自带内核（需 playwright install chromium）。
    """

    headless: bool = True
    channel: str = "chrome"
    slow_mo: int = 0
    viewport: dict[str, int] = {"width": 1280, "height": 800}


class LLMConfig(BaseModel):
    """LLM 配置。

    - enabled: 模型能力总开关。False 或缺少 API key 时，诊断退回规则式、语义策略被跳过（等价 Phase 1）。
    - provider / model / base_url: OpenAI 兼容接口，可指向 OpenAI / DeepSeek / Qwen / 智谱等。
    - api_key_env: 从环境变量名读取密钥（如 OPENAI_API_KEY），不写明文。
    """

    enabled: bool = True
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str | None = None
    temperature: float = 0.0


class HealingConfig(BaseModel):
    """自愈行为配置。

    - enabled: 插件总开关。False 时 HealingPage 透传为原生行为，不触发任何修复。
    - on_uncertain: AI 不确定（置信度 < confidence_threshold）时的兜底策略（见 RULE.md 决策 D6）。
      use_fallback=用人工备用定位器（默认，CI 友好）；pause=交互模式下暂停等人工；fail=快速失败。
    """

    enabled: bool = True
    strategy_order: list[str] = ["heuristic", "semantic", "visual"]
    knowledge_first: bool = True
    confidence_threshold: float = 0.6
    on_uncertain: Literal["use_fallback", "pause", "fail"] = "use_fallback"


class Settings(BaseModel):
    """顶层配置模型。TODO: 补全 execution / vision / knowledge / reporting 子模型。"""

    browser: BrowserConfig = BrowserConfig()
    llm: LLMConfig = LLMConfig()
    healing: HealingConfig = HealingConfig()


def load_settings(path: Path | str = DEFAULT_CONFIG_PATH) -> Settings:
    """加载并校验配置；文件不存在时返回默认配置（便于骨架阶段运行）。"""
    path = Path(path)
    if not path.exists():
        return Settings()
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Settings.model_validate(raw)
