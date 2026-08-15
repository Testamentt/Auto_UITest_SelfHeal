"""单元测试：A3 orchestrator 接线 —— L1 硬短路 / L3 语义采纳 / 上下文提取 / persist 富化（不触网）。"""

import pytest

from selfheal.agent.dom import (
    ElementContext,
    compute_repair_key,
    extract_element_context,
)
from selfheal.agent.orchestrator import HealOutcome, SelfHealOrchestrator
from selfheal.agent.strategies.semantic import SemanticStrategy
from selfheal.collect.collector import Scene
from selfheal.config import Settings
from selfheal.knowledge.schema import RepairCase
from selfheal.knowledge.store import KnowledgeStore
from selfheal.llm.embedding import NgramEmbedding

pytestmark = pytest.mark.unit

EMB = NgramEmbedding()


def _ctx(text: str, siblings=(), path="html>body>button") -> ElementContext:
    return ElementContext(text=text, siblings=list(siblings), tag_path=path, source="snapshot")


def _store_case(
    store: KnowledgeStore,
    *,
    text: str,
    new: str = "#new",
    page_fp: str = "pg1",
    path: str = "html>body>button",
    siblings=(),
    verified: bool = False,
    created_at: str | None = None,
) -> RepairCase:
    ctx = _ctx(text, siblings, path)
    case = RepairCase(
        original_selector="#old",
        new_selector=new,
        strategy="heuristic",
        confidence=0.9,
        page_url=f"https://x/{page_fp}",
        page_fingerprint=page_fp,
        repair_key=compute_repair_key(page_fp, path),
        embedding=EMB.embed(ctx.query_text),
        embedding_version=EMB.embedding_version,
        is_verified=verified,
        created_at=created_at,
    )
    store.add_repair(case)
    return case


# --- L1：find_by_repair_key 硬短路 ---


def test_lookup_l1_hard_short_circuit():
    store = KnowledgeStore()
    _store_case(store, text="确定", new="#a-new")
    orch = SelfHealOrchestrator(page=None, settings=Settings(), knowledge=store)
    scene = Scene(url="https://x/pg1")
    outcome = orch._lookup_knowledge(
        scene, "#old", "fp", "pg1", _ctx("确定")
    )
    assert outcome is not None and outcome.success
    assert outcome.strategy == "knowledge"
    assert outcome.root_cause == "cached_l1"
    assert outcome.new_selector == "#a-new"


def test_lookup_l1_miss_falls_back_to_find_repair():
    """repair_key 不匹配时回退旧式精确 selector 检索（向后兼容）。"""
    store = KnowledgeStore()
    _store_case(store, text="确定", new="#a-new")
    orch = SelfHealOrchestrator(page=None, settings=Settings(), knowledge=store)
    scene = Scene(url="https://x/pg1")
    # v2：repair_key 不含文本；用不同 tag_path 使 L1 不命中 → 回退旧式 find_repair(#old) 命中
    outcome = orch._lookup_knowledge(scene, "#old", "fp", "pg1", _ctx("取消", path="html>body>a"))
    assert outcome is not None and outcome.success
    assert outcome.root_cause == "cached"


def test_lookup_l1_hit_bumps_hit_count():
    store = KnowledgeStore()
    case = _store_case(store, text="确定")
    orch = SelfHealOrchestrator(page=None, settings=Settings(), knowledge=store)
    orch._lookup_knowledge(Scene(url="https://x/pg1"), "#old", "fp", "pg1", _ctx("确定"))
    assert store.find_by_repair_key(case.repair_key).hit_count == 1


def test_lookup_l1_empty_context_skips():
    """上下文为空 → 跳过 L1（无法计算 repair_key），走旧式检索。"""
    store = KnowledgeStore()
    _store_case(store, text="确定")
    orch = SelfHealOrchestrator(page=None, settings=Settings(), knowledge=store)
    empty = ElementContext(source="static")
    assert orch._lookup_knowledge(Scene(url="https://x/pg1"), "#other", "fp", "pg1", empty) is None


