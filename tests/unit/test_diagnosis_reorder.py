"""单元测试：P2 诊断与成本收敛 —— 诊断后置 / C7 低置信归因 / B2 成本计数 / B4 L1 门控（不触网）。"""

import pytest

import selfheal.agent.orchestrator as orch_mod
from selfheal.agent.dom import ElementContext, compute_repair_key
from selfheal.agent.orchestrator import HealOutcome, SelfHealOrchestrator
from selfheal.agent.strategies.base import RepairCandidate
from selfheal.collect.collector import Scene
from selfheal.config import HealingConfig, Settings
from selfheal.knowledge.schema import RepairCase
from selfheal.knowledge.store import KnowledgeStore

pytestmark = pytest.mark.unit

LOGIN_DOM = '<html><body><button id="login">登录</button></body></html>'


class _FakeCollector:
    """注入自定义 Scene（unit 无浏览器，绕开 SceneCollector 的空采集）。"""

    def __init__(self, url: str, dom: str | None = None):
        self._url = url
        self._dom = dom

    def capture(self) -> Scene:
        return Scene(url=self._url, dom_snapshot=self._dom)


class _RecLLM:
    """记录 chat 调用次数的假 LLM；回复一个白名单内的诊断 JSON。"""

    def __init__(self):
        self.chat_calls = 0

    def chat(self, messages, **kwargs):
        self.chat_calls += 1
        return '{"root_cause": "not_visible", "reason": "被弹窗遮挡"}'

    def complete(self, prompt, **kwargs):
        return self.chat([], **kwargs)


class _FixedStrategy:
    """返回类属性 candidate 的假策略（注册进 _STRATEGY_REGISTRY，_build_strategy 无参实例化）。"""

    name = "fixed"
    candidate = None

    def repair(self, scene, selector, description):
        return type(self).candidate


def _orch(settings, llm=None, store=None, url="https://x/login", dom=LOGIN_DOM):
    if llm is None:
        settings.llm.enabled = False  # 隔离本机 .env 的 API key，避免无 client 测试误触真实 LLM
    orch = SelfHealOrchestrator(
        page=None, settings=settings, knowledge=store or KnowledgeStore(), llm_client=llm
    )
    orch._collector = _FakeCollector(url, dom)
    return orch


def _use_fixed_strategy(monkeypatch, candidate):
    monkeypatch.setattr(_FixedStrategy, "candidate", candidate)
    monkeypatch.setitem(orch_mod._STRATEGY_REGISTRY, "fixed", _FixedStrategy)


# --- 诊断后置（C3）：高置信成功不触发 LLM，失败/低置信才触发 ---


def test_high_confidence_success_skips_llm_diagnosis(monkeypatch):
    llm = _RecLLM()
    settings = Settings()
    settings.healing.strategy_order = ["fixed"]
    _use_fixed_strategy(monkeypatch, RepairCandidate(selector="#ok", confidence=0.9, strategy="heuristic"))
    orch = _orch(settings, llm=llm)
    outcome = orch.run("#submit-btn", description="登录")
    assert outcome.success is True
    assert llm.chat_calls == 0  # 高置信成功：规则式归因即可，省一次 LLM 调用
    assert orch._reporter.stats.get("llm_diagnoses", 0) == 0
    assert outcome.root_cause == "not_found"  # 规则式归因保留（selector 主键不在 DOM）


def test_low_confidence_success_triggers_llm_diagnosis(monkeypatch):
    """C7：置信度落在 [threshold, llm_diagnose_threshold) → 补 LLM 归因，丰富审计。"""
    llm = _RecLLM()
    settings = Settings()
    settings.healing.strategy_order = ["fixed"]
    _use_fixed_strategy(monkeypatch, RepairCandidate(selector="#ok", confidence=0.62, strategy="heuristic"))
    orch = _orch(settings, llm=llm)
    outcome = orch.run("#submit-btn", description="登录")
    assert outcome.success is True
    assert llm.chat_calls == 1
    assert orch._reporter.stats.get("llm_diagnoses", 0) == 1
    assert orch._reporter.stats.get("llm_calls", 0) >= 1  # B2：诊断调用计入成本
    assert outcome.root_cause == "not_visible"  # LLM 归因覆盖规则式


