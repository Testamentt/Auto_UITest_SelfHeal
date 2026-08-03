"""端到端冒烟：真实 VLM 视觉定位（有 DASHSCOPE_API_KEY 且装了 openai 才跑）。

直接构造 VisualStrategy + 真实视觉客户端，对演示页截图做视觉定位，验证
qwen3-vl-flash 能识别"登录按钮"并返回候选集中真实存在的定位器。
无 key / 无 openai 时整模块跳过，不影响 CI。
"""

import os

import pytest

pytestmark = pytest.mark.e2e

try:
    import openai  # noqa: F401

    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False

if not (os.getenv("DASHSCOPE_API_KEY") and _HAS_OPENAI):
    pytest.skip(
        "未设置 DASHSCOPE_API_KEY 或未安装 openai，跳过真实 VLM 冒烟",
        allow_module_level=True,
    )


def test_visual_locate_login_button(healing_page):
    from selfheal.agent.strategies.visual import VisualStrategy
    from selfheal.collect.collector import Scene
    from selfheal.config import load_settings
    from selfheal.llm.factory import get_vision_for_settings
    from tests.e2e.pages.demo_page import DemoPage

    client = get_vision_for_settings(load_settings())
    assert client is not None  # 有 key + openai（否则已被模块级 skip）

    demo = DemoPage(healing_page)
    demo.open()
    scene = Scene(
        url=healing_page.url,
        screenshot=healing_page.screenshot(full_page=True),
        dom_snapshot=healing_page.content(),
    )
    cand = VisualStrategy(client=client).repair(scene, "#submit-btn-old", description="登录按钮")

    # 真实 VLM 应能从候选中识别出登录按钮（护栏保证 selector 真实存在）
    assert cand is not None
    assert cand.strategy == "visual"
    assert cand.confidence >= 0.3
