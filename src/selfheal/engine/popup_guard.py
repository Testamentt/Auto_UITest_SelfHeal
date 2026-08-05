"""弹窗处理。

识别并自动关闭系统弹窗、权限申请、运营浮层等"突袭"，是提升通过率的关键能力。
策略：**知识优先**——先查弹窗特征库，命中则直接用沉淀的关闭方式；未命中则在弹窗内
启发式找关闭按钮（aria-label / 文本 / data-testid 含关闭类关键词），点击成功后沉淀特征。

纯函数 _normalize_signature / _is_close_hint 抽离于类外，便于无浏览器单测。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from selfheal.agent.dom import Element, build_stable_selector
from selfheal.knowledge.base import KnowledgeBackend
from selfheal.knowledge.schema import PopupFeature

if TYPE_CHECKING:  # 仅类型检查时导入，避免运行期强依赖 playwright
    from playwright.sync_api import Locator, Page

logger = logging.getLogger(__name__)

# 常见弹窗容器特征（role / aria / 类名 / id 含弹窗语义）
_POPUP_CONTAINER_SELECTOR = (
    "[role='dialog'],[aria-modal='true'],.modal,.popup,.dialog,"
    "[class*='overlay'],[id*='overlay'],[id*='popup'],[id*='modal']"
)
# 关闭按钮的识别关键词（aria-label / data-testid / 文本，统一小写匹配）。
# 注意：刻意**不含**"取消/cancel"——取消是业务动作而非"关闭弹窗"，
# 避免把测试流程中合法的确认对话框（确定/取消）误当干扰弹窗点掉。
_CLOSE_KEYWORDS = ("关闭", "close", "dismiss", "×", "✕")
_CLICK_TIMEOUT_MS = 2000


def _normalize_signature(text: str | None) -> str | None:
    """把弹窗文本归一化为签名（去空白、截断），供知识库匹配。"""
    if not text:
        return None
    sig = "".join(text.split())[:50]
    return sig or None


def _is_close_hint(label: str, testid: str, text: str) -> bool:
    """判断某元素的 aria-label / testid / 文本是否含关闭类关键词。"""
    haystack = f"{label} {testid} {text}".lower()
    return any(kw in haystack for kw in _CLOSE_KEYWORDS)


class PopupGuard:
    """检测并自动关闭干扰弹窗；命中知识优先，未命中启发式找关闭按钮。"""

    def __init__(self, page: Page, knowledge: KnowledgeBackend | None = None):
        self._page = page
        self._knowledge = knowledge

    def dismiss_if_present(self) -> bool:
        """检测并关闭当前页面的干扰弹窗，返回是否处理了弹窗。"""
        container = self._find_visible_popup()
        if container is None:
            return False
        signature = _normalize_signature(self._safe_text(container))

        # 1) 知识优先：命中已沉淀的弹窗特征，直接用其关闭定位器。
        #    知识读取 best-effort：失败按未命中继续走启发式。
        if self._knowledge is not None and signature:
            feature = self._safe_find_popup(signature)
            if feature is not None and self._try_click_selector(feature.dismiss_selector):
                return True

        # 2) 启发式：在弹窗内找关闭按钮并点击
        close_btn = self._find_close_button(container)
        if close_btn is None:
            return False
        dismiss_selector = self._stable_selector(close_btn)
        try:
            close_btn.click(timeout=_CLICK_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - 点击失败视为未处理，交后续自愈
            return False
        # 3) 沉淀特征（仅当能生成可复用定位器时）。沉淀 best-effort：失败仅记日志、不影响关闭结果。
        if self._knowledge is not None and signature and dismiss_selector:
            self._safe_add_popup(PopupFeature(signature=signature, dismiss_selector=dismiss_selector))
        return True

    # --- 内部步骤 ---

    def _safe_find_popup(self, signature: str) -> PopupFeature | None:
        """知识库读取隔离：失败按未命中处理（不炸主流程）。"""
        try:
            return self._knowledge.find_popup(signature)
        except Exception:  # noqa: BLE001 - 读取失败按未命中继续启发式
            logger.warning("弹窗特征读取失败，转启发式", exc_info=True)
            return None

    def _safe_add_popup(self, feature: PopupFeature) -> None:
        """知识库沉淀隔离：失败仅记日志（关闭动作已成功，不影响返回 True）。"""
        try:
            self._knowledge.add_popup(feature)
        except Exception:  # noqa: BLE001 - 沉淀失败不影响主流程
            logger.warning("弹窗特征沉淀失败", exc_info=True)

    def _find_visible_popup(self) -> Locator | None:
        containers = self._page.locator(_POPUP_CONTAINER_SELECTOR)
        try:
            count = containers.count()
        except Exception:  # noqa: BLE001 - 页面不可用时 count 失败按无弹窗处理
            logger.warning("弹窗容器检测失败（页面可能不可用）", exc_info=True)
            return None
        for i in range(count):
            loc = containers.nth(i)
            try:
                if loc.is_visible():
                    return loc
            except Exception:  # noqa: BLE001 - 个别容器不可见判定失败则跳过
                continue
        return None

    def _find_close_button(self, container: Locator) -> Locator | None:
        candidates = container.locator("button, a, [role='button']")
        for i in range(candidates.count()):
            el = candidates.nth(i)
            try:
                if not el.is_visible():
                    continue
                label = el.get_attribute("aria-label") or ""
                testid = el.get_attribute("data-testid") or ""
                text = self._safe_text(el)
            except Exception:  # noqa: BLE001 - 单元素读取失败则跳过
                continue
            if _is_close_hint(label, testid, text):
                return el
        return None

    def _try_click_selector(self, selector: str) -> bool:
        try:
            loc = self._page.locator(selector)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=_CLICK_TIMEOUT_MS)
                return True
        except Exception:  # noqa: BLE001 - 关闭失败返回 False，交启发式兜底
            pass
        return False

    @staticmethod
    def _safe_text(loc: Locator) -> str:
        try:
            return (loc.inner_text() or "").strip()
        except Exception:  # noqa: BLE001 - 读取文本失败返回空串
            return ""

    @staticmethod
    def _stable_selector(el: Locator) -> str | None:
        """为关闭按钮生成可复用的稳定定位器（复用 dom 公共工具：data-testid > id > 文本 > aria）。"""
        try:
            # 把 live Locator 的关键属性投影为 dom.Element，复用共享的 build_stable_selector（评审 #6 去重）。
            # tag 不参与选择器生成（build_stable_selector 只用属性/text/aria），传占位。
            return build_stable_selector(
                Element(
                    "",
                    [
                        ("data-testid", el.get_attribute("data-testid")),
                        ("id", el.get_attribute("id")),
                    ],
                )
            )
        except Exception:  # noqa: BLE001 - 读取属性失败则无法沉淀
            return None
