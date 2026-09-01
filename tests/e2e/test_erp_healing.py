"""端到端测试：管伊佳 ERP 自愈场景（T23 P2，marker erp；需本地 ERP 环境）。

核心演示：**真实被测系统上的自愈闭环**。场景设计（单弹窗内完成，规避 antd 弹窗
不销毁导致的 DOM 残留污染）：

1. 商品新增弹窗：改版**前**用旧定位器 #name 正常填入（表单可用基线）；
2. **前端改版注入**：名称输入框 id 由 `name` 变更为 `name-v2`（与真实"前端升级改 id"
   同构，元素保留 placeholder 稳定属性）；
3. 改版**后**继续用旧定位器 #name 操作 → 失效 → 自愈凭 description="商品名称" 语义
   匹配 placeholder="请输入商品名称"（heuristic token 重叠；命中率不足时 VLM 兜底）
   重定位到 #name-v2，完成填名并保存；
4. API 断言商品真实落库 + 自愈记录产生 + 用例后清理（数据隔离约定）。
"""

from uuid import uuid4

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.erp]


def test_erp_material_add_with_healing(erp_page, erp_admin, settings):
    """商品新增弹窗内经历"前端改版"→ 自愈重定位完成填名 → 落库断言 → 清理。"""
    page = erp_page
    from tests.e2e.pages.erp.material_page import MaterialPage

    mp = MaterialPage(page, settings.sut.base_url)
    name = f"自愈演示商品_{uuid4().hex[:6]}"

    try:
        mp.open_list()
        mp.open_add_modal()

        # 改版前：旧定位器正常工作（基线）
        mp.fill_name("占位名称_改版前")

        # —— 前端改版注入：名称输入框 id 由 name 变更为 name-v2 ——
        mp.inject_renamed_id("name", "name-v2")

        # 改版后：旧定位器 #name 失效 → 自愈重定位 → 换名保存（演示核心）
        mp.fill_name(name)
        mp.fill_others()
        mp.save_modal()

        # 断言：列表可见 + API 确认真实落库 + 自愈记录产生
        assert mp.row_visible(name), f"商品未出现在列表：{name}"
        assert erp_admin.find_material_id(name), f"商品未落库（自愈保存失败）：{name}"
        assert any(r.success for r in page.reporter.records), "未产生自愈成功记录"
    finally:
        # 数据清理（数据隔离约定：演示环境不留测试垃圾）
        material_id = erp_admin.find_material_id(name)
        if material_id:
            erp_admin.delete_material(material_id)
