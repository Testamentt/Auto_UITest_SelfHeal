"""单元测试：D6 兜底分支（不依赖浏览器）。"""

import pytest

from selfheal.agent.orchestrator import HealOutcome
from selfheal.config import HealingConfig
from selfheal.engine.healing_locator import HealingFailedError, HealingLocator

pytestmark = pytest.mark.unit


class _Resolved:
    def __init__(self, selector: str):
        self.selector = selector

    def click(self, *a, **k):
        return f"clicked:{self.selector}"


class _FakePage:
    def locator(self, selector, **k):
        return _Resolved(selector)


class _FakeOrch:
    def __init__(self, outcome: HealOutcome):
        self._outcome = outcome

    def run(self, selector, description=None, failure=None):
        return self._outcome


def _make(outcome, *, on_uncertain="use_fallback", fallback=None):
    cfg = HealingConfig(on_uncertain=on_uncertain)
    return HealingLocator(
        object(),
        _FakePage(),
        "#old",
        cfg,
        _FakeOrch(outcome),
        fallback=fallback,
        description=None,
        enabled=True,
    )


def test_uses_healed_selector():
    hl = _make(
        HealOutcome(
            success=True, new_selector='[data-testid="x"]', confidence=0.9, strategy="heuristic"
        )
    )
    assert hl._heal_and_resolve().click() == 'clicked:[data-testid="x"]'


def test_use_fallback_when_uncertain():
    hl = _make(HealOutcome(success=False), on_uncertain="use_fallback", fallback="#fb")
    assert hl._heal_and_resolve().click() == "clicked:#fb"


def test_fail_when_uncertain_and_no_fallback():
    hl = _make(HealOutcome(success=False), on_uncertain="use_fallback")
    with pytest.raises(HealingFailedError):
        hl._heal_and_resolve()


def test_fail_mode_ignores_fallback():
    hl = _make(HealOutcome(success=False), on_uncertain="fail", fallback="#fb")
    with pytest.raises(HealingFailedError):
        hl._heal_and_resolve()
