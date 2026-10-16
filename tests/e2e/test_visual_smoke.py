"""端到端冒烟：真实 VLM 视觉定位（有 DASHSCOPE_API_KEY 且装了 openai 才跑）。

直接构造 VisualStrategy + 真实视觉客户端，对演示页截图做视觉定位，验证
qwen3-vl-flash 能识别"登录按钮"并返回候选集中真实存在的定位器。
无 key / 无 openai 时跳过，不影响 CI。

L19：本模块是**真实模型调用**（外呼付费 API），打 `live` marker——常规 e2e
（`-m "e2e and not erp and not live"`）不会跑到它，显式 `-m live` 才跑。
"""

import os

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.live]

try:
    import openai  # noqa: F401

    _HAS_OPENAI = True
except ImportError:
    _HAS_OPENAI = False


@pytest.fixture(autouse=True)
def _require_vision_env():
    """无 DASHSCOPE_API_KEY / 未装 openai → 跳过（L19 改造：模块级 skip → 用例级 skip）。

    模块级 skip 会在缺依赖的环境**收集不到**用例，`-m live` 隔离无法验证；
    本模块 `import openai` 本身是容错的（try/except），故收集恒可见、运行仍安全跳过。
    """
    if not (os.getenv("DASHSCOPE_API_KEY") and _HAS_OPENAI):
        pytest.skip("未设置 DASHSCOPE_API_KEY 或未安装 openai（llm extra），跳过真实 VLM 冒烟")


def test_visual_locate_login_button(healing_page):
    from selfheal.agent.strategies.visual import VisualStrategy
    from selfheal.collect.collector import Scene
    from selfheal.config import load_settings
    from selfheal.llm.factory import get_vision_for_settings
    from tests.e2e.pages.demo_page import DemoPage

    client = get_vision_for_settings(load_settings())
    assert client is not None  # 有 key + openai（否则已被 _require_vision_env skip）

    demo = DemoPage(healing_page)
    demo.open()
    scene = Scene(
        url=healing_page.url,
        screenshot=healing_page.screenshot(full_page=True),
        dom_snapshot=healing_page.content(),
    )
    # VLM 输出非确定性：偶尔返回越界/幻觉/解析失败结果（护栏拒绝后为 None），
    # 生产上 orchestrator 会回退其他策略；冒烟测试单测 visual，故允许重试。
    strategy = VisualStrategy(client=client)
    cand = None
    for _attempt in range(3):
        cand = strategy.repair(scene, "#submit-btn-old", description="登录按钮")
        if cand is not None:
            break

    # 真实 VLM 应能从候选中识别出登录按钮（护栏保证 selector 真实存在）
    assert cand is not None
    assert cand.strategy == "visual"
    assert cand.confidence >= 0.3

    # T2 证据留存：截图 + VLM 定位结果（reports/ 已 gitignore，如需展示可另行提交）
    import json
    from pathlib import Path

    evidence_dir = Path("reports/evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "visual_scene.png").write_bytes(scene.screenshot)
    (evidence_dir / "visual_result.json").write_text(
        json.dumps(
            {
                "description": "登录按钮",
                "original_selector": "#submit-btn-old",
                "healed_selector": cand.selector,
                "strategy": cand.strategy,
                "confidence": cand.confidence,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
