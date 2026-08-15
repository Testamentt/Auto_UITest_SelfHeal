"""单元测试：PopupGuard 纯函数（签名归一化 / 关闭按钮识别 / 稳定定位器投影，不依赖浏览器）。"""

import pytest

from selfheal.engine.popup_guard import PopupGuard, _is_close_hint, _normalize_signature

pytestmark = pytest.mark.unit


class _FakeLoc:
    """模拟关闭按钮 Locator：按属性返回投影值。"""

    def __init__(self, attrs=None, text=""):
        self._attrs = attrs or {}
        self._text = text

    def get_attribute(self, name):
        return self._attrs.get(name)

    def inner_text(self):
        return self._text


def test_signature_normalizes_whitespace_and_truncates():
    assert _normalize_signature("  欢迎 ！\n请先关闭 ") == "欢迎！请先关闭"
    assert _normalize_signature("x" * 100) == "x" * 50  # 截断到 50
    assert _normalize_signature(None) is None
    assert _normalize_signature("   ") is None


def test_is_close_hint_matches_keywords():
    assert _is_close_hint("关闭弹窗", "", "") is True
    assert _is_close_hint("", "popup-close", "") is True
    assert _is_close_hint("", "", "Dismiss") is True
    assert _is_close_hint("", "", "×") is True
    assert _is_close_hint("提交", "submit-btn", "确定") is False


def test_is_close_hint_excludes_cancel():
    # 取消/确定是业务动作，不应被当作"关闭弹窗"（避免误关合法对话框）
    assert _is_close_hint("", "", "取消") is False
    assert _is_close_hint("", "", "cancel") is False


def test_is_close_hint_case_insensitive():
    assert _is_close_hint("CLOSE", "", "") is True
    assert _is_close_hint("", "Popup-CLOSE", "") is True


# --- _stable_selector：弹窗特征沉淀的定位器投影（审查 M2） ---


def test_stable_selector_text_button():
    """纯文本"关闭"按钮（无 testid/id）→ 生成 text= 定位器（修复前返回 None、无法沉淀）。"""
    loc = _FakeLoc(text="关闭")
    assert PopupGuard._stable_selector(loc) == 'text="关闭"'


def test_stable_selector_testid_preferred():
    """data-testid 优先于文本。"""
    loc = _FakeLoc(attrs={"data-testid": "popup-close"}, text="关闭")
    assert PopupGuard._stable_selector(loc) == '[data-testid="popup-close"]'


def test_stable_selector_id_fallback():
    loc = _FakeLoc(attrs={"id": "close-btn"}, text="关闭")
    assert PopupGuard._stable_selector(loc) == "#close-btn"


def test_stable_selector_aria_fallback():
    """无文本无 id → aria-label 兜底。"""
    loc = _FakeLoc(attrs={"aria-label": "关闭弹窗"})
    assert PopupGuard._stable_selector(loc) == '[aria-label="关闭弹窗"]'


def test_stable_selector_none_without_any_hint():
    assert PopupGuard._stable_selector(_FakeLoc()) is None
