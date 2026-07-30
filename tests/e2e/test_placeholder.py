"""端到端测试：需要 Playwright 浏览器内核（pytest -m e2e）。

TODO: 用 engine.BrowserManager + HealingLocator 编写真实自愈场景用例，
例如：故意使用失效定位器，断言框架自愈成功。
"""

import pytest


@pytest.mark.e2e
def test_placeholder():
    pytest.skip("骨架阶段占位：待接入真实页面与自愈场景")