# --- L3：语义向量检索采纳规则 ---


def test_semantic_accept_verified():
    store = KnowledgeStore()
    _store_case(store, text="登录", new="#login", verified=True)
    strat = SemanticStrategy(
        knowledge=store, embedding=EMB, page_fingerprint="pg1", element_context=_ctx("登录")
    )
    cand = strat.repair(Scene(url=""), "#old", "登录按钮")
    assert cand is not None
    assert cand.selector == "#login"
    assert cand.strategy == "semantic"
    assert cand.confidence > 0.9


def test_semantic_accept_fresh_unverified():
    """新鲜窗口（7 天）内 sim>0.80 → 未 verified 也自动采纳。"""
    import datetime

    store = KnowledgeStore()
    fresh = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _store_case(store, text="登录", new="#login", verified=False, created_at=fresh)
    strat = SemanticStrategy(
        knowledge=store, embedding=EMB, page_fingerprint="pg1", element_context=_ctx("登录")
    )
    cand = strat.repair(Scene(url=""), "#old", "登录按钮")
    assert cand is not None and cand.selector == "#login"


def test_semantic_suggest_stale_unverified(monkeypatch, tmp_path):
    """旧（>7天）且未 verified → 不采纳、写人审清单、返回 None（降级 L4）。"""
    import datetime

    from selfheal.reporting import fix_proposals
    from selfheal.reporting.fix_proposals import append_review_proposal

    monkeypatch.setattr(fix_proposals, "REVIEW_QUEUE_PATH", tmp_path / "review-queue.md")
    store = KnowledgeStore()
    stale = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)).isoformat()
    _store_case(store, text="登录", new="#login", verified=False, created_at=stale)
    strat = SemanticStrategy(
        knowledge=store,
        embedding=EMB,
        page_fingerprint="pg1",
        element_context=_ctx("登录"),
        review_writer=append_review_proposal,  # B6：注入收敛出口（策略不再直连 reporting）
    )
    cand = strat.repair(Scene(url=""), "#old", "登录按钮")
    assert cand is None  # 不采纳 → 继续 L4
    assert (tmp_path / "review-queue.md").exists()
    assert "#old" in (tmp_path / "review-queue.md").read_text(encoding="utf-8")
    assert "#login" in (tmp_path / "review-queue.md").read_text(encoding="utf-8")


def test_semantic_low_sim_no_hit():
    store = KnowledgeStore()
    _store_case(store, text="提交订单", new="#submit")
    strat = SemanticStrategy(
        knowledge=store, embedding=EMB, page_fingerprint="pg1", element_context=_ctx("查询天气")
    )
    cand = strat.repair(Scene(url=""), "#old", "天气")
    assert cand is None


def test_semantic_stale_cached_selector_not_used(monkeypatch, tmp_path):
    """审查 M1 回归：L3 命中的 new_selector 已失效（页面不存在）→ 不采纳、写人审清单（与 L1 护栏对称）。"""
    from selfheal.reporting import fix_proposals
    from selfheal.reporting.fix_proposals import append_review_proposal

    monkeypatch.setattr(fix_proposals, "REVIEW_QUEUE_PATH", tmp_path / "review-queue.md")
    store = KnowledgeStore()
    _store_case(store, text="登录", new="#login", verified=True)  # verified 高分 → 本应自动采纳

    class _FakePage:
        def locator(self, sel, **k):
            class _Loc:
                def count(self):
                    return 0  # #login 不存在 → 候选失效

            return _Loc()

    strat = SemanticStrategy(
        knowledge=store,
        embedding=EMB,
        page_fingerprint="pg1",
        element_context=_ctx("登录"),
        page=_FakePage(),
        review_writer=append_review_proposal,
    )
    cand = strat.repair(Scene(url=""), "#old", "登录按钮")
    assert cand is None  # 候选失效 → 不采纳（交 L4 重定位）
    # 人审清单记录失效原因
    text = (tmp_path / "review-queue.md").read_text(encoding="utf-8")
    assert "cached_selector_stale" in text