def test_strategy_failure_triggers_llm_diagnosis(monkeypatch):
    """策略链全失败（无候选）→ LLM 深挖归因（诊断后置）。"""
    llm = _RecLLM()
    settings = Settings()
    settings.healing.strategy_order = ["fixed"]
    _use_fixed_strategy(monkeypatch, None)
    orch = _orch(settings, llm=llm)
    outcome = orch.run("#submit-btn", description="登录")
    assert outcome.success is False
    assert llm.chat_calls == 1
    assert orch._reporter.stats.get("llm_diagnoses", 0) == 1
    assert outcome.root_cause == "not_visible"


def test_below_threshold_candidate_emits_human_review_proposal(monkeypatch):
    """有候选但置信度不足：LLM 归因 + 写 fix-proposal 供人审。"""
    llm = _RecLLM()
    settings = Settings()
    settings.healing.strategy_order = ["fixed"]
    _use_fixed_strategy(monkeypatch, RepairCandidate(selector="#maybe", confidence=0.3, strategy="heuristic"))
    orch = _orch(settings, llm=llm)
    emitted = []
    orch._emit_proposal = lambda *a, **k: emitted.append(a)  # 拦截写文件副作用
    outcome = orch.run("#submit-btn", description="登录")
    assert outcome.success is False
    assert llm.chat_calls == 1
    assert len(emitted) == 1  # 低置信候选 → 人审建议


def test_no_llm_falls_back_to_rule_only(monkeypatch):
    """LLM 不可用（无 client）→ 全程规则式，不崩溃、无计数。"""
    settings = Settings()
    settings.healing.strategy_order = ["fixed"]
    _use_fixed_strategy(monkeypatch, None)
    orch = _orch(settings, llm=None)
    outcome = orch.run("#submit-btn", description="登录")
    assert outcome.success is False
    assert outcome.root_cause in ("not_found", "unknown")
    assert orch._reporter.stats.get("llm_diagnoses", 0) == 0


# --- 配置阈值（C7）---


def test_llm_diagnose_threshold_must_exceed_confidence():
    with pytest.raises(ValueError):
        HealingConfig(confidence_threshold=0.6, llm_diagnose_threshold=0.5)
    assert HealingConfig().llm_diagnose_threshold == 0.75


# --- B4：L1 键写入不依赖 embedding ---


def test_persist_writes_l1_key_without_embedding():
    """B4：embedding 关闭时 L1 键仍写入 → 后续同结构 L1 硬短路（读写门控对称化）。"""
    settings = Settings()
    settings.embedding.enabled = False  # embedding 不可用
    store = KnowledgeStore()
    orch = SelfHealOrchestrator(page=None, settings=settings, knowledge=store)
    scene = Scene(url="https://x/login", dom_snapshot=LOGIN_DOM)
    ctx = ElementContext(
        text="登录",
        tag_path="html:nth-of-type(1)>body:nth-of-type(1)>button:nth-of-type(1)",
        source="snapshot",
    )
    orch._persist(
        scene,
        "#old",
        HealOutcome(success=True, new_selector="#ok", confidence=0.9, strategy="heuristic"),
        "fp",
        "pg1",
        ctx,
    )
    case = store._repairs[0]
    assert case.repair_key is not None  # B4：L1 键写入不依赖 embedding
    assert case.embedding is None  # embedding 缺失 → 无向量（L3 自然不命中）
    assert case.embedding_version is None
    # 同结构上下文再次查询 → L1 命中（无需 embedding）
    outcome = orch._lookup_knowledge(scene, "#old", "fp", "pg1", ctx)
    assert outcome is not None and outcome.success
    assert outcome.root_cause == "cached_l1"


def test_static_context_skips_l1_key_avoiding_collision():
    """空 tag_path（静态兜底上下文）不产生 L1 键：防同页所有静态失败折叠成同一键（跨用例误命中）。"""
    store = KnowledgeStore()
    # 模拟若不加门控会被写入的"退化键"（同页所有静态失败共享 md5(page_fp|"")）
    store.add_repair(
        RepairCase(
            original_selector="#a",
            new_selector="#a-new",
            strategy="heuristic",
            confidence=0.9,
            page_url="u",
            repair_key=compute_repair_key("pg1", ""),
        )
    )
    orch = SelfHealOrchestrator(page=None, settings=Settings(), knowledge=store)
    # 另一 selector 的静态上下文查询：L1 跳过（tag_path 空）→ 旧式检索也无 #b → 不得命中 #a 的修复
    static = ElementContext(text="x", source="static")
    assert orch._lookup_knowledge(Scene(url="u"), "#b", "fp", "pg1", static) is None
