"""知识库后端抽象。

定义知识存储与检索的统一接口（Protocol）。内存实现（store.KnowledgeStore）与
SQLite 实现（sqlite_store.SqliteKnowledgeStore）均遵循该接口，由 factory 按配置选择，
调用方（orchestrator / PopupGuard / 测试）不感知具体后端。

**时间字段格式契约（R1，两端必须逐字一致）**——适用于 ``RepairCase.created_at`` 与
``RepairCase.last_hit_at``：

- **写入**：一律 ``utc_now_iso()``（UTC ISO 8601、秒精度、带 ``+00:00``，形如
  ``2026-10-08T12:00:00+00:00``）。字段缺席时由 ``utc_now_iso_if_missing()`` 补齐，
  **不得**依赖 SQLite 列默认值 ``CURRENT_TIMESTAMP``（它产出 ``2026-10-08 12:00:00``，
  空格分隔且无时区）。
- **读出**：两端都必须给出可被 ``datetime.fromisoformat`` 解析且 tz-aware 的字符串；
  SQLite 读侧对旧库的历史空格格式做等价归一（补 UTC 时区 → ISO），兼容而非丢弃。
- **消费方**：``agent/strategies/semantic._is_fresh``（L3 七天新鲜窗口）按此契约解析，
  两端判定口径因此一致；无法解析的值按"不新鲜"处理（保守，不误采纳）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from selfheal.knowledge.schema import PopupFeature, RepairCase, RepairQuery


def utc_now_iso() -> str:
    """知识库时间字段的统一格式：UTC ISO 8601、秒精度、带 ``+00:00`` 时区。

    形如 ``2026-10-08T12:00:00+00:00``。内存后端与 SQLite 后端写 ``last_hit_at``
    共用本函数（M4：此前两端分别用 ``isoformat`` 与 SQLite ``datetime('now')``，
    格式不一致且注释与事实相反），保证读出值可被 ``datetime.fromisoformat`` 解析且 tz-aware。
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utc_now_iso_if_missing(raw: str | None) -> str:
    """``created_at`` 写入归一：未提供（``None``）时补当前 UTC ISO 串，已提供原样返回（R1）。

    背景：两端曾各自兜底——SQLite 靠列默认值 ``DEFAULT CURRENT_TIMESTAMP`` 产出
    ``"2026-10-08 12:00:00"``（空格分隔、无时区），内存后端写入方不传则整字段为 ``None``，
    于是同一份逻辑数据在两端落成"可解析"与"不可解析"两种形态，直接改变
    ``agent/strategies/semantic._is_fresh``（L3 七天新鲜窗口）的判定口径。

    约定：写入侧统一调用本函数补齐，读出侧再按同一契约归一（SQLite 读侧兼容旧空格格式），
    因此两端读出的 ``created_at`` 必然同格式、同可解析性。空串等假值视为已提供、不覆盖
    （与 SQLite 列默认值只在字段缺席时生效的语义保持一致）。
    """
    return raw if raw is not None else utc_now_iso()


class KnowledgeBackend(Protocol):
    """知识库统一接口（内存 / SQLite 实现均遵循）。

    Phase 5 A 语义化新增：
    - find_by_repair_key：L1 精确命中（确定性 repair_key）。
    - find_semantic：L3 按 page_fingerprint 分桶 → numpy 余弦相似度。
    - bump_hit / set_verified：防污染衰减与人工审核（命中递增、verified 标记）。
    A3 门面：query(RepairQuery) 收敛「L1 精确 → 旧式检索」的择优（置信度/缓存验证留调用方）。
    M4：补齐 count_repairs / close 声明——两端实现本就有，Protocol 缺声明会让
    orchestrator._safe_close 的 getattr 探测静默跳过（资源不释放、无任何日志）。
    """

    def add_repair(self, case: RepairCase) -> None:
        """沉淀一条修复案例（按 (original_selector, new_selector, dom_fingerprint or "") upsert）。

        时间契约（见模块 docstring）：``case.created_at`` 为 ``None`` 时由写入侧补
        ``utc_now_iso()``——两端都不得落 ``None``，否则 L3 新鲜窗口在两端判定不同。
        upsert 保留首次写入的 ``created_at``（不刷新为末次写入时刻）。
        """
        ...

    def add_popup(self, feature: PopupFeature) -> None:
        """沉淀一条弹窗特征。"""
        ...

    def find_repair(
        self, original_selector: str, dom_fingerprint: str | None = None
    ) -> RepairCase | None:
        """按原定位器检索修复案例；给定 dom_fingerprint 时优先同结构页面。"""
        ...

    def find_by_repair_key(self, repair_key: str) -> RepairCase | None:
        """L1：按确定性 repair_key 精确命中（页面指纹 + 元素文本 + 标签路径）。"""
        ...

    def query(self, q: RepairQuery) -> list[tuple[RepairCase, str]]:
        """按优先级返回候选（L1 精确命中优先，其次旧式 selector+指纹）；可能为空列表。

        返回 [(case, source)]，source ∈ {"l1", "legacy"}；供调用方按序做置信度/缓存验证。
        """
        ...

    def find_semantic(
        self,
        query_vec: bytes,
        page_fingerprint: str,
        embedding_version: str,
        k: int = 1,
        threshold: float = 0.75,
    ) -> list[tuple[RepairCase, float]]:
        """L3：同页分桶 + numpy 余弦相似度，返回 top-k（sim >= threshold）。"""
        ...

    def bump_hit(self, repair_key: str) -> None:
        """命中递增（热度 / 衰减用）。"""
        ...

    def set_verified(self, repair_key: str, verified: bool) -> None:
        """人工审核标记（人审确认后置 True）。"""
        ...

    def find_popup(self, signature: str) -> PopupFeature | None:
        """按弹窗签名检索关闭方式。"""
        ...

    def count_popups(self) -> int:
        """返回已沉淀的弹窗特征数量。"""
        ...

    def count_repairs(self) -> int:
        """返回已沉淀的修复案例数量（指标统计 / 迁移断言用）。"""
        ...

    def close(self) -> None:
        """释放后端资源（幂等；内存后端为 no-op），供生命周期收口统一调用。"""
        ...
