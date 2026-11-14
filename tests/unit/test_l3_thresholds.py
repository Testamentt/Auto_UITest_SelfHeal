# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""单元测试：L3 语义检索阈值外置（不依赖浏览器 / 网络）。

回归的问题：L3 的 4 个阈值（min_sim / fresh_sim / verified_sim / fresh_days）此前是
`agent/strategies/semantic.py` 的模块级常量，按被测系统标定就要改代码、发版。

本文件锁定：
1. 配置默认值 == 原常量（零回归）；
2. 层级校验拒绝倒挂（min <= fresh <= verified），加载期报错而非运行时静默；
3. `L3Thresholds.from_config` 正确映射；`None` 回退默认；
4. 阈值真的驱动行为：调高 min_sim 后原本能检索的相似度不再进入检索；
5. 改 verified_sim / fresh_sim 后，采纳与否随配置变化（而非常量写死）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from selfheal.agent.strategies.semantic import (
    L3_FRESH_DAYS,
    L3_FRESH_SIM,
    L3_MIN_SIM,
    L3_VERIFIED_SIM,
    L3Thresholds,
    SemanticStrategy,
    _is_fresh,
)
from selfheal.collect.collector import Scene
from selfheal.config import Settings
from selfheal.dom import ElementContext
from selfheal.knowledge.schema import RepairCase
from selfheal.knowledge.store import KnowledgeStore
from selfheal.llm.base import LLMClient
from selfheal.llm.embedding import NgramEmbedding

pytestmark = pytest.mark.unit


class _FakeLLM(LLMClient):
    """永远不会被调用的假 LLM（语义段走知识库命中时不应外呼）。"""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, **kwargs):  # noqa: ANN001 - 测试假实现
        self.calls += 1
        return "{}"


def _cfg(**kwargs):
    """构造 SemanticKnowledgeConfig，未给字段用默认。"""
    return Settings().knowledge.semantic.model_copy(update=kwargs)


def _case(*, verified: bool = False, created_at: str | None = None) -> RepairCase:
    return RepairCase(
        original_selector="#old",
        new_selector="#new",
        strategy="heuristic",
        confidence=0.9,
        page_url="demo://page",
        page_fingerprint="fp",
        repair_key="rk-1",
        is_verified=verified,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# 1. 默认值与零回归
# ---------------------------------------------------------------------------


def test_config_defaults_match_module_constants():
    """配置默认值必须与原有模块常量逐值相同（零回归）。"""
    cfg = Settings().knowledge.semantic
    assert cfg.min_sim == L3_MIN_SIM
    assert cfg.verified_sim == L3_VERIFIED_SIM
    assert cfg.fresh_sim == L3_FRESH_SIM
    assert cfg.fresh_days == L3_FRESH_DAYS


def test_thresholds_default_equals_constants():
    t = L3Thresholds()
    assert (t.min_sim, t.verified_sim, t.fresh_sim, t.fresh_days) == (
        L3_MIN_SIM,
        L3_VERIFIED_SIM,
        L3_FRESH_SIM,
        L3_FRESH_DAYS,
    )


def test_from_config_maps_all_fields():
    cfg = _cfg(min_sim=0.5, verified_sim=0.95, fresh_sim=0.7, fresh_days=30)
    t = L3Thresholds.from_config(cfg)
    assert (t.min_sim, t.verified_sim, t.fresh_sim, t.fresh_days) == (0.5, 0.95, 0.7, 30)


def test_from_config_none_falls_back_to_defaults():
    t = L3Thresholds.from_config(None)
    assert t == L3Thresholds()


# ---------------------------------------------------------------------------
# 2. 层级校验（防倒挂）
# ---------------------------------------------------------------------------


def test_config_rejects_min_sim_above_fresh_sim():
    """进入检索的门槛高于自动采纳门槛 → 后者永远不可达，属配置错误。"""
    with pytest.raises(ValidationError, match="min_sim"):
        Settings.model_validate({"knowledge": {"semantic": {"min_sim": 0.9, "fresh_sim": 0.8}}})


def test_config_rejects_fresh_sim_above_verified_sim():
    """未人审的案例不应比已人审的更容易被采纳。"""
    with pytest.raises(ValidationError, match="fresh_sim"):
        Settings.model_validate(
            {"knowledge": {"semantic": {"fresh_sim": 0.95, "verified_sim": 0.9}}}
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"min_sim": -0.1},
        {"min_sim": 1.1},
        {"fresh_days": -1},
        {"unknown_key": 1},
    ],
)
def test_config_rejects_invalid_values(payload):
    with pytest.raises(ValidationError):
        Settings.model_validate({"knowledge": {"semantic": payload}})


