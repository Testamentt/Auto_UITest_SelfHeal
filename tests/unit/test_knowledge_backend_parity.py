"""单元测试：知识库双后端（内存 / SQLite）语义对齐（M4，不依赖浏览器与网络）。

背景：代码审查发现双后端语义漂移——内存端 add_repair 直接 append（无 upsert）、
find_repair 取"插入序首个"指纹匹配者（SQLite 取置信度最高者）、last_hit_at 用 Python ISO
而 SQLite 用 datetime('now')、内存端缺 close()。本文件用**同一组输入**跑两个后端并逐条
比对观测结果（upsert 键 / 覆盖列 / 择优规则 / 时间格式 / 人工审核标记保留）。
"""

import sqlite3
from contextlib import contextmanager
from dataclasses import replace
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


# --- R2（K2）：find_repair 同分并列的稳定次序 ---


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_find_repair_tie_break_stable_and_matches_memory(backend, tmp_path):
    """R2：置信度全部并列 → 次序稳定且取插入序首个（SQLite 端靠 ``ORDER BY … rowid ASC``）。

    修复前 SQL 未显式写次级排序键，次序依赖 SQLite 扫描/索引选择（理论不保证）；现约定
    "置信度降序 → rowid 升序（插入序）"，与内存端稳定排序的列表序一致。
    """
    with _store(backend, tmp_path) as store:
        for new in ("#t-first", "#t-second", "#t-third"):
            store.add_repair(_case(new=new, confidence=0.6, fp=None))
        # 指纹不同也走并列分支（查询不带指纹）→ 取首写者
        assert store.find_repair("#old").new_selector == "#t-first"
        # 重复调用同一输入必须同结果（稳定性，非偶然）
        assert {store.find_repair("#old").new_selector for _ in range(5)} == {"#t-first"}


def test_find_repair_tie_break_uses_rowid_ascending(tmp_path):
    """R2：带指纹时同样按 "置信度降序 → 插入序" 取首个指纹匹配者（SQLite 侧逐条验证）。"""
    store = SqliteKnowledgeStore(str(tmp_path / "tie.db"))
    try:
        store.add_repair(_case(new="#n1", confidence=0.6, fp="fpX", repair_key="rk-1"))
        store.add_repair(_case(new="#n2", confidence=0.6, fp="fpX", repair_key="rk-2"))
        # 同指纹并列 → 首写者
        assert store.find_repair("#old", "fpX").new_selector == "#n1"
        # 与库内 rowid 次序一致（首写 = 最小 rowid）
        rows = store._conn.execute(
            "SELECT new_selector FROM repairs WHERE original_selector = '#old'"
            " ORDER BY confidence DESC, rowid ASC"
        ).fetchall()
        assert [r["new_selector"] for r in rows] == ["#n1", "#n2"]
    finally:
        store.close()


# --- R3（K3）：NULL / 负值 confidence 的读出与排序一致性 ---


