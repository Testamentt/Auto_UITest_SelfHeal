"""集中式配置加载与校验。

所有运行时配置统一由本模块用 pydantic 从 config/settings.yaml 加载，
业务代码不得散落读取环境变量或硬编码配置。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"


class BrowserConfig(BaseModel):
    headless: bool = True
    channel: str = "chromium"
    slow_mo: int = 0
    viewport: dict[str, int] = {"width": 1280, "height": 800}


class LLMConfig(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str | None = None
    temperature: float = 0.0


class HealingConfig(BaseModel):
    strategy_order: list[str] = ["heuristic", "semantic", "visual"]
    knowledge_first: bool = True
    confidence_threshold: float = 0.6


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
