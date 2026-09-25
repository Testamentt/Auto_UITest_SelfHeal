"""稳定定位器生成子模块（A5 拆包）——data-testid > id > 文本 > aria-label 优先级链。

原 agent/dom.py 按职责拆分而来（见 dom/__init__.py 导出）。

2026-10-08 审查修复（M11）：属性/文本值统一转义（含引号）——此前值里带 `"` 会生成非法选择器
（CSS 属性选择器直接解析报错）；换行折成空格，避免 text 选择器跨行失配。
实测语义（Playwright）：`text="关闭"` 是**精确匹配**（整串相等、大小写敏感），`text=关闭` 才是
子串匹配——本模块产出带引号形式，故不会把"关闭"扩大到"关闭订单"。
"""

from __future__ import annotations

import re

from selfheal.agent.dom.parser import Element

# 可安全用作 CSS id 选择器的字符集（其余走属性选择器，防特殊字符破坏 CSS，审查 nit）
_SAFE_ID_RE = re.compile(r"[\w-]+")


def _escape(value: str) -> str:
    """转义选择器字面量中的引号与换行（双引号形式的选择器值）。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")


def build_stable_selector(el: Element) -> str | None:
    """由候选元素生成稳定的 Playwright 定位器。"""
    if testid := el.attr("data-testid"):
        return f'[data-testid="{_escape(testid)}"]'
    if el_id := el.attr("id"):
        # id 含 CSS 特殊字符（: . [ ] 空格等）时用属性选择器，避免生成非法 CSS
        return f'[id="{_escape(el_id)}"]' if not _SAFE_ID_RE.fullmatch(el_id) else f"#{el_id}"
    if text := el.field("text"):
        return f'text="{_escape(text)}"'
    if aria := el.attr("aria-label"):
        return f'[aria-label="{_escape(aria)}"]'
    return None