def test_config_accepts_equal_boundaries():
    """相等是允许的（min == fresh == verified 表示统一门槛）。"""
    cfg = Settings.model_validate(
        {"knowledge": {"semantic": {"min_sim": 0.8, "fresh_sim": 0.8, "verified_sim": 0.8}}}
    )
    assert cfg.knowledge.semantic.min_sim == 0.8


# ---------------------------------------------------------------------------
# 3. 阈值真的驱动行为
# ---------------------------------------------------------------------------


def _semantic_with_thresholds(thresholds: L3Thresholds, *, sim_case: RepairCase):
    """构造一个"知识库里有 1 条案例"的语义策略；返回 (策略, 知识库)。

    案例的向量与查询上下文用**同一段文本**生成，使相似度为 1.0（self-match），
    从而"阈值是否生效"成为唯一变量。
    """
    text = "登录按钮"
    embedding = NgramEmbedding(dim=64)
    knowledge = KnowledgeStore()
    knowledge.add_repair(
        RepairCase(
            original_selector=sim_case.original_selector,
            new_selector=sim_case.new_selector,
            strategy=sim_case.strategy,
            confidence=sim_case.confidence,
            page_url=sim_case.page_url,
            page_fingerprint=sim_case.page_fingerprint,
            repair_key=sim_case.repair_key,
            embedding=embedding.embed(text),
            embedding_version=embedding.embedding_version,
            is_verified=sim_case.is_verified,
            created_at=sim_case.created_at,
        )
    )
    strategy = SemanticStrategy(
        client=_FakeLLM(),
        knowledge=knowledge,
        embedding=embedding,
        page_fingerprint="fp",
        element_context=ElementContext(text=text),
        page=None,  # 无页面 → 候选视为可用（selector_exists 契约）
        thresholds=thresholds,
    )
    return strategy, knowledge


def _scene() -> Scene:
    return Scene(url="demo://page", dom_snapshot="<html><body><button>登录</button></body></html>")


def test_high_min_sim_blocks_retrieval():
    """把 min_sim 抬到 1.0 以上不可能达到 → 检索被空过（证明阈值真的生效）。"""
    case = _case(verified=True)
    strategy, _ = _semantic_with_thresholds(
        L3Thresholds(min_sim=1.0, fresh_sim=1.0, verified_sim=1.0), sim_case=case
    )
    # 相似度不可能 > 1.0 → 无候选被采纳
    assert strategy._knowledge_semantic("#old") is None


def test_default_thresholds_allow_self_similar_match():
    """默认阈值下，与查询文本完全一致的案例应能命中（证明检索本身可用）。"""
    case = _case(verified=True)
    strategy, _ = _semantic_with_thresholds(L3Thresholds(), sim_case=case)
    hit = strategy._knowledge_semantic("#old")
    assert hit is not None
    assert hit.selector == "#new"


def test_accept_uses_configured_verified_sim():
    """已人审案例：采纳阈值来自配置而非写死常量。"""
    case = _case(verified=True)
    # sim 取 0.9：默认 verified_sim=0.92 不采纳；配置降到 0.85 则采纳
    assert (
        SemanticStrategy._accept(
            SemanticStrategy(thresholds=L3Thresholds(verified_sim=0.92)), case, 0.90
        )
        is False
    )
    assert (
        SemanticStrategy._accept(
            SemanticStrategy(thresholds=L3Thresholds(verified_sim=0.85)), case, 0.90
        )
        is True
    )


def test_accept_uses_configured_fresh_sim():
    """未人审案例：走新鲜窗口分支，阈值与窗口天数都来自配置。"""
    fresh_case = _case(verified=False, created_at=_now_iso())
    # 默认 fresh_sim=0.80：sim 0.82 采纳
    assert (
        SemanticStrategy._accept(SemanticStrategy(thresholds=L3Thresholds()), fresh_case, 0.82)
        is True
    )
    # 配置抬到 0.85：同一 sim 不再采纳
    assert (
        SemanticStrategy._accept(
            SemanticStrategy(thresholds=L3Thresholds(fresh_sim=0.85)), fresh_case, 0.82
        )
        is False
    )


def test_accept_respects_configured_fresh_days():
    """新鲜窗口天数来自配置：0 天窗口下"刚创建"的案例也过期（保守）。"""
    fresh_case = _case(verified=False, created_at=_now_iso())
    assert (
        SemanticStrategy._accept(
            SemanticStrategy(thresholds=L3Thresholds(fresh_days=0)), fresh_case, 0.99
        )
        is False
    )


def _now_iso() -> str:
    from selfheal.knowledge.base import utc_now_iso

    return utc_now_iso()


def test_is_fresh_default_arg_still_bound_to_constant():
    """`_is_fresh` 的默认参数保持与模块常量一致（外部直调时行为不变）。"""
    assert _is_fresh(_now_iso()) is True
    assert _is_fresh(None) is False
