# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""端到端测试：自愈价值 A/B 对比场景（需浏览器内核，-m e2e）。

同一组「UI 改版定位失效」场景，parametrize 双变体同源运行：
- `disabled` 变体：强制关闭自愈——失效定位器直接超时失败（xfail(strict) 保证 CI 绿，
  语义 = 「无自愈时需人工修复」）；
- `healing` 变体：开启自愈——经 heuristic 策略按 aria-label 重新识别并完成操作。

`scripts/ab_compare.py` 以 -k 过滤分别收集两变体的 junitxml，产出对比报告。
**10 个场景覆盖 5 类失效形态**（对应第15节「自愈要解决什么」）：
- 属性重生成：s5 hash class、s10 placeholder；
- DOM 结构变化：s7 多包一层、s8 元素移动；
- 链式断裂：s4 容器稳定/叶子失效；
- 弹层内控件改版：s9 弹窗按钮 id 变更；
- 文案/属性微调：s6 按钮文案变更（控件未动）；
- id 改版（基线）：s1~s3 登录按钮 / 用户名 / 密码输入框。

全部场景用**确定性 heuristic 可命中**的稳定属性（aria-label 与 description 一致或互为子串），
不依赖真实模型——对比结果可复现、耗时稳定。多轮口径用
`python scripts/ab_compare.py --rounds N`（同一场景多轮跑，flaky 场景单列）。
"""

import pytest

from tests.e2e.pages.demo_page import DemoPage

# healing marker 让 Allure feature 落「AI 自愈」分组；disabled 变体的 xfail 语义不变
pytestmark = [pytest.mark.e2e, pytest.mark.healing]

# A/B 双变体注入（indirect）：disabled 关自愈 + 短超时快速失败；healing 正常自愈
ab_variants = pytest.mark.parametrize(
    "ab_page",
    [
        pytest.param(
            "disabled",
            marks=pytest.mark.xfail(
                strict=True, reason="无自愈：演示改版定位失效，需人工修复（disabled 变体）"
            ),
        ),
        "healing",
    ],
    indirect=True,
)


@pytest.fixture
def ab_page(request, settings, knowledge, context):
    """A/B 双变体页面（复用 healing_page/disabled_page 语义，支持参数化选择）。"""
    from selfheal.plugin.healing_page import HealingPage  # 惰性导入（装配层在 plugin/）

    if request.param == "disabled":
        page = HealingPage(
            context.new_page(), settings, knowledge=knowledge, enabled_override=False
        )
        page.set_default_timeout(800)  # 失效定位器快速失败，缩短 A 组耗时
    else:
        page = HealingPage(context.new_page(), settings, knowledge=knowledge, enabled_override=True)
    # 登记 reporter：让 A/B 场景的自愈记录进入会话聚合（dashboard / healing-records.json），
    # 否则场景跑出的自愈事件在看板与简报里不可见，证据链断在测试层。
    from tests.conftest import _session_reporters

    _session_reporters.append(page.reporter)
    yield page
    page.close()  # 释放自愈资源


@ab_variants
def test_ab_s1_login_button(ab_page):
    """场景 1：登录按钮 id 改版（#submit-btn-old 失效）→ 自愈后点击成功。"""
    demo = DemoPage(ab_page)
    demo.open()
    demo.login()  # 内部含失效定位器 #submit-btn-old（description=登录按钮）
    assert demo.result() == "logged-in"
    if ab_page.healing_enabled:  # B 组：确有自愈发生（价值证据）
        assert any(r.original_selector == "#submit-btn-old" for r in ab_page.reporter.records)


@ab_variants
def test_ab_s2_username_input(ab_page):
    """场景 2：用户名输入框 id 改版（#user-field-old 失效）→ 自愈后 fill 成功。"""
    demo = DemoPage(ab_page)
    demo.open()
    demo.locator("#user-field-old", description="用户名").fill("tester")
    assert ab_page.locator('[data-testid="username"]').input_value() == "tester"


@ab_variants
def test_ab_s3_password_input(ab_page):
    """场景 3：密码输入框 id 改版（#pass-field-old 失效）→ 自愈后 fill 成功。"""
    demo = DemoPage(ab_page)
    demo.open()
    demo.locator("#pass-field-old", description="密码").fill("secret")
    assert ab_page.locator('[data-testid="password"]').input_value() == "secret"


@ab_variants
def test_ab_s4_chain_leaf(ab_page):
    """场景 4：链式定位（稳定容器 #panel + 失效叶子 #inner-btn-old）→ 自愈修叶子后点击成功。

    与场景 1~3 的区别：断裂点在**链的叶子**，自愈要先重放链再修叶（engine 两档语义）。
    演示页里真实按钮为 #inner-btn-v2、文本「面板按钮」；根上挂描述保证启发式确定性收官。
    """
    demo = DemoPage(ab_page)
    demo.open()
    ab_page.locator("#panel", description="面板按钮").locator("#inner-btn-old").click()
    assert demo.result() == "panel-clicked"
    if ab_page.healing_enabled:
        assert any(r.original_selector == "#inner-btn-old" for r in ab_page.reporter.records)


