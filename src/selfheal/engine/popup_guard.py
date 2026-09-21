"""弹窗处理。

识别并自动关闭系统弹窗、权限申请、运营浮层等"突袭"，是提升通过率的关键能力。
策略：**知识优先**——先查弹窗特征库，命中则直接用沉淀的关闭方式；未命中则在弹窗内
启发式找关闭按钮（aria-label / 文本 / data-testid 含关闭类关键词），点击成功后沉淀特征。

纯函数 _normalize_signature / _is_close_hint / _close_hint_strength 抽离于类外，便于无浏览器单测。

2026-10-08 审查修复（H1）：
1. **关闭点击限定在弹窗容器内**：此前知识命中后走 `page.locator(sel).first`（全页范围），
   一旦沉淀的选择器与实际页面错位，就可能点到弹窗之外的业务控件。
2. **关键词从"子串"升级为分级命中**：`_is_close_hint`（子串）保留为兼容入口，但找按钮改为
   `_close_hint_strength` 打分——精确标签/无歧义符号（关闭 / close / × / ✕ …）为强信号，
   子串命中（"关闭订单"）降级为弱信号，仅在无强信号时兜底使用。
3. 实测校正（Playwright 语义）：`selector_builder` 产出的 `text="关闭"` 是**精确匹配**
   （带引号的 text 选择器：整串相等且大小写敏感），`text=关闭` 才是子串匹配；
   故沉淀选择器本身不是通配风险，风险在"未限定作用域"与"关键词子串误判"两点。
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
# 强信号标签：**整串**等于下列之一才算"确定是关闭控件"（避免 "关闭订单" 这类业务文案命中）
_EXACT_CLOSE_LABELS = frozenset(
    {
        "关闭",
        "關閉",
        "关闭弹窗",
        "关闭窗口",
        "关闭对话框",
        "关闭提示",
        "close",
        "close dialog",
        "close window",
        "close popup",
        "close modal",
        "dismiss",
        "dismiss dialog",
        "×",
        "✕",
        "x",
    }
)
_STRENGTH_NONE = 0
_STRENGTH_WEAK = 1  # 子串命中：可能是业务文案（"关闭订单"），仅在无强信号时兜底
_STRENGTH_STRONG = 2  # 整串命中精确标签 / 无歧义符号
_CLICK_TIMEOUT_MS = 2000


def _normalize_signature(text: str | None) -> str | None:
    """把弹窗文本归一化为签名（去空白、截断），供知识库匹配。"""
    if not text:
        return None
    sig = "".join(text.split())[:50]
    return sig or None


def _normalize_label(value: str | None) -> str:
    """归一化候选标签：去首尾空白、折叠内部空白、转小写（比较用）。"""
    return " ".join((value or "").split()).lower()


def _close_hint_strength(label: str, testid: str, text: str) -> int:
    """给"关闭按钮"候选打分（H1）：2=强（精确标签/无歧义符号），1=弱（子串），0=不命中。

    打分而非布尔判定，是为了让"整串就是关闭"的控件优先于"文案里含关闭"的业务按钮
    （如"关闭订单"）——后者只在弹窗内找不到任何强信号时才兜底使用。
    """
    fields = [_normalize_label(label), _normalize_label(testid), _normalize_label(text)]
    if any(field and field in _EXACT_CLOSE_LABELS for field in fields):
        return _STRENGTH_STRONG
    haystack = f"{label} {testid} {text}".lower()
    if any(keyword in haystack for keyword in _CLOSE_KEYWORDS):
        return _STRENGTH_WEAK
    return _STRENGTH_NONE


def _is_close_hint(label: str, testid: str, text: str) -> bool:
    """候选是否"像"关闭按钮（兼容入口：任意强度的命中都算 True）。"""
    return _close_hint_strength(label, testid, text) > _STRENGTH_NONE


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

        # 1) 知识优先：命中已沉淀的弹窗特征，直接用其关闭定位器（**限定在弹窗容器内**，H1）。
        #    知识读取 best-effort：失败按未命中继续走启发式。
        if self._knowledge is not None and signature:
            feature = self._safe_find_popup(signature)
            if feature is not None and self._try_click_selector(
                container, feature.dismiss_selector
            ):
                return True

        # 2) 启发式：在弹窗内找关闭按钮并点击
        close_btn = self._find_close_button(container)
        if close_btn is None:
            return False
        dismiss_selector = self._stable_selector(close_btn)
        try:
            close_btn.click(timeout=_CLICK_TIMEOUT_MS)
        except Exception:  # noqa: BLE001 - 点击失败视为未处理，交后续自愈
            logger.warning("弹窗关闭按钮点击失败，交后续自愈处理", exc_info=True)
            return False
        # 3) 沉淀特征（仅当能生成可复用定位器时）。沉淀 best-effort：失败仅记日志、不影响关闭结果。
        if self._knowledge is not None and signature and dismiss_selector:
            self._safe_add_popup(
                PopupFeature(signature=signature, dismiss_selector=dismiss_selector)
            )
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
        """在弹窗内找关闭按钮：强信号优先，弱信号（子串命中）仅作兜底（H1）。"""
        candidates = container.locator("button, a, [role='button']")
        weak_fallback: Locator | None = None
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
            strength = _close_hint_strength(label, testid, text)
            if strength == _STRENGTH_STRONG:
                return el
            if strength == _STRENGTH_WEAK and weak_fallback is None:
                weak_fallback = el
                logger.debug("弹窗关闭按钮仅弱信号命中（文案含关闭关键词），作为兜底候选")
        return weak_fallback

    def _try_click_selector(self, container: Locator, selector: str) -> bool:
        """按沉淀的选择器关闭弹窗（**限定在弹窗容器内**，H1：防误点弹窗外的业务控件）。"""
        try:
            loc = container.locator(selector)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=_CLICK_TIMEOUT_MS)
                return True
        except Exception:  # noqa: BLE001 - 关闭失败返回 False，交启发式兜底
            logger.warning(
                "弹窗知识定位器点击失败，转启发式（selector=%r）", selector, exc_info=True
            )
        return False

    @staticmethod
    def _safe_text(loc: Locator) -> str:
        try:
            return (loc.inner_text() or "").strip()
        except Exception:  # noqa: BLE001 - 读取文本失败返回空串
            return ""

    @staticmethod
    def _stable_selector(loc: Locator) -> str | None:
        """为关闭按钮生成可复用的稳定定位器（复用 dom 公共工具：data-testid > id > 文本 > aria）。

        审查 M2：除 data-testid/id 外，还投影 aria-label 与文本——
        纯文本"关闭"按钮（无 testid/id）此前无法沉淀弹窗特征，每次都要启发式重找。

        注（2026-10-08 实测）：`text="关闭"` 是 **精确匹配**（带引号的 text 选择器要求整串相等、
        大小写敏感），`text=关闭` 才是子串匹配——故此处生成的选择器不会扩大到"关闭订单"。
        """
        try:
            dom_el = Element(
                "",
                [
                    ("data-testid", loc.get_attribute("data-testid")),
                    ("id", loc.get_attribute("id")),
                    ("aria-label", loc.get_attribute("aria-label")),
                ],
            )
            dom_el.text = (loc.inner_text() or "").strip()
            return build_stable_selector(dom_el)
        except Exception:  # noqa: BLE001 - 读取属性失败则无法沉淀
            return None
