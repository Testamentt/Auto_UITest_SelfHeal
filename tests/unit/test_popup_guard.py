"""单元测试：PopupGuard 纯函数（签名归一化 / 关闭按钮识别，不依赖浏览器）。"""

import pytest

from selfheal.engine.popup_guard import _is_close_hint, _normalize_signature

pytestmark = pytest.mark.unit


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
