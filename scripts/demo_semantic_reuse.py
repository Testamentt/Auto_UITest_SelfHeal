#!/usr/bin/env python
"""Phase 5 知识库语义化「越用越聪明」独立演示（无真实浏览器）。

用可控 fake page + 临时 SQLite 纯逻辑、确定性演示三类命中：
  1) 首次修复并沉淀 —— 旧 UI 改版、旧定位器失效，启发式凭描述重新定位 → 知识沉淀（含向量/指纹）。
  2) L1 精确命中复用 —— 同场景再次失败 → repair_key 硬短路，不跑策略直接复用。
  3) L3 语义向量检索 —— 结构微变（按钮移入表单，标签路径变了 → L1 键不等）→ 语义相似命中，打印 sim。

运行：python scripts/demo_semantic_reuse.py
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from selfheal.agent.diagnose import FailureContext  # noqa: E402
from selfheal.agent.dom import (  # noqa: E402
    ElementContext,
    compute_page_fingerprint,
    compute_repair_key,
)
from selfheal.agent.orchestrator import HealOutcome, SelfHealOrchestrator  # noqa: E402
from selfheal.agent.strategies.semantic import SemanticStrategy  # noqa: E402
from selfheal.collect.collector import Scene  # noqa: E402
from selfheal.config import Settings  # noqa: E402
from selfheal.knowledge.schema import RepairCase  # noqa: E402
from selfheal.llm.embedding import NgramEmbedding  # noqa: E402

DEMO_URL = "https://demo/login"
DOM = '<html><body><button data-testid="submit-btn" aria-label="登录按钮">登录</button></body></html>'
NEW_SELECTOR = '[data-testid="submit-btn"]'


class _FakePage:
    """可控页面：url/content/screenshot/evaluate/locator 均模拟，无需真实浏览器。

    - evaluate 未配置的 selector 返回 None → 现场提取走 DOM 快照 / 静态兜底。
    - locator 只在 present 集合内的 selector 存在（模拟"旧 id 已失效、新定位器可用"）。
    """

    def __init__(self, url: str, dom: str, present: set[str]):
        self._url = url
        self._dom = dom
        self._present = present

    @property
    def url(self):
        return self._url

    def screenshot(self, **k):
        return b""

    def content(self):
        return self._dom

    def evaluate(self, js, sel):
        return None  # 旧 id 在 DOM 中已不存在 → 走快照/静态提取

    def locator(self, sel, **k):
        present = self._present

        class _Loc:
            def count(self):
                return 1 if sel in present else 0

        return _Loc()


def _timeout() -> FailureContext:
    return FailureContext(failure_type="TimeoutError", message="定位超时：元素不可交互")


def _print_outcome(label: str, out: HealOutcome) -> None:
    status = "[成功]" if out.success else "[失败]"
    print(f"  → {label}")
    print(f"     策略={out.strategy or '-'} 置信度={out.confidence:.2f} 来源={out.root_cause or '-'} {status}")
    if out.new_selector:
        print(f"     新定位器 = {out.new_selector}")


def _demo(db_path: str) -> None:
    settings = Settings()
    settings.knowledge.backend = "sqlite"
    settings.knowledge.path = db_path

    page_fp = compute_page_fingerprint(DEMO_URL, DOM)

    # ---------- 场景 1 · 首次修复并沉淀 ----------
    print("\n[场景 1] 旧 UI 改版，旧定位器 #submit-btn-old 失效 → 启发式凭描述重新定位并沉淀")
    page1 = _FakePage(url=DEMO_URL, dom=DOM, present={NEW_SELECTOR})
    orch1 = SelfHealOrchestrator(page1, settings)
    out1 = orch1.run("#submit-btn-old", description="登录按钮", failure=_timeout())
    _print_outcome("首次自愈", out1)
    kb = orch1._knowledge
    print(f"  已沉淀 {kb.count_repairs()} 条修复案例（含 page_fingerprint / repair_key / embedding）")

    # ---------- 场景 2 · L1 精确命中复用（跨"会话"持久化） ----------
    print("\n[场景 2] 另一会话同场景再次失败 → L1 repair_key 硬短路，不跑策略直接复用")
    page2 = _FakePage(url=DEMO_URL, dom=DOM, present={NEW_SELECTOR})
    orch2 = SelfHealOrchestrator(page2, settings)  # 新实例，共享同一 SQLite → 模拟跨会话
    out2 = orch2.run("#submit-btn-old", description="登录按钮", failure=_timeout())
    _print_outcome("二次自愈（知识复用）", out2)
    assert out2.root_cause == "cached_l1", f"期望 L1 硬短路，实际 {out2.root_cause}"

    # ---------- 场景 3 · L3 语义向量检索（结构微变） ----------
    print("\n[场景 3] 结构微变：按钮被新增 <form> 包裹（路径多一层 → L1 键不等）→ L3 语义向量命中")
    # 上一"会话"沉淀：同文本、按钮直接位于 div 内的登录按钮（模拟历史修复）
    seed_ctx = ElementContext(text="登录", tag_path="html>body>div>button", source="live")
    kb.add_repair(
        RepairCase(
            original_selector="#submit-btn",
            new_selector=NEW_SELECTOR,
            strategy="heuristic",
            confidence=0.9,
            page_url=DEMO_URL,
            page_fingerprint=page_fp,
            repair_key=compute_repair_key(page_fp, "登录", "html>body>div>button"),
            embedding=NgramEmbedding().embed(seed_ctx.query_text),
            embedding_version="v1-ngram",
            created_at=datetime.now(timezone.utc).isoformat(),  # 新鲜窗口 → sim>0.80 自动采纳
        )
    )
    # 本次失败：同文本、按钮被 form 包裹（L1 键因路径多一层而不等）→ 交给 L3 向量检索
    try:
        query_ctx = ElementContext(text="登录", tag_path="html>body>div>form>button", source="live")
        strat = SemanticStrategy(
            knowledge=kb, embedding=NgramEmbedding(), page_fingerprint=page_fp, element_context=query_ctx
        )
        cand = strat.repair(Scene(url=DEMO_URL), "#submit-btn", "登录")
        assert cand is not None and cand.strategy == "semantic", "L3 语义检索应命中"
        print(f"  → L3 命中：新定位器 = {cand.selector}  策略 = {cand.strategy}  置信度(sim) = {cand.confidence:.3f}")
        print("  （L3 依据文本+兄弟+标签路径的语义相似复用历史修复，无需启发式/LLM 重新定位）")
    finally:
        # 关闭 SQLite 连接（Windows 下不关会锁文件，导致临时库无法清理）
        orch1._knowledge.close()
        orch2._knowledge.close()

    print("\n" + "=" * 70)
    print("结论：知识库语义化让「越用越聪明」成立 —— 首次修复沉淀后，")
    print("L1 精确命中（硬短路 <50ms）与 L3 语义检索（跨结构复用）都能直接复用，")
    print("无需重复调用启发式/LLM/VLM，降低成本与延迟。")
    print("=" * 70)


def main() -> int:
    # Windows 控制台默认 GBK，强制 stdout 用 UTF-8，中文/符号正常显示
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 70)
    print("AutoAiSelfHeal · Phase 5 知识库语义化「越用越聪明」演示")
    print("=" * 70)
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _demo(db_path)
    finally:
        with contextlib.suppress(Exception):
            os.unlink(db_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
