"""稳定定位器生成子模块（A5 拆包）——data-testid > id > 文本 > aria-label 优先级链。

原 agent/dom.py 按职责拆分而来（见 dom/__init__.py 导出）。
"""

from __future__ import annotations

import re

from selfheal.agent.dom.parser import Element

# 可安全用作 CSS id 选择器的字符集（其余走属性选择器，防特殊字符破坏 CSS，审查 nit）
_SAFE_ID_RE = re.compile(r"[\w-]+")


def build_stable_selector(el: Element) -> str | None:
    """由候选元素生成稳定的 Playwright 定位器。"""
    if testid := el.attr("data-testid"):
        return f'[data-testid="{testid}"]'
    if el_id := el.attr("id"):
        # id 含 CSS 特殊字符（: . [ ] 空格等）时用属性选择器，避免生成非法 CSS
        return f'[id="{el_id}"]' if not _SAFE_ID_RE.fullmatch(el_id) else f"#{el_id}"
    if text := el.field("text"):
        return f'text="{text}"'
    if aria := el.attr("aria-label"):
        return f'[aria-label="{aria}"]'
    return None
