"""端到端测试：智能等待（wait_until_stable，需浏览器内核，-m e2e）。

演示页的 moving-btn 在加载后 ~30ms 移动一次再稳定；wait_until_stable 应等到
其移动完成并稳定后才返回，返回时元素已处于最终位置。
"""

import pytest

from tests.e2e.pages.popup_page import PopupPage

pytestmark = pytest.mark.e2e


def test_wait_until_stable_on_moving_element(healing_page):
    demo = PopupPage(healing_page)
    demo.open()
    loc = healing_page.locator('[data-testid="moving-btn"]', description="移动按钮")
    loc.wait_until_stable(timeout_ms=5000, stable_ms=300, poll_ms=50)
    # 元素已移动并稳定：最终 y 应已下移（margin-top 60px + body margin）
    assert loc.bounding_box()["y"] >= 60
