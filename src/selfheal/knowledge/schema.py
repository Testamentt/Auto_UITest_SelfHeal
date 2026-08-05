"""知识库数据模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RepairCase:
    """一条修复案例：原定位器 -> 新定位器。

    Phase 5 A 语义化扩展字段：
    - page_fingerprint: URL 路径 + 关键 DOM 结构哈希（页面隔离，防跨页误修复）。
    - repair_key: md5(page_fingerprint + 元素文本 + 标签路径)，L1 精确命中键（确定性哈希）。
    - embedding / embedding_version: 本地向量（BLOB）与版本（迁移用）。
    - hit_count / last_hit_at: 命中次数与时间（热度与衰减）。
    - is_verified: 人工确认标记（防误修复自命中）。
    - created_at: 创建时间（新鲜度特权）。
    """

    original_selector: str
    new_selector: str
    strategy: str  # heuristic / semantic / visual / knowledge
    confidence: float
    page_url: str
    dom_fingerprint: str | None = None  # 旧相似度指纹（保留兼容）
    page_fingerprint: str | None = None
    repair_key: str | None = None
    embedding: bytes | None = None
    embedding_version: str | None = None
    hit_count: int = 0
    last_hit_at: str | None = None
    is_verified: bool = False
    created_at: str | None = None


@dataclass
class PopupFeature:
    """一条弹窗特征：如何识别 + 如何关闭。"""

    signature: str  # 识别特征（文本 / 结构指纹）
    dismiss_selector: str  # 关闭按钮定位器
    category: str = "generic"  # permission / ad / system ...