@ab_variants
def test_ab_s5_hash_class_regen(ab_page):
    """场景 5：构建工具 hash class 重新生成（`.btn-a1b2c3` 部署后改名）→ 自愈重定位。

    对应第15节「属性重生成」类场景：控件没动、只有生成式属性变了。
    改版注入用运行时改 class 实现；自愈凭 aria-label/文本「哈希按钮」重识别。
    """
    demo = DemoPage(ab_page)
    demo.open()
    # 模拟部署：构建产物把 `.btn-a1b2c3` 换成 `.btn-d4e5f6`
    ab_page.evaluate("() => { document.getElementById('hash-btn').className = 'btn-d4e5f6'; }")
    ab_page.locator(".btn-a1b2c3", description="哈希按钮").click()
    assert demo.result() == "hash-clicked"


@ab_variants
def test_ab_s6_text_changed(ab_page):
    """场景 6：按钮文案微调（「登录」→「立即登录」，控件本身没动）→ 自愈重定位。

    对应第15节「文案微调」：旧定位是精确文案 `text="登录"`，文案变了即失效；
    稳定属性 aria-label「登录按钮」仍在，启发式凭描述收官。
    注意：**断言文案（expect_text）不参与本场景**——文案变了不是改预期的理由。
    """
    demo = DemoPage(ab_page)
    demo.open()
    ab_page.evaluate("() => { document.getElementById('submit-btn-v2').textContent = '立即登录'; }")
    ab_page.locator('text="登录"', description="登录按钮").click()
    assert demo.result() == "logged-in"


@ab_variants
def test_ab_s7_dom_wrapped(ab_page):
    """场景 7：DOM 多包一层（输入框被包进新 div）→ 直接子代选择器失效 → 自愈重定位。

    对应第15节「DOM 结构调整」：旧定位 `#login-form > [data-testid=username]` 依赖父子关系，
    多一层包装即断；元素自身 data-testid / aria-label 未变，启发式可重识别。
    """
    demo = DemoPage(ab_page)
    demo.open()
    ab_page.evaluate(
        "() => {"
        "  const el = document.querySelector('[data-testid=\"username\"]');"
        "  const wrap = document.createElement('div');"
        "  wrap.className = 'field-wrapper';"
        "  el.parentNode.insertBefore(wrap, el);"
        "  wrap.appendChild(el);"
        "}"
    )
    ab_page.locator('#login-form > [data-testid="username"]', description="用户名输入框").fill(
        "tester"
    )
    assert ab_page.locator('[data-testid="username"]').input_value() == "tester"


@ab_variants
def test_ab_s8_dom_moved(ab_page):
    """场景 8：元素被移动到另一容器（备用入口从表单移入面板）→ 容器后代选择器失效 → 自愈。

    与 s7 的区别：s7 是"层级变深"，s8 是"跨容器搬家"。旧定位依赖 `#login-form` 上下文，
    搬家后不再命中；点击监听随节点移动保留，故成功判据仍是 result 值。
    """
    demo = DemoPage(ab_page)
    demo.open()
    ab_page.evaluate(
        "() => { document.getElementById('panel').appendChild("
        "document.getElementById('real-secondary')); }"
    )
    ab_page.locator('#login-form [aria-label="备用入口"]', description="备用入口").click()
    assert demo.result() == "secondary"


@ab_variants
def test_ab_s9_dialog_button_renamed(ab_page):
    """场景 9：弹层内控件 id 改版（弹窗按钮 #popup-close-old 失效）→ 自愈在弹层内重定位。

    对应「弹层时机/结构变化」：控件在 dialog 容器内，旧 id 被重生成；
    aria-label「关闭弹窗」稳定，启发式在真实候选集内选中它。
    """
    demo = DemoPage(ab_page)
    demo.open()
    ab_page.evaluate("() => { document.getElementById('popup').hidden = false; }")
    ab_page.locator("#popup-close-old", description="关闭弹窗").click()
    assert ab_page.locator('[data-testid="popup-close"]').is_visible()


@ab_variants
def test_ab_s10_placeholder_changed(ab_page):
    """场景 10：placeholder 属性微调（「用户名」→「请输入用户名」）→ 属性选择器失效 → 自愈。

    对应「属性重生成」的轻量形态：只有提示语变了，控件与可访问名未变。
    """
    demo = DemoPage(ab_page)
    demo.open()
    ab_page.evaluate(
        "() => { document.querySelector('[data-testid=\"username\"]')"
        ".placeholder = '请输入用户名'; }"
    )
    ab_page.locator('[placeholder="用户名"]', description="用户名输入框").fill("tester")
    assert ab_page.locator('[data-testid="username"]').input_value() == "tester"
