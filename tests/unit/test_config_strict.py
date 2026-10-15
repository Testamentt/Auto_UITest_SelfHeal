"""配置严格性测试（2026-09-03 子代理复核 V4）：extra=forbid 全层下发 + 阈值/dim 校验。

另含 2026-10-08 低优先级修复 L10 的用例：viewport 键名白名单、embedding.method 能力清单。
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from selfheal.config import BrowserConfig, EmbeddingConfig, HealingConfig, Settings, load_settings

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


# --- L10：viewport 键名白名单 + embedding.method 能力清单 ---


def test_viewport_default_and_valid_values_keep_dict_shape():
    """L10：默认视口与显式合法配置照常通过，且仍是**普通 dict**（engine 直接交给 new_context）。"""
    assert BrowserConfig().viewport == {"width": 1280, "height": 800}
    cfg = BrowserConfig(viewport={"width": 1920, "height": 1080})
    assert isinstance(cfg.viewport, dict)
    assert cfg.viewport == {"width": 1920, "height": 1080}
    assert Settings.model_validate(
        {"browser": {"viewport": {"width": 800, "height": 600}}}
    ).browser.viewport == {
        "width": 800,
        "height": 600,
    }


def test_viewport_typo_key_rejected():
    """L10 核心：`wdith` 这类拼错键必须报错——此前裸 dict 会静默放行，Playwright 退回默认视口。"""
    with pytest.raises(ValidationError, match="未知键"):
        BrowserConfig(viewport={"wdith": 1280, "height": 800})
    with pytest.raises(ValidationError, match="未知键"):
        Settings.model_validate({"browser": {"viewport": {"width": 1280, "hight": 800}}})


def test_viewport_missing_or_invalid_size_rejected():
    """L10：缺字段 / 非正尺寸 / bool 都在加载期拒绝（0 与负值会让 Playwright 直接报错或行为未定义）。"""
    for bad in (
        {"width": 1280},
        {"height": 800},
        {},
        {"width": 0, "height": 800},
        {"width": -1, "height": 8},
    ):
        with pytest.raises(ValidationError):
            BrowserConfig(viewport=bad)
    with pytest.raises(ValidationError):
        BrowserConfig(viewport={"width": "1280", "height": 800})
    with pytest.raises(ValidationError):
        BrowserConfig(viewport={"width": True, "height": 800})


def test_embedding_method_literal_rejects_unknown_spelling():
    """L10：method 收敛为 Literal，拼错（ngarm）加载期即报错，不再被当字符串放行。"""
    with pytest.raises(ValidationError):
        EmbeddingConfig(method="ngarm")
    with pytest.raises(ValidationError):
        Settings.model_validate({"embedding": {"method": "text-embedding-3"}})


def test_embedding_fastembed_rejected_until_implemented():
    """L10：fastembed 是预留值——Literal 放行但**加载期报错**（此前会让语义检索静默失效）。"""
    assert EmbeddingConfig(method="ngram").method == "ngram"
    with pytest.raises(ValidationError, match="尚未实现"):
        EmbeddingConfig(method="fastembed")
    with pytest.raises(ValidationError, match="尚未实现"):
        Settings.model_validate({"embedding": {"method": "fastembed"}})
