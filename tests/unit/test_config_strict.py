"""配置严格性测试（2026-09-03 子代理复核 V4）：extra=forbid 全层下发 + 阈值/dim 校验。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from selfheal.config import EmbeddingConfig, HealingConfig, Settings, load_settings

pytestmark = pytest.mark.unit

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "settings.example.yaml"


def test_example_settings_yaml_loads():
    """配置示例文件必须始终通过严格校验（防文档与模型漂移）。"""
    assert isinstance(load_settings(EXAMPLE), Settings)


def test_nested_unknown_key_rejected():
    """V4 复核：嵌套段（healing:）里的拼错/多余键必须报错，而非被静默丢弃。"""
    with pytest.raises(ValidationError):
        Settings.model_validate({"healing": {"dryrun": True}})


def test_top_level_unknown_key_rejected():
    """#16/T9 既有行为：顶层未建模字段报错（execution/reporting 段不得出现）。"""
    with pytest.raises(ValidationError):
        Settings.model_validate({"execution": {"timeout": 1}})


def test_global_threshold_out_of_range_rejected():
    """V4 复核：全局三阈值补 [0,1] 值域校验（负数=全采纳垃圾修复，>1=永不采纳）。"""
    for field in ("confidence_threshold", "early_accept_threshold", "llm_diagnose_threshold"):
        with pytest.raises(ValidationError):
            HealingConfig(**{field: -0.1})
        with pytest.raises(ValidationError):
            HealingConfig(**{field: 1.1})


def test_embedding_dim_must_be_positive():
    """V4 复核：dim 非正整数在加载期拒绝（0 → NgramEmbedding 取模除零，负数 → 负索引错向量）。"""
    with pytest.raises(ValidationError):
        EmbeddingConfig(dim=0)
    with pytest.raises(ValidationError):
        EmbeddingConfig(dim=-3)
    assert EmbeddingConfig(dim=1).dim == 1
