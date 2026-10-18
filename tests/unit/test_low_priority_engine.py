"""单元测试：低优先级清单（docs/backlog/low-priority.md §一）engine 层修复。

用例命名对应清单条目（L1/L2…），便于"条目 ↔ 用例"双向检索：
- L1：代理对象（HealingLocator / HealingPage）在内部字段未初始化时不递归，按属性缺失处理；
- L2：HealingPage 自愈边界——行为（page.get_by_* / page.click(sel) 原样透传不自愈）
  与 docstring 契约（边界必须写在类与 locator() 的 docstring 里）。

纯逻辑用例，不依赖浏览器与网络。
"""

import copy

import pytest

from selfheal.config import HealingConfig, Settings
from selfheal.engine.healing_locator import HealingLocator
from selfheal.knowledge.store import KnowledgeStore
from selfheal.plugin.healing_page import HealingPage  # M10-②：装配层在 plugin/
from selfheal.reporting.hooks import HealingReporter

pytestmark = pytest.mark.unit


class _FakeLocator:
    """最小 Locator fake：click 计次，链式方法返回自身类型（供链包裹判定）。"""

    def __init__(self):
        self.clicks = 0

    def click(self, *args, **kwargs):
        self.clicks += 1
        return "clicked"

    def locator(self, selector, **kwargs):
        return _FakeLocator()

    def nth(self, index):
        return _FakeLocator()

    def get_by_role(self, role, **kwargs):
        return _FakeLocator()

    def count(self):
        return 1


class _FakePage:
    """最小 Page fake：记录原生 click 调用，get_by_* 返回原生（非自愈）Locator。"""

    def __init__(self):
        self.raw_clicks: list[str] = []
        self.locator_calls: list[str] = []

    @property
    def url(self):
        return "file:///demo"

    def locator(self, selector, **kwargs):
        self.locator_calls.append(selector)
        return _FakeLocator()

    def get_by_role(self, role, **kwargs):
        return _FakeLocator()

    def click(self, selector, **kwargs):
        self.raw_clicks.append(selector)
        return None


def _healing_locator(locator=None, page=None, selector="#old") -> HealingLocator:
    """构造一个不依赖浏览器/编排器的 HealingLocator（orchestrator=None 仅用于透传类断言）。"""
    return HealingLocator(
        locator or _FakeLocator(),
        page or _FakePage(),
        selector,
        HealingConfig(),
        None,  # M10-②：orchestrator 形参为 HealRunner | None；本组用例只走透传/拷贝路径
    )


# --- L1：代理对象 __getattr__ 递归风险 ---


def test_l1_copy_and_deepcopy_do_not_recurse():
    """L1：copy/deepcopy 探测 `__setstate__`/`__deepcopy__` 时不得无限递归（原为 RecursionError）。"""
    hl = _healing_locator()
    for copier in (copy.copy, copy.deepcopy):
        clone = copier(hl)  # 修复前：RecursionError（__getattr__("_enabled") 自递归）
        assert object.__getattribute__(clone, "_selector") == "#old"
        assert object.__getattribute__(clone, "_enabled") is True


def test_l1_getattr_before_init_raises_attribute_error():
    """L1：构造中途取属性（属性表未就绪）→ 抛 AttributeError，而非递归崩溃。"""
    hl = HealingLocator.__new__(HealingLocator)  # 跳过 __init__，模拟反序列化中途
    with pytest.raises(AttributeError):
        hl._enabled  # noqa: B018 - 断言点：取未初始化内部字段必须抛 AttributeError
    with pytest.raises(AttributeError):
        hl.click  # noqa: B018 - 任意未定义属性同样按缺失处理
    assert hasattr(hl, "_enabled") is False  # hasattr 协议不被破坏（拷贝/pickle 依赖它）
    assert hasattr(hl, "click") is False


def test_l1_getattr_missing_locator_field_raises_attribute_error():
    """L1：只有 _enabled、_locator 缺失（半初始化）时同样按属性缺失处理，不递归。"""
    hl = HealingLocator.__new__(HealingLocator)
    object.__setattr__(hl, "_enabled", True)
    with pytest.raises(AttributeError):
        hl.count  # noqa: B018 - _locator 缺失 → AttributeError（透传无源）


def test_l1_healing_page_getattr_before_init_raises_attribute_error():
    """L1：HealingPage 是同源的代理对象，内部字段未就绪时同样不得递归。"""
    hp = HealingPage.__new__(HealingPage)
    with pytest.raises(AttributeError):
        hp.click  # noqa: B018 - 断言点：_page 未就绪
    assert hasattr(hp, "_page") is False


# --- L2：自愈边界（行为 + docstring 契约） ---


def _enabled_healing_page() -> tuple[HealingPage, _FakePage]:
    """构造开启自愈的 HealingPage（注入内存知识库，避免落盘 SQLite 的文件副作用）。"""
    page = _FakePage()
    hp = HealingPage(page, Settings(), knowledge=KnowledgeStore(), reporter=HealingReporter())
    return hp, page


def test_l2_page_get_by_and_click_selector_are_not_healed():
    """L2：HealingPage 只代理 locator()；page.get_by_*(...) / page.click(sel) 原样透传不自愈。"""
    hp, page = _enabled_healing_page()
    try:
        assert hp.healing_enabled is True
        assert not isinstance(hp.get_by_role("button"), HealingLocator)  # 原生 Locator，无自愈包装
        hp.click("#raw")  # 原生点击：直接落到 page.click，不进入"失败→自愈→重试"
        assert page.raw_clicks == ["#raw"]
        assert isinstance(hp.locator("#wrapped"), HealingLocator)  # 唯一自愈入口
    finally:
        hp.close()


def test_l2_healing_locator_get_by_is_chain_healed():
    """L2（边界另一侧）：自愈入口内的 get_by_* 会作为链环被包裹，与 page.get_by_* 不同。"""
    hp, _page = _enabled_healing_page()
    try:
        derived = hp.locator("#panel").get_by_role("button", name="提交")
        assert isinstance(derived, HealingLocator)
        assert derived._leaf_selector == "[role=button] 提交"  # 链环记为叶子修复目标
    finally:
        hp.close()


def test_l2_boundary_documented_in_docstrings():
    """L2：边界写入 HealingPage 类 docstring 与 locator() docstring（防止再被读成"全 API 自愈"）。"""
    class_doc = HealingPage.__doc__ or ""
    method_doc = HealingPage.locator.__doc__ or ""
    for keyword in ("get_by_", "page.click", "expect", "不自愈"):
        assert keyword in class_doc, f"类 docstring 缺少边界说明: {keyword}"
        assert keyword in method_doc, f"locator() docstring 缺少边界说明: {keyword}"
