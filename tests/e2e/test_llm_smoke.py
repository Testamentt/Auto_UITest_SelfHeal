"""端到端冒烟：真实 LLM 语义定位（有 API key 才跑，否则 skip）。

- test_semantic_healing_smoke：验证**完整自愈链路**在真实环境可闭环（注意：演示页登录
  按钮带 aria-label，启发式早停即达，此用例不一定触发 LLM 调用）。
- test_semantic_llm_locate_login_button：**直怼语义策略**的真实 LLM 冒烟——绕开启发式
  短路直接构造 SemanticStrategy + 真实 LLMClient（deepseek-v4-flash），验证模型能依据
  精简 DOM 索引给出登录按钮的真实稳定定位器，并拿到真实置信度数据点（T5 收缩标定输入）。

无 OPENAI_API_KEY 时自动跳过，不影响 CI。
"""

import os

import pytest

from tests.e2e.pages.demo_page import DemoPage

pytestmark = pytest.mark.e2e

if not os.getenv("OPENAI_API_KEY"):
    pytest.skip("未设置 OPENAI_API_KEY，跳过真实 LLM 冒烟", allow_module_level=True)


def test_semantic_healing_smoke(healing_page):
    assert healing_page.healing_enabled is True
    demo = DemoPage(healing_page)
    demo.open()
    demo.login()  # 内部使用失效定位器 #submit-btn-old

    assert demo.result() == "logged-in"
    assert len(healing_page.reporter.records) >= 1
    rec = healing_page.reporter.records[0]
    assert rec.original_selector == "#submit-btn-old"

    # T2 证据留存：自愈记录（reports/ 已 gitignore，如需展示可另行提交）
    import json
    from dataclasses import asdict
    from pathlib import Path

    evidence_dir = Path("reports/evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "llm_healing_records.json").write_text(
        json.dumps(
            [asdict(r) for r in healing_page.reporter.records], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )


def test_semantic_llm_locate_login_button(healing_page):
    """真实 LLM 语义定位冒烟：直构 SemanticStrategy + 真实 client，绕开启发式早停。

    演示页登录按钮对语义策略是"硬场景"（T8 后 DOM 索引含 testid/aria 全量信息），
    真实模型应从候选集中挑出登录按钮的稳定定位器；结果受 3 处护栏约束
    （selector 必须在真实索引 / 置信度 0~1 / 返回解析），失败返回 None 允许重试。
    """
    from selfheal.agent.strategies.semantic import SemanticStrategy
    from selfheal.collect.collector import Scene
    from selfheal.config import load_settings
    from selfheal.llm.factory import get_llm_for_settings

    client = get_llm_for_settings(load_settings())
    assert client is not None  # 有 OPENAI_API_KEY（否则已被模块级 skip）

    demo = DemoPage(healing_page)
    demo.open()
    scene = Scene(
        url=healing_page.url,
        screenshot=healing_page.screenshot(full_page=True),
        dom_snapshot=healing_page.content(),
    )
    # LLM 输出非确定性：偶尔越界/幻觉/解析失败（护栏拒绝后为 None），允许重试；
    # 生产上 orchestrator 会回退其他策略，冒烟单测 semantic 所以给重试余量。
    strategy = SemanticStrategy(client=client)
    cand = None
    for _attempt in range(3):
        cand = strategy.repair(scene, "#submit-btn-old", description="登录按钮")
        if cand is not None:
            break

    # 真实 LLM 应能识别登录按钮且护栏保证 selector 真实存在
    assert cand is not None
    assert cand.strategy == "semantic"
    assert cand.confidence >= 0.3

    # 证据留存：语义定位结果（含真实置信度数据点，T5 收缩标定输入）
    import json
    from pathlib import Path

    evidence_dir = Path("reports/evidence")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "semantic_result.json").write_text(
        json.dumps(
            {
                "description": "登录按钮",
                "original_selector": "#submit-btn-old",
                "healed_selector": cand.selector,
                "strategy": cand.strategy,
                "confidence": cand.confidence,
                "note": "T5 默认恒等标尺；shrink_self_reported=True 时按 raw^2 保守收缩",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
