"""端到端测试：弹窗自愈场景（需浏览器内核，-m e2e）。

验证 PopupGuard：有效定位器但被弹窗遮挡 → 动作超时 → 自动关闭弹窗 → 重试成功，
并把弹窗特征沉淀进知识库。
"""

import pytest

from tests.e2e.pages.popup_page import PopupPage

pytestmark = pytest.mark.e2e


def test_popup_auto_dismiss_then_action(healing_page, knowledge):
    healing_page.set_default_timeout(3000)  # 缩短等待，避免遮挡超时等满 30s

    demo = PopupPage(healing_page)
    demo.open()
    demo.click_target()  # 弹窗遮挡 → PopupGuard 关闭 → 重试成功

    assert demo.result() == "clicked"

    # 弹窗特征已沉淀进知识库（供下次命中复用）
    assert knowledge.count_popups() >= 1
