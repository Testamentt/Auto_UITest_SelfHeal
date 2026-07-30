"""弹窗处理。

识别并自动关闭系统弹窗、权限申请、运营浮层等"突袭"，是提升通过率的关键能力。
命中特征优先走知识库（弹窗特征库），未命中再交由 LLM 诊断。
TODO: 接入 knowledge 弹窗特征库与 agent.diagnose。
"""

from __future__ import annotations

from playwright.sync_api import Page


class PopupGuard:
    def __init__(self, page: Page):
        self._page = page

    def dismiss_if_present(self) -> bool:
        """检测并关闭当前页面的干扰弹窗，返回是否处理了弹窗。"""
        # TODO: 1) 查知识库弹窗特征；2) 未命中则截图 + DOM 交 LLM 判定关闭按钮。
        return False
