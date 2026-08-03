"""知识库数据模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RepairCase:
    """一条修复案例：原定位器 -> 新定位器。"""

    original_selector: str
    new_selector: str
    strategy: str  # heuristic / semantic / visual
    confidence: float
    page_url: str
    dom_fingerprint: str | None = None  # 用于相似度匹配


@dataclass
class PopupFeature:
    """一条弹窗特征：如何识别 + 如何关闭。"""

    signature: str  # 识别特征（文本 / 结构指纹）
    dismiss_selector: str  # 关闭按钮定位器
    category: str = "generic"  # permission / ad / system ...
