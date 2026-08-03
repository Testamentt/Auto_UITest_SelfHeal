"""单元测试：智能等待稳定判定（fake locator，不依赖浏览器）。"""

import pytest

from selfheal.engine.smart_wait import wait_until_stable

pytestmark = pytest.mark.unit


class _FakeLocator:
    """模拟元素：按序弹出 bounding_box，耗尽后保持最后一个（稳定）。"""

    def __init__(self, boxes):
        self._boxes = list(boxes)
        self._last = boxes[-1] if boxes else None

    def wait_for(self, state=None, timeout=None):
        pass

    def bounding_box(self):
        if self._boxes:
            return self._boxes.pop(0)
        return self._last


def test_stable_returns():
    box = {"x": 0, "y": 0, "width": 10, "height": 10}
    wait_until_stable(_FakeLocator([box]), timeout_ms=2000, stable_ms=50, poll_ms=10)


def test_position_change_then_stable():
    a = {"x": 0, "y": 0, "width": 10, "height": 10}
    b = {"x": 0, "y": 60, "width": 10, "height": 10}
    wait_until_stable(_FakeLocator([a, a, b]), timeout_ms=3000, stable_ms=50, poll_ms=10)


def test_never_stable_times_out():
    class _Jitter:
        _n = 0

        def wait_for(self, state=None, timeout=None):
            pass

        def bounding_box(self):
            self._n += 1
            return {"x": 0, "y": self._n, "width": 10, "height": 10}

    with pytest.raises(TimeoutError):
        wait_until_stable(_Jitter(), timeout_ms=200, stable_ms=500, poll_ms=10)
