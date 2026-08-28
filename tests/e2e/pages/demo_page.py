"""演示页的页面对象（POM，继承 BasePage）。

关键设计（呼应你的第 1 点）：本 POM 只依赖 Page 接口。注入 HealingPage（开）或
强制关闭的 HealingPage（关）都能运行同一份代码——区别仅在于开时会自动自愈、
关时失效定位器直接抛超时。description / fallback 为自愈增强参数。
"""

from __future__ import annotations

from pathlib import Path

from selfheal.engine.base_page import BasePage

# 以 file:// 打开本页，零依赖、不起服务（决策 D3）
DEMO_PAGE_URL = (Path(__file__).resolve().parent / "demo_page.html").as_uri()


class DemoPage(BasePage):
    """登录演示页对象。"""

    url = DEMO_PAGE_URL

    def login(self, username: str = "tester", password: str = "secret") -> None:
        self.locator('[data-testid="username"]', description="用户名输入框").fill(username)
        self.locator('[data-testid="password"]', description="密码输入框").fill(password)
        # 模拟 UI 改版导致的失效定位器：旧 id 已不存在。
        # 自愈应凭 description 重新识别"登录按钮"；同时提供人工备用定位器兜底。
        self.locator("#submit-btn-old", description="登录按钮", fallback="#submit-btn-v2").click()

    def click_secondary_via_fallback(self) -> None:
        """无法自愈的场景：description 故意不匹配，只能靠人工备用定位器。"""
        self.locator(
            "#ghost-btn-old", description="zzz_不存在的描述", fallback="#real-secondary"
        ).click()

    def result(self) -> str | None:
        return self.locator("#result").text_content()