def test_dirty_confidence_ordering_parity(tmp_path):
    """R3 主回归：脏库（confidence 为 NULL / 负值）在两端读出与排序结论一致。

    - 读出：NULL → 0.0、负值 → 0.0（两端同值，越界值不泄进指标）；
    - 排序：负值不再被排到有效候选之后（SQLite 侧此前 ``ORDER BY confidence DESC`` 把负值
      排最后，而内存端按真实值排序 → 两端可能给出相反结论）；
    - 历史 NULL 行（旧库 ALTER 补列）不参与并列争抢，语义与改动前一致。
    """
    with _store("memory", tmp_path) as memory, _store("sqlite", tmp_path) as sqlite:
        for store in (memory, sqlite):
            # ① 正常写入：负值经写侧归一落 0.0
            store.add_repair(_case(original="#dirty", new="#d-neg", confidence=-0.5, fp="fpD"))
            store.add_repair(_case(original="#dirty", new="#d-good", confidence=0.6, fp="fpD"))
            store.add_repair(_case(original="#dirty", new="#d-zero", confidence=0.0, fp="fpD"))
            store.add_repair(_case(original="#dirty", new="#d-null", confidence=0.4, fp="fpD"))
        # ② 直插脏值（等价手改库 / 旧库补列留下的 NULL），绕过写侧归一
        memory._repairs = [
            replace(c, confidence=None) if c.new_selector in ("#d-null", "#d-zero") else c
            for c in memory._repairs
        ]
        sqlite._conn.execute(
            "UPDATE repairs SET confidence = NULL WHERE new_selector IN ('#d-null', '#d-zero')"
        )
        sqlite._conn.commit()

        def _read_all(store):
            """统一从库内取回该选择器全部案例（SQLite 走行→dataclass 归一）。

            内存端直接取内部对象，刻意**不走**读侧归一——先验证"库内脏值"本身在两端同值域
            （写侧归一）；读侧归一由 ``test_read_side_confidence_normalized`` 覆盖。
            """
            if isinstance(store, KnowledgeStore):
                return [c for c in store._repairs if c.original_selector == "#dirty"]
            return [
                store._row_to_repair(row)
                for row in store._conn.execute(
                    "SELECT * FROM repairs WHERE original_selector = '#dirty'"
                ).fetchall()
            ]

        def _ordered(store):
            """按 confidence 降序（并列按插入序）列出——脏 ``None`` 先按 0.0 处理（同读侧契约）。

            SQLite 侧经过 ``_row_to_repair`` 已归一；内存端读的是库内原始对象（可能是
            ``None``），此处显式按同一契约折算，避免 ``None`` 参与比较抛 ``TypeError``。
            """
            return sorted(
                (
                    (0.0 if c.confidence is None else c.confidence, c.new_selector)
                    for c in _read_all(store)
                ),
                key=lambda item: item[0],
                reverse=True,
            )

        assert _ordered(memory) == _ordered(sqlite)
        assert _ordered(memory)[0] == (0.6, "#d-good")
        assert sorted(_ordered(memory)) == [
            (0.0, "#d-neg"),  # 写侧归一：负值 → 0.0（修复前 SQLite 端会排到最后）
            (0.0, "#d-null"),  # 直插 NULL → 读出 0.0
            (0.0, "#d-zero"),  # 直插 NULL → 读出 0.0
            (0.6, "#d-good"),
        ]
        # 择优结论两端一致：值最高者胜（脏值不因其存储形态而改变判定）
        assert memory.find_repair("#dirty", "fpD").new_selector == "#d-good"
        assert sqlite.find_repair("#dirty", "fpD").new_selector == "#d-good"
        assert memory.find_repair("#dirty", "fpD").confidence == 0.6
        assert sqlite.find_repair("#dirty", "fpD").confidence == 0.6


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_read_side_confidence_normalized(backend, tmp_path):
    """R3：读侧同样归一——脏库里的 NULL confidence 经任一读路径都返回 0.0（两端对称）。

    修复前：SQLite 端读侧归一 0.0，内存端把 ``None`` 原样泄给调用方（``float`` 消费者会炸、
    排序比较会抛 ``TypeError``），同一份脏数据两端不一致。
    """
    with _store(backend, tmp_path) as store:
        store.add_repair(
            _case(original="#dirty", new="#only", confidence=0.4, fp="fpD", repair_key="rk-only")
        )
        # 直插脏值（绕过写侧归一）：等价手改库 / 旧库补列留下的 NULL
        if isinstance(store, KnowledgeStore):
            store._repairs[0] = replace(store._repairs[0], confidence=None)
        else:
            store._conn.execute("UPDATE repairs SET confidence = NULL")
            store._conn.commit()
        assert store.find_repair("#dirty", "fpD").confidence == 0.0
        assert store.find_by_repair_key("rk-only").confidence == 0.0
        assert store.find_by_repair_key("rk-missing") is None  # 未命中不返回脏对象


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_write_side_confidence_normalized(backend, tmp_path):
    """R3：两端写入即归一——``None`` / 负值 → 0.0；``> 1.0`` 原样保留。

    越上界刻意不夹取：``fix_generator.lookup_knowledge`` 用 ``0.0 <= conf <= 1.0`` 门槛
    拒绝无效置信度（见 ``test_secondary_healing`` 的越界用例），知识库层洗成 1.0 会静默
    绕过该 fail-closed 判定。
    """
    with _store(backend, tmp_path) as store:
        store.add_repair(_case(new="#neg", confidence=-0.5, fp="fpD", repair_key="rk-neg"))
        store.add_repair(_case(new="#over", confidence=1.7, fp="fpD", repair_key="rk-over"))
        assert store.find_by_repair_key("rk-neg").confidence == 0.0
        assert store.find_by_repair_key("rk-over").confidence == 1.7  # 不洗白，交由消费端拒绝


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