def test_semantic_empty_context_skips_l3():
    store = KnowledgeStore()
    _store_case(store, text="登录")
    strat = SemanticStrategy(
        knowledge=store, embedding=EMB, page_fingerprint="pg1", element_context=ElementContext(source="static")
    )
    assert strat.repair(Scene(url=""), "#old", None) is None  # 无 client → LLM 兜底也为 None


# --- 上下文提取（快照 / 静态，无浏览器） ---


def test_extract_snapshot_css():
    dom = '<html><body><button id="login">登录</button><a>取消</a></body></html>'
    ctx = extract_element_context(None, dom, "#login")
    assert ctx.source == "snapshot"
    assert ctx.text == "登录"
    assert ctx.tag_path == "html:nth-of-type(1)>body:nth-of-type(1)>button:nth-of-type(1)"


def test_extract_snapshot_text_selector():
    dom = '<html><body><button id="login">登录</button></body></html>'
    ctx = extract_element_context(None, dom, 'text="登录"')
    assert ctx.source == "snapshot"
    assert ctx.text == "登录"


def test_extract_static_fallback():
    ctx = extract_element_context(None, None, "#never-seen", "登录按钮")
    assert ctx.source == "static"
    assert "登录按钮" in ctx.text


# --- persist 富化 ---


def test_persist_enriches_semantic_fields():
    store = KnowledgeStore()
    orch = SelfHealOrchestrator(page=None, settings=Settings(), knowledge=store)
    scene = Scene(url="https://x/login")
    ctx = _ctx("确定")
    outcome = HealOutcome(success=True, new_selector="#a-new", confidence=0.9, strategy="heuristic")
    orch._persist(scene, "#old", outcome, "fp", "pg1", ctx)
    case = store._repairs[0]
    assert case.page_fingerprint == "pg1"
    assert case.repair_key == compute_repair_key("pg1", "html>body>button")
    assert case.embedding is not None
    assert case.embedding_version == EMB.embedding_version
    assert case.created_at is not None


def test_persist_without_embedding_context_leaves_optional_none():
    store = KnowledgeStore()
    orch = SelfHealOrchestrator(page=None, settings=Settings(), knowledge=store)
    outcome = HealOutcome(success=True, new_selector="#a-new", confidence=0.9, strategy="heuristic")
    orch._persist(Scene(url=""), "#old", outcome)
    case = store._repairs[0]
    assert case.page_fingerprint == ""
    assert case.embedding is None and case.repair_key is None


# --- 上下文缓存：URL 维度键 + 上限（审查 M4） ---


def test_failure_context_cache_keyed_by_url():
    """同 selector 不同 URL → 不误用旧上下文（SPA 多页面流程）。"""
    orch = SelfHealOrchestrator(page=None, settings=Settings(), knowledge=KnowledgeStore())
    dom_a = '<html><body><button id="login">登录</button></body></html>'
    dom_b = '<html><body><button id="login">退出</button></body></html>'
    ctx_a = orch._assembler._element_context(Scene(url="https://x/a", dom_snapshot=dom_a), "#login", None)
    ctx_b = orch._assembler._element_context(Scene(url="https://x/b", dom_snapshot=dom_b), "#login", None)
    assert ctx_a.text == "登录"
    assert ctx_b.text == "退出"  # 修复前同 selector 命中缓存 → 误返回"登录"
    assert len(orch._assembler._failure_context_cache) == 2


def test_failure_context_cache_limited():
    """缓存上限：超过 256 条淘汰最久未用，防长流程内存膨胀。"""
    orch = SelfHealOrchestrator(page=None, settings=Settings(), knowledge=KnowledgeStore())
    dom = '<html><body><button id="b">B</button></body></html>'
    for i in range(300):
        orch._assembler._element_context(Scene(url=f"https://x/{i}", dom_snapshot=dom), "#b", None)
    assert len(orch._assembler._failure_context_cache) <= 256
    # 最早插入的键已被淘汰
    assert ("https://x/0", "#b") not in orch._assembler._failure_context_cache
