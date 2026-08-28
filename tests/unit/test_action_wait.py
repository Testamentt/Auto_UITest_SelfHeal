"""单元测试：T6 动作前置智能等待（healing.action_wait，不触网/不依赖浏览器）。

覆盖：
1. 配置模型：默认关闭（零侵入，守决策 D12）+ 参数默认值；
2. 关闭时动作不做前置等待（直接透传原生动作）；
3. 开启时动作前先做短稳等待（wait_until_stable 被调用）；
4. 前置等待失败不阻塞动作（best-effort，等待是增强不是正确性前提）；
5. POM 显式 wait_until_stable 在开关两态下都可用。
"""

import pytest

from selfheal.config import Settings
from selfheal.engine.healing_locator import HealingLocator

pytestmark = pytest.mark.unit


class _FakeLocator:
    """可观察的假 Locator：记录 wait_for / bounding_box / click 调用，box 恒定=立即稳定。"""

    def __init__(self):
        self.wait_for_calls: list[tuple] = []
        self.box_calls = 0
        self.click_calls = 0
        self.raise_on_box = False

    def wait_for(self, state=None, timeout=None):
        self.wait_for_calls.append((state, timeout))

    def bounding_box(self):
        self.box_calls += 1
        if self.raise_on_box:
            raise RuntimeError("box 读取失败（模拟重渲染瞬间）")
        return {"x": 0, "y": 0, "width": 10, "height": 10}

    def click(self, **kwargs):
        self.click_calls += 1
        return "clicked"


def _locator(cfg, enabled: bool = True):
    """构造带假 locator 的 HealingLocator（动作成功路径不触碰 page/orchestrator）。"""
    fake = _FakeLocator()
    hl = HealingLocator(
        fake,
        page=object(),
        selector="#x",
        cfg=cfg,
        orchestrator=None,
        enabled=enabled,
    )
    return hl, fake


def test_action_wait_default_off():
    """默认关闭：既有无需确认——动作不额外等待（决策 D12 不改默认行为）。"""
    aw = Settings().healing.action_wait
    assert aw.enabled is False
    assert aw.timeout_ms == 2000
    assert aw.stable_ms == 300


def test_wait_skipped_when_disabled():
    """关闭时 click 直通原生动作，无任何前置等待调用。"""
    hl, fake = _locator(Settings().healing)
    assert hl.click() == "clicked"
    assert fake.wait_for_calls == []
    assert fake.click_calls == 1


def test_wait_before_action_when_enabled():
    """开启时 click 前先做短稳等待（wait_until_stable 被调用），再执行动作。"""
    s = Settings()
    s.healing.action_wait.enabled = True
    s.healing.action_wait.stable_ms = 50
    hl, fake = _locator(s.healing)
    assert hl.click() == "clicked"
    assert fake.wait_for_calls  # 等待发生：wait_until_stable 内部先 wait_for(state="visible")
    assert fake.click_calls == 1  # 动作随后执行


def test_wait_failure_does_not_block_action():
    """前置等待失败（元素不可测/不存在）→ 仅记日志，动作照常执行（不变成新超时源）。"""
    s = Settings()
    s.healing.action_wait.enabled = True
    hl, fake = _locator(s.healing)
    fake.raise_on_box = True
    assert hl.click() == "clicked"  # 等待抛错被吞，动作未受阻塞
    assert fake.click_calls == 1


def test_disabled_locator_passes_through_even_if_cfg_enabled():
    """HealingLocator(enabled=False)（插件关闭）即使配置开启也不包装/不代表执行前置等待。"""
    s = Settings()
    s.healing.action_wait.enabled = True
    hl, fake = _locator(s.healing, enabled=False)
    assert hl.click() == "clicked"  # 直通原生（不包装 → 无前置等待逻辑）
    assert fake.wait_for_calls == [] and fake.click_calls == 1


def test_explicit_wait_until_stable_still_available():
    """POM 显式 wait_until_stable（smart_wait）在开关两态下都可用（T6 不削弱显式调用）。"""
    hl, fake = _locator(Settings().healing)
    hl.wait_until_stable(timeout_ms=1000, stable_ms=50)
    assert fake.wait_for_calls  # 显式等待照常生效
