"""端到端测试：自愈价值 A/B 对比场景（T20，需浏览器内核，-m e2e）。

同一组「UI 改版定位失效」场景，parametrize 双变体同源运行：
- `disabled` 变体：强制关闭自愈——失效定位器直接超时失败（xfail(strict) 保证 CI 绿，
  语义 = 「无自愈时需人工修复」）；
- `healing` 变体：开启自愈——经 heuristic 策略按 aria-label 重新识别并完成操作。

`scripts/ab_compare.py` 以 -k 过滤分别收集两变体的 junitxml，产出对比报告。
三个场景全部使用**确定性 heuristic 可命中**的稳定属性（aria-label 与 description
完全一致），不依赖真实模型——对比结果可复现、耗时稳定。
"""

import pytest

from tests.e2e.pages.demo_page import DemoPage

pytestmark = pytest.mark.e2e

# A/B 双变体注入（indirect）：disabled 关自愈 + 短超时快速失败；healing 正常自愈
ab_variants = pytest.mark.parametrize(
    "ab_page",
    [
        pytest.param(
            "disabled",
            marks=pytest.mark.xfail(
                strict=True, reason="无自愈：演示改版定位失效，需人工修复（T20 A 组）"
            ),
        ),
        "healing",
    ],
    indirect=True,
)


@pytest.fixture
def ab_page(request, settings, knowledge, context):
    """A/B 双变体页面（复用 healing_page/disabled_page 语义，支持参数化选择）。"""
    from selfheal.engine.healing_locator import HealingPage  # 惰性导入

    if request.param == "disabled":
        page = HealingPage(
            context.new_page(), settings, knowledge=knowledge, enabled_override=False
        )
        page.set_default_timeout(800)  # 失效定位器快速失败，缩短 A 组耗时
    else:
        page = HealingPage(context.new_page(), settings, knowledge=knowledge, enabled_override=True)
    yield page
    page.close()  # M3：释放自愈资源


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
