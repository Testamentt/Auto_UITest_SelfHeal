"""单元测试：P1 链式定位自愈（C1 + C2 + C6，纯逻辑，不依赖浏览器）。

覆盖 HealingLocator 的链式定位能力：
- 链操作记录（chain_ops 元组）与递归包裹（first/last/nth/filter/locator/get_by_*）；
- 链派生不污染共享链（不可变元组拼接）；
- 第一次自愈修根 + 重放整条链（property 走 getattr、method 走调用）；
- 第二次自愈直接修叶子（覆盖中段/叶子断裂）；
- auto_description 自动生成与链提示截断（截断仅限描述，重放全量）；
- FrameLocator / 标量透传（跨 iframe 自愈不支持）。
"""

import pytest

from selfheal.agent.orchestrator import HealOutcome
from selfheal.config import HealingConfig
from selfheal.engine.healing_locator import HealingLocator, _is_locator_like

pytestmark = pytest.mark.unit


class _MyTimeout(Exception):
    """类名含 Timeout，模拟 playwright 超时（供无浏览器单测）。"""


class _Page:
    """selector → 链式 fake locator；broken 集合里的键（含链上任一层）使 click 超时。"""

    def __init__(self, broken=(), handlers=None, url=""):
        self.broken = set(broken)
        self.handlers = handlers or {}
        self.url = url
        self.locator_calls: list[str] = []  # page.locator(sel) 调用序列
        self.derived_ops: list[tuple] = []  # 链环应用记录（断言重放全量用）

    def locator(self, selector, **k):
        self.locator_calls.append(selector)
        return _ChainableLocator(self, (selector,))

    def page_goto(self, *_a, **_k):  # 占位，仅让 fake 更像 page
        return None


class _ChainableLocator:
    """模拟 Playwright 链式定位：locator/nth/filter/first/last 派生，click 可超时。

    keys 记录链上所有定位键（任一个 broken → click 超时），等价于 Playwright
    "作用域链任一环节失效则整链失败"的语义；handler 按最内层键解析。
    """

    def __init__(self, page, keys=(), chain=()):
        self._page = page
        self._keys = keys
        self._chain = chain  # (op_name, args, kwargs) 已应用记录，供断言

    def _derive(self, name, args=(), kwargs=None, new_key=None):
        keys = self._keys + ((new_key,) if new_key else ())
        if new_key is not None:
            self._page.derived_ops.append((name, new_key))
        return _ChainableLocator(self._page, keys, self._chain + ((name, args, kwargs or {}),))

    def click(self, *a, **k):
        if any(key in self._page.broken for key in self._keys):
            raise _MyTimeout(f"{self._keys} 不可定位")
        return self._page.handlers[self._keys[-1]]()

    def count(self):
        return len(self._page.handlers)

    def locator(self, sub, **k):
        return self._derive("locator", (sub,), k, new_key=sub)

    def nth(self, i):
        return self._derive("nth", (i,), {})

    def filter(self, **k):
        return self._derive("filter", (), k)

    def frame_locator(self, *_a, **_k):
        return _FrameLocatorSentinel()

    @property
    def first(self):
        return self._derive("first")

    @property
    def last(self):
        return self._derive("last")


class _FrameLocatorSentinel:
    """无 locator/nth/click → _is_locator_like 判 False（模拟 FrameLocator 透传）。"""


class _RecOrch:
    """记录 (sel, desc, use_knowledge) 调用，按序弹 HealOutcome。"""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls: list[tuple] = []

    def run(self, sel, desc, failure=None, use_knowledge=True):
        self.calls.append((sel, desc, use_knowledge))
        return self._outcomes.pop(0)


def _hl(page, selector="#form", **kw):
    """构造根 HealingLocator（默认开启自愈）。"""
    return HealingLocator(page.locator(selector), page, selector, HealingConfig(), _RecOrch([]), **kw)


# --- 链记录与递归包裹 ---


def test_chain_records_ops_and_leaf_target():
    page = _Page()
    hl = _hl(page)
    child = hl.locator("#btn")
    leaf = child.first
    assert hl._chain_ops == ()
    assert child._chain_ops == (("locator", ("#btn",), {}, False),)
    assert child._leaf_selector == "#btn"  # locator 引入新 selector → 叶子目标更新
    assert leaf._chain_ops == (("locator", ("#btn",), {}, False), ("first", (), {}, True))
    assert leaf._leaf_selector == "#btn"  # first 纯定位 → 沿用父叶子目标
    assert leaf._selector == "#form"  # 根选择器不变（第一次自愈修根用）


def test_chain_derivation_does_not_pollute_shared_chain():
    page = _Page()
    hl = _hl(page)
    c1 = hl.first
    c2 = hl.first
    assert hl._chain_ops == ()  # 原实例链不被污染
    assert c1 is not c2
    assert c1._chain_ops == (("first", (), {}, True),)
    assert c2._chain_ops == (("first", (), {}, True),)


def test_is_locator_like():
    page = _Page()
    assert _is_locator_like(_ChainableLocator(page, ("#x",))) is True
    assert _is_locator_like(None) is False
    assert _is_locator_like("string") is False
    assert _is_locator_like(3) is False
    assert _is_locator_like([1, 2]) is False
    assert _is_locator_like(_FrameLocatorSentinel()) is False  # 跨 iframe 透传


