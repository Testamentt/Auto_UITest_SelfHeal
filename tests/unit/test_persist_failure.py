"""单元测试：H3 知识库沉淀→复用闭环 + 沉淀失败容忍（不依赖浏览器）。

覆盖：run() 策略命中→_persist 真实写入→二次 run() 命中知识短路（写→读闭环）；
以及 add_repair 抛异常时 run() 仍返回 success（沉淀失败被容忍，R4 记日志不静默）。
"""

import pytest

from selfheal.agent.orchestrator import SelfHealOrchestrator
from selfheal.config import Settings
from selfheal.knowledge.base import KnowledgeBackend
from selfheal.knowledge.store import KnowledgeStore

pytestmark = pytest.mark.unit

_DOM = '<html><body><button data-testid="submit-btn" aria-label="登录按钮">登录</button></body></html>'


class _HealPage:
    """页面：content 含可交互元素，locator 按 selector 是否存在返回 count。"""

    _PRESENT = {'[data-testid="submit-btn"]'}

    @property
    def url(self):
        return "file:///demo"

    def screenshot(self, **k):
        return b""

    def content(self):
        return _DOM

    def locator(self, sel, **k):
        class _Loc:
            def count(self):
                return 1 if sel in _HealPage._PRESENT else 0

        return _Loc()


class _ThrowingStore(KnowledgeBackend):
    """add_repair 抛异常、其余正常，验证沉淀失败被容忍。"""

    def __init__(self):
        self.repairs = []

    def add_repair(self, case):
        raise RuntimeError("disk full")

    def add_popup(self, feature):
        self.repairs.append(feature)

    def find_repair(self, original_selector, dom_fingerprint=None):
        return None

    def find_popup(self, signature):
        return None

    def count_popups(self):
        return 0


def _orch(store=None):
    return SelfHealOrchestrator(_HealPage(), Settings(), knowledge=store or KnowledgeStore())


def test_persist_then_reuse_closed_loop():
    """修复成功→沉淀→下次命中知识短路（strategy=knowledge），形成写→读闭环。"""
    store = KnowledgeStore()
    orch = _orch(store)

    first = orch.run("#submit-btn-old", "登录按钮")
    assert first.success
    assert first.strategy == "heuristic"  # 首次走策略
    # B1：run() 仅暂存，引擎层重试成功后 commit（单测直连 run，手动模拟引擎提交）
    orch.commit_pending(first.attempt_id)

    # 写侧：真实沉淀出的案例字段正确
    case = store.find_repair("#submit-btn-old")
    assert case is not None
    assert case.new_selector == '[data-testid="submit-btn"]'
    assert case.strategy == "heuristic"
    assert 0.0 <= case.confidence <= 1.0
    assert case.dom_fingerprint is not None

    # 读侧：二次 run() 命中知识短路
    second = orch.run("#submit-btn-old", "登录按钮")
    assert second.success
    assert second.strategy == "knowledge"


def test_persist_failure_tolerated():
    """add_repair 抛异常 → commit 不抛、run() 仍返回 success（沉淀失败不影响已成功的自愈）。"""
    orch = SelfHealOrchestrator(_HealPage(), Settings(), knowledge=_ThrowingStore())
    out = orch.run("#submit-btn-old", "登录按钮")
    assert out.success
    assert out.strategy == "heuristic"
    orch.commit_pending(out.attempt_id)  # 沉淀失败在 commit 阶段被容忍（_persist 内部捕获 + warning）
