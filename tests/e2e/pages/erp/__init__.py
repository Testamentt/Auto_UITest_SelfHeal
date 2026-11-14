# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""管伊佳 ERP 页面对象。"""

from __future__ import annotations

# intro.js 新手引导层（overlay/helperLayer/tooltip/hints）会遮挡全页点击（covered），
# 用 DOM 移除（而非 display:none，避免祖先层隐藏误伤其他元素的可见性判定）。
DISMISS_INTRO_JS = (
    "() => { document.querySelectorAll('.introjs-overlay, .introjs-helperLayer,"
    " .introjs-tooltipReferenceLayer, .introjs-tooltip, .introjs-hints')"
    ".forEach(el => el.remove()); }"
)


def dismiss_intro(page) -> None:
    """移除 ERP 前端 intro.js 引导层（best-effort：未启动引导时无元素可移除）。"""
    page.evaluate(DISMISS_INTRO_JS)