def test_frame_locator_and_scalar_passthrough():
    page = _Page(handlers={"#form": lambda: "x"})
    hl = _hl(page)
    # frame_locator 不在 CHAIN_METHODS，原样透传原生结果（不包裹、不进 chain_ops）
    assert isinstance(hl.frame_locator("#iframe"), _FrameLocatorSentinel)
    assert hl._chain_ops == ()
    # 标量（count）同样透传
    assert hl.count() == 1
    assert hl._chain_ops == ()


# --- 重试路径：修根 + 重放链 / 修叶子 ---


def test_root_broken_heals_root_and_replays_chain():
    """根选择器断裂：第一次自愈修根，重放整条链（含 first property）后成功。"""
    orch = _RecOrch([HealOutcome(success=True, new_selector="#form2", confidence=0.9)])
    page = _Page(broken={"#form"}, handlers={"#btn": lambda: "root-replayed-ok"})
    hl = HealingLocator(page.locator("#form"), page, "#form", HealingConfig(), orch).locator("#btn").first
    assert hl.click() == "root-replayed-ok"
    # 第一次自愈：修根 selector + 走知识库
    assert orch.calls[0][0] == "#form"
    assert orch.calls[0][2] is True
    # 重试基于修复后的根定位器（而非失效的 #form）
    assert page.locator_calls == ["#form", "#form2"]
    # first 是 property：若实现误按方法调用会抛 TypeError，能跑通即证明走 getattr


def test_leaf_broken_second_heal_targets_leaf():
    """中段/叶子选择器断裂：第一次修根+重放仍失败 → 第二次自愈直接修叶子。"""
    orch = _RecOrch(
        [
            HealOutcome(success=True, new_selector="#form", confidence=0.9),  # 修根（根未坏，返回原根）
            HealOutcome(success=True, new_selector="#btn2", confidence=0.9),  # 修叶子
        ]
    )
    page = _Page(broken={"#btn"}, handlers={"#btn2": lambda: "leaf-ok"})
    hl = HealingLocator(page.locator("#form"), page, "#form", HealingConfig(), orch).locator("#btn")
    assert hl.click() == "leaf-ok"
    # 第一次修根 sel='#form'（use_knowledge=True）；第二次修叶子 sel='#btn'（跳过缓存）
    assert [c[0] for c in orch.calls] == ["#form", "#btn"]
    assert [c[2] for c in orch.calls] == [True, False]
    # 叶子修复的描述携带链意图
    assert "locator" in orch.calls[1][1]
    # 重试用修复后的叶子定位器
    assert page.locator_calls == ["#form", "#form", "#btn2"]


def test_deep_chain_replays_all_ops():
    """深链（6 层 locator）重放全量：任何一环被跳过都会使 derived_ops 缺项。"""
    orch = _RecOrch([HealOutcome(success=True, new_selector="#r2", confidence=0.9)])
    page = _Page(broken={"#r"}, handlers={"#deep": lambda: "deep-ok"})
    hl = HealingLocator(page.locator("#r"), page, "#r", HealingConfig(), orch)
    for i in range(5):
        hl = hl.locator(f"#l{i}")
    hl = hl.locator("#deep")
    page.derived_ops.clear()  # 只统计重放期的链环应用
    assert hl.click() == "deep-ok"
    assert [key for _name, key in page.derived_ops] == ["#l0", "#l1", "#l2", "#l3", "#l4", "#deep"]


# --- auto_description（C1 + C6） ---


def test_auto_description_flows_to_run_when_no_user_desc():
    orch = _RecOrch([HealOutcome(success=True, new_selector="#form2", confidence=0.9)])
    page = _Page(broken={"#form"}, handlers={"#btn": lambda: "ok"}, url="https://demo/login")
    hl = HealingLocator(page.locator("#form"), page, "#form", HealingConfig(), orch).locator("#btn")
    hl.click()
    sel, desc, _use_knowledge = orch.calls[0]
    assert sel == "#form"
    assert desc is not None
    assert "元素" in desc and "#form" in desc and "https://demo/login" in desc
    assert "locator" in desc  # 链意图拼入自动描述


def test_user_description_preserved():
    orch = _RecOrch([HealOutcome(success=True, new_selector="#form2", confidence=0.9)])
    page = _Page(broken={"#form"}, handlers={"#btn": lambda: "ok"})
    hl = HealingLocator(
        page.locator("#form"), page, "#form", HealingConfig(), orch, description="提交按钮"
    ).locator("#btn")
    hl.click()
    assert orch.calls[0][1] == "提交按钮"  # 用户描述优先，不覆盖


def test_chain_hint_truncates_to_recent_3():
    """C6：链提示只保留最近 3 层 + 省略号（防描述稀释意图）。"""
    page = _Page()
    hl = _hl(page)
    for i in range(5):
        hl = hl.locator(f"#l{i}")
    hint = hl._chain_hint()
    assert hint.startswith("（经过 …")
    assert "#l4" in hint and "#l3" in hint and "#l2" in hint
    assert "#l0" not in hint and "#l1" not in hint  # 最早两层被截断


def test_chain_hint_short_chain_not_truncated():
    page = _Page()
    hl = _hl(page).locator("#btn").filter(has_text="提交")
    hint = hl._chain_hint()
    assert not hint.startswith("（经过 …")
    assert "locator('#btn')" in hint
    assert "filter(has_text='提交')" in hint
