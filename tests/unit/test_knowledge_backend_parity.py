"""单元测试：知识库双后端（内存 / SQLite）语义对齐（M4，不依赖浏览器与网络）。

背景：代码审查发现双后端语义漂移——内存端 add_repair 直接 append（无 upsert）、
find_repair 取"插入序首个"指纹匹配者（SQLite 取置信度最高者）、last_hit_at 用 Python ISO
而 SQLite 用 datetime('now')、内存端缺 close()。本文件用**同一组输入**跑两个后端并逐条
比对观测结果（upsert 键 / 覆盖列 / 择优规则 / 时间格式 / 人工审核标记保留）。
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from selfheal.knowledge.base import KnowledgeBackend
from selfheal.knowledge.schema import PopupFeature, RepairCase
from selfheal.knowledge.sqlite_store import SqliteKnowledgeStore
from selfheal.knowledge.store import KnowledgeStore

pytestmark = pytest.mark.unit


def _case(
    *,
    original: str = "#old",
    new: str = "#new",
    confidence: float = 0.9,
    fp: str | None = None,
    strategy: str = "heuristic",
    page_url: str = "https://x/page",
    page_fp: str | None = None,
    repair_key: str | None = None,
) -> RepairCase:
    return RepairCase(
        original_selector=original,
        new_selector=new,
        strategy=strategy,
        confidence=confidence,
        page_url=page_url,
        dom_fingerprint=fp,
        page_fingerprint=page_fp,
        repair_key=repair_key,
    )


@contextmanager
def _store(backend: str, tmp_path):
    """按后端名构造 store（sqlite 落 tmp_path，避免污染真实库）；退出时 close（内存端 no-op）。"""
    store = (
        KnowledgeStore() if backend == "memory" else SqliteKnowledgeStore(str(tmp_path / "kb.db"))
    )
    try:
        yield store
    finally:
        store.close()


def _is_utc_iso(raw: str | None) -> bool:
    """是否 UTC ISO 串（可被 fromisoformat 解析、tz-aware、秒精度）。"""
    if not raw:
        return False
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return False
    return (
        dt.tzinfo is not None
        and dt.utcoffset() == timedelta(0)
        and raw == dt.isoformat(timespec="seconds")
    )


def _observations(store) -> list[tuple]:
    """对同一组输入跑一遍后端，返回可比对观测列表（顺序敏感 → 逐条对齐）。

    刻意不含 last_hit_at / created_at 原值：两端调用时刻可能差 1 秒（非语义差异），
    时间格式由 test_last_hit_at_iso_utc_format 单独断言。
    """
    obs: list[tuple] = []

    # ① upsert：同键三次写入收敛为 1 条，末次写入的"覆盖列"胜出
    store.add_repair(
        _case(new="#n1", confidence=0.5, fp="fpA", strategy="heuristic", repair_key="rk-1")
    )
    store.add_repair(
        _case(new="#n1", confidence=0.7, fp="fpA", strategy="semantic", repair_key="rk-1")
    )
    store.add_repair(
        _case(
            new="#n1",
            confidence=0.9,
            fp="fpA",
            strategy="visual",
            page_url="https://x/page2",
            page_fp="pg2",
            repair_key="rk-1",
        )
    )
    obs.append(("count_after_upsert", store.count_repairs()))
    c1 = store.find_by_repair_key("rk-1")
    obs.append(
        (
            "upsert_last_write_wins",
            (c1.new_selector, c1.strategy, c1.confidence, c1.page_url, c1.page_fingerprint),
        )
    )

    # ② 键由 (original_selector, new_selector, dom_fingerprint) 三元组决定：任一不同 → 独立条目
    store.add_repair(_case(new="#n2", confidence=0.8, fp="fpA", repair_key="rk-2"))
    store.add_repair(
        _case(original="#other", new="#n2", confidence=0.8, fp="fpA", repair_key="rk-3")
    )
    # 指纹用 fpC（非 fpB）：下面 ④ 的 fpB 分组只保留待比对的 3 条，避免混淆择优信号
    store.add_repair(_case(new="#n2", confidence=0.8, fp="fpC", repair_key="rk-4"))
    obs.append(("count_distinct_keys", store.count_repairs()))

    # ③ 无指纹：None 与 "" 视为同一键（SQLite UNIQUE 对 NULL 不生效的坑），读回均为 None
    store.add_repair(_case(new="#n3", confidence=0.6, fp=None, repair_key="rk-none"))
    store.add_repair(_case(new="#n3", confidence=0.6, fp="", repair_key="rk-none"))
    obs.append(("count_none_and_empty_fp_merged", store.count_repairs()))
    obs.append(("none_fp_reads_back_none", store.find_by_repair_key("rk-none").dom_fingerprint))

    # ④ 指纹命中：只有一条匹配 → 即便置信度更低也返回；多条匹配 → 取置信度最高者
    store.add_repair(_case(new="#n4", confidence=0.4, fp="fpB", repair_key="rk-4b"))
    obs.append(("fp_single_match", store.find_repair("#old", "fpB").new_selector))
    store.add_repair(_case(new="#n5", confidence=0.75, fp="fpB", repair_key="rk-5"))
    store.add_repair(_case(new="#n6", confidence=0.45, fp="fpB", repair_key="rk-6"))
    obs.append(("fp_picks_highest_confidence", store.find_repair("#old", "fpB").new_selector))

    # ⑤ 未给指纹 / 指纹无匹配 → 退化取置信度最高者；选择器无命中 → None
    obs.append(("no_fp_arg_picks_highest", store.find_repair("#old").new_selector))
    obs.append(("fp_no_match_falls_back", store.find_repair("#old", "fpZ").new_selector))
    obs.append(("selector_miss", store.find_repair("#missing")))

    # ⑥ 命中计数 / 人工审核标记：再次 upsert 不得清除（对齐 SQLite DO UPDATE 列清单）
    store.bump_hit("rk-5")
    store.set_verified("rk-5", True)
    obs.append(("hit_count", store.find_by_repair_key("rk-5").hit_count))
    obs.append(("hit_at_is_utc_iso", _is_utc_iso(store.find_by_repair_key("rk-5").last_hit_at)))
    store.add_repair(
        _case(new="#n5", confidence=0.75, fp="fpB", strategy="heuristic", repair_key="rk-5")
    )
    after = store.find_by_repair_key("rk-5")
    obs.append(("verified_preserved_after_upsert", after.is_verified))
    obs.append(("hit_count_preserved_after_upsert", after.hit_count))
    obs.append(("hit_at_preserved_after_upsert", _is_utc_iso(after.last_hit_at)))

    # ⑦ 弹窗特征：同 signature upsert，最新观察胜出
    store.add_popup(PopupFeature(signature="cookie", dismiss_selector="#c1"))
    store.add_popup(PopupFeature(signature="cookie", dismiss_selector="#c2"))
    obs.append(("popup_count", store.count_popups()))
    obs.append(("popup_latest_wins", store.find_popup("cookie").dismiss_selector))

    return obs


def test_backends_parity_same_inputs_same_results(tmp_path):
    """M4 主回归：同一组输入 → 内存与 SQLite 的观测结果逐条相同（含条数、字段、择优）。"""
    with _store("memory", tmp_path) as memory, _store("sqlite", tmp_path) as sqlite:
        assert _observations(memory) == _observations(sqlite)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_upsert_converges_and_last_write_wins(backend, tmp_path):
    """M4：同键重复沉淀收敛为 1 条，覆盖列取末次写入值（此前内存端会堆 3 条）。"""
    with _store(backend, tmp_path) as store:
        for confidence, strategy in ((0.5, "heuristic"), (0.7, "semantic"), (0.9, "visual")):
            store.add_repair(
                _case(
                    new="#n1", confidence=confidence, fp="fpA", strategy=strategy, repair_key="rk"
                )
            )
        assert store.count_repairs() == 1
        found = store.find_by_repair_key("rk")
        assert (found.new_selector, found.strategy, found.confidence) == ("#n1", "visual", 0.9)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_upsert_preserves_is_verified(backend, tmp_path):
    """M4：upsert 不清除人工审核标记 is_verified（与 SQLite DO UPDATE 列清单一致）。"""
    with _store(backend, tmp_path) as store:
        store.add_repair(_case(fp="fp", repair_key="rk"))
        store.set_verified("rk", True)
        store.add_repair(_case(fp="fp", repair_key="rk", strategy="semantic"))  # 新案例默认 False
        found = store.find_by_repair_key("rk")
        assert found.is_verified is True
        assert found.strategy == "semantic"  # 覆盖列仍生效，仅审核标记被保留


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_find_repair_prefers_highest_confidence_fingerprint_match(backend, tmp_path):
    """M4：指纹命中多条 → 取该指纹下置信度最高者（此前内存端取插入序首个）。"""
    with _store(backend, tmp_path) as store:
        store.add_repair(_case(new="#first", confidence=0.4, fp="fpB"))
        store.add_repair(_case(new="#best", confidence=0.75, fp="fpB"))
        store.add_repair(_case(new="#middle", confidence=0.6, fp="fpB"))
        found = store.find_repair("#old", "fpB")
        assert found is not None and found.new_selector == "#best"


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_find_repair_falls_back_to_highest_confidence(backend, tmp_path):
    """M4：指纹无匹配 / 未给指纹 → 退化取置信度最高者；选择器无命中 → None。"""
    with _store(backend, tmp_path) as store:
        store.add_repair(_case(new="#low", confidence=0.3, fp="fpA"))
        store.add_repair(_case(new="#high", confidence=0.95, fp="fpB"))
        assert store.find_repair("#old").new_selector == "#high"
        assert store.find_repair("#old", "fpZ").new_selector == "#high"
        assert store.find_repair("#missing") is None


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_last_hit_at_iso_utc_format(backend, tmp_path):
    """M4：last_hit_at 两端同格式——UTC ISO、秒精度、可被 fromisoformat 解析且 tz-aware。"""
    with _store(backend, tmp_path) as store:
        store.add_repair(_case(repair_key="rk-iso"))
        store.bump_hit("rk-iso")
        found = store.find_by_repair_key("rk-iso")
        raw = found.last_hit_at
        assert _is_utc_iso(raw)
        assert raw.endswith("+00:00") and "T" in raw  # 非 SQLite datetime('now') 的空格格式
        assert abs((datetime.now(timezone.utc) - datetime.fromisoformat(raw)).total_seconds()) < 60
        if backend == "sqlite":
            stored = store._conn.execute("SELECT last_hit_at FROM repairs").fetchone()[0]
            assert stored == raw  # 落库即 ISO（修复前落的是 datetime('now')）


def test_legacy_last_hit_at_space_format_normalized(tmp_path):
    """M4：旧库 datetime('now') 值（空格分隔、无时区）读出时归一为 UTC ISO，仍可解析。"""
    store = SqliteKnowledgeStore(str(tmp_path / "legacy-ts.db"))
    try:
        store.add_repair(_case(repair_key="rk-legacy"))
        store._conn.execute("UPDATE repairs SET last_hit_at = '2026-10-08 12:00:00'")
        store._conn.commit()
        found = store.find_by_repair_key("rk-legacy")
        assert found.last_hit_at == "2026-10-08T12:00:00+00:00"
        assert _is_utc_iso(found.last_hit_at)
    finally:
        store.close()


def test_memory_close_noop_idempotent():
    """M4：内存后端 close() 为 no-op 且幂等，且不清空已沉淀数据。"""
    store = KnowledgeStore()
    store.add_repair(_case(repair_key="rk-close"))
    store.close()
    store.close()  # 二次 close 不抛异常
    assert store.count_repairs() == 1  # 数据保留（与 SQLite 关连接后文件数据仍在语义对齐）


def test_protocol_declares_count_repairs_and_close():
    """M4：KnowledgeBackend 显式声明 count_repairs / close（缺声明会让 _safe_close 静默跳过）。"""
    assert callable(getattr(KnowledgeBackend, "count_repairs", None))
    assert callable(getattr(KnowledgeBackend, "close", None))


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_implementations_expose_protocol_lifecycle_members(backend, tmp_path):
    """M4：两端实现都暴露协议声明的生命周期成员（结构一致性，防再次漂移）。"""
    with _store(backend, tmp_path) as store:
        for name in ("add_repair", "find_repair", "count_repairs", "close"):
            assert callable(getattr(store, name, None))