# --- R1（K1）：created_at 双端格式漂移 ---


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_created_at_filled_and_iso_utc_when_missing(backend, tmp_path):
    """R1：写入方未传 created_at 时两端都补 UTC ISO 串——不再"内存 None / SQLite 空格格式"。

    修复前：内存端整字段为 ``None``（不可解析），SQLite 靠列默认值 ``CURRENT_TIMESTAMP``
    产出 ``2026-10-08 12:00:00``（空格分隔、无时区），L3 七天新鲜窗口（``_is_fresh``）的
    判定口径因此在两端不同。
    """
    with _store(backend, tmp_path) as store:
        store.add_repair(_case(repair_key="rk-created"))  # created_at 刻意留空（None）
        found = store.find_by_repair_key("rk-created")
        raw = found.created_at
        assert raw is not None  # 修复前内存端为 None
        assert _is_utc_iso(raw)  # 可解析、tz-aware、秒精度、与 last_hit_at 同格式
        assert raw.endswith("+00:00") and "T" in raw and " " not in raw
        assert abs((datetime.now(timezone.utc) - datetime.fromisoformat(raw)).total_seconds()) < 60
        if backend == "sqlite":
            stored = store._conn.execute("SELECT created_at FROM repairs").fetchone()[0]
            assert stored == raw  # 落库即 ISO（修复前落的是 CURRENT_TIMESTAMP 空格格式）


def test_explicit_created_at_preserved_on_both_backends(tmp_path):
    """R1：调用方显式给出的 created_at 原样保留（两端一致，不被补值覆盖）。"""
    explicit = "2026-01-02T03:04:05+00:00"
    cases = [
        _case(new="#n1", fp="fpA", repair_key="rk-exp"),
        _case(new="#n2", fp="fpB", repair_key="rk-exp2"),
    ]
    with _store("memory", tmp_path) as memory, _store("sqlite", tmp_path) as sqlite:
        observed = []
        for store in (memory, sqlite):
            for case in cases:
                store.add_repair(replace(case, created_at=explicit))
            observed.append(
                (
                    store.find_by_repair_key("rk-exp").created_at,
                    store.find_by_repair_key("rk-exp2").created_at,
                )
            )
        assert observed[0] == observed[1] == (explicit, explicit)


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_created_at_preserved_on_upsert(backend, tmp_path):
    """R1：upsert 保留首写 created_at（与 is_verified 同策略），两端一致。"""
    explicit = "2026-01-02T03:04:05+00:00"
    with _store(backend, tmp_path) as store:
        store.add_repair(replace(_case(fp="fp", repair_key="rk-keep"), created_at=explicit))
        store.add_repair(_case(fp="fp", repair_key="rk-keep", confidence=0.5))  # 新案例不带时间
        assert store.find_by_repair_key("rk-keep").created_at == explicit


def test_legacy_created_at_space_format_normalized(tmp_path):
    """R1：旧库 CURRENT_TIMESTAMP 值（空格分隔、无时区）读出时归一为 UTC ISO 且可解析。"""
    store = SqliteKnowledgeStore(str(tmp_path / "legacy-created.db"))
    try:
        store.add_repair(_case(repair_key="rk-legacy-created"))
        store._conn.execute("UPDATE repairs SET created_at = '2026-10-08 12:00:00'")
        store._conn.commit()
        found = store.find_by_repair_key("rk-legacy-created")
        assert found.created_at == "2026-10-08T12:00:00+00:00"
        assert _is_utc_iso(found.created_at)
    finally:
        store.close()


def test_legacy_null_created_at_reads_back_none(tmp_path):
    """R1：历史 NULL 列（ALTER 补列）读出为 None，不伪造时间、不抛异常。"""
    db = tmp_path / "legacy-null-created.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE repairs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " original_selector TEXT NOT NULL, new_selector TEXT NOT NULL, repair_key TEXT)"
    )
    conn.execute(
        "INSERT INTO repairs (original_selector, new_selector, repair_key)"
        " VALUES ('#old', '#new', 'rk-null')"
    )
    conn.commit()
    conn.close()
    store = SqliteKnowledgeStore(str(db))
    try:
        assert store.find_by_repair_key("rk-null").created_at is None
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
