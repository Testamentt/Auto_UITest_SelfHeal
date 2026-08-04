"""端到端冒烟：真实 LLM 语义定位（有 API key 才跑，否则 skip）。

验证语义策略在真实模型下能命中演示页的登录按钮，并在报告中产生 semantic 记录。
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
        json.dumps([asdict(r) for r in healing_page.reporter.records], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
