"""pytest 公共 fixture（见 RULE.md R2：测试分层）。

- `-m unit`：单元测试，不依赖浏览器 / 网络（不请求下方浏览器 fixture）。
- `-m e2e`：集成 / 端到端测试，使用 healing_page / disabled_page（需要浏览器内核）。
- 自愈开关：CLI `--selfheal` / `--no-selfheal` 优先于 `settings.healing.enabled`。

注意：engine（含 playwright）在 fixture 内**惰性导入**，使 `-m unit` 无需安装 playwright 即可收集运行。
"""

from __future__ import annotations

import pytest

from selfheal.config import Settings, load_settings
from selfheal.knowledge.store import KnowledgeStore


def pytest_addoption(parser):
    group = parser.getgroup("selfheal", "AI 自愈开关")
    group.addoption(
        "--selfheal", dest="selfheal", action="store_true", default=None,
        help="强制开启自愈（优先于配置）",
    )
    group.addoption(
        "--no-selfheal", dest="selfheal", action="store_false",
        help="强制关闭自愈，回退原生 Playwright 行为",
    )


@pytest.fixture(scope="session")
def settings() -> Settings:
    return load_settings()


@pytest.fixture(scope="session")
def knowledge() -> KnowledgeStore:
    """会话级共享知识库，便于演示"修复后命中复用"。"""
    return KnowledgeStore()


@pytest.fixture(scope="session")
def browser_manager(settings):
    from selfheal.engine.browser import BrowserManager  # 惰性导入，避免单测强依赖 playwright

    with BrowserManager(settings) as manager:
        yield manager


@pytest.fixture
def healing_page(settings, knowledge, browser_manager, request):
    """自愈页面插件。开关：CLI > settings.healing.enabled。"""
    from selfheal.engine.healing_locator import HealingPage  # 惰性导入

    cli = request.config.getoption("selfheal")
    page = browser_manager.new_page()
    yield HealingPage(page, settings, knowledge=knowledge, enabled_override=cli)


@pytest.fixture
def disabled_page(settings, browser_manager):
    """强制关闭自愈的页面，用于验证"关闭=原生行为"。"""
    from selfheal.engine.healing_locator import HealingPage  # 惰性导入

    page = browser_manager.new_page()
    yield HealingPage(page, settings, enabled_override=False)
