"""管伊佳 ERP 商品管理页 POM（T23 P2）。

勘测确认（2026-08-31，jsh 账号）：
- 列表路由 /material/material（SPA 同文档渲染，内容不在 iframe）；
- 新增弹窗表单输入框带稳定 id（#name / #standard / #model / #unit），
  placeholder 齐全（"请输入商品名称"等）——heuristic 语义匹配的依据；
- 列表搜索框 placeholder="请输入名称、规格、型号等查询"；新增按钮 class ant-btn
  文本"新 增"（Ant Design 两字按钮自动插空格）。
"""

from __future__ import annotations

from selfheal.engine.base_page import BasePage
from tests.e2e.pages.erp import dismiss_intro


class MaterialPage(BasePage):
    """商品管理页：列表查询、新增弹窗、改版注入（自愈演示）。"""

    def __init__(self, page, base_url: str):
        super().__init__(page)
        self._base_url = base_url.rstrip("/")

    @property
    def url(self) -> str:
        return f"{self._base_url}/material/material"

    def open_list(self) -> None:
        self.open()

    def open_add_modal(self) -> None:
        """点击「新增」打开弹窗（Ant Design 两字按钮文本带空格，按含"新"匹配）。"""
        self.locator("button.ant-btn:has-text('新')", description="商品新增按钮").click()
        self.locator(".ant-modal #name", description="商品名称弹窗输入框").wait_for(state="visible")

    def fill_name(self, name: str) -> None:
        """填写商品名称（#name 故意用旧 id——改版注入后由自愈重定位；语义=placeholder）。"""
        self.locator("#name", description="商品名称").fill(name)

    def fill_others(self, *, standard: str = "标准", model: str = "M-1", unit: str = "个") -> None:
        """填写名称以外的表单字段（不受改版注入影响）。"""
        self.locator("#standard", description="商品规格").fill(standard)
        self.locator("#model", description="商品型号").fill(model)
        self.locator("#unit", description="商品单位").fill(unit)

    def save_modal(self) -> None:
        """保存弹窗（footer 主按钮；弹窗内另有 plus 新增主按钮，须限定 footer 避免歧义）。"""
        dismiss_intro(self.page)  # 保存前再清一次引导层（防 covered）
        self.locator(".ant-modal-footer button.ant-btn-primary", description="商品保存按钮").click()
        # 保存后弹窗关闭（等待 DOM 收敛，列表随后刷新）
        self.page.locator(".ant-modal:visible").wait_for(state="hidden", timeout=10_000)

    def row_visible(self, name: str, timeout: int = 10_000) -> bool:
        """表格中出现含 name 的行（保存成功后列表刷新可见）。"""
        try:
            self.page.locator(f"tr:has-text('{name}')").first.wait_for(
                state="visible", timeout=timeout
            )
            return True
        except Exception:  # noqa: BLE001 - 断言语义由调用方处理（超时=不可见）
            return False

    def inject_renamed_id(self, old_id: str = "name", new_id: str = "name-v2") -> None:
        """T23 改版注入：把弹窗内输入框的 id 改名（模拟前端升级后 id 变更）。

        旧定位器 #<old_id> 随即失效，而元素仍保留 id/placeholder 稳定属性——
        与真实"前端升级改 id"场景同构，自愈应凭 description 语义重定位到新 id。
        """
        self.page.evaluate(
            "([oldId, newId]) => { const el = document.querySelector(`.ant-modal #${oldId}`);"
            " if (el) { el.id = newId; } }",
            [old_id, new_id],
        )
