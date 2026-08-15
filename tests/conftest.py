"""pytest 公共 fixture（见 RULE.md R2：测试分层）。

- `-m unit`：单元测试，不依赖浏览器 / 网络（不请求下方浏览器 fixture）。
- `-m e2e`：集成 / 端到端测试，使用 healing_page / disabled_page（需要浏览器内核）。
- 自愈开关：CLI `--selfheal` / `--no-selfheal` 优先于 `settings.healing.enabled`。

注意：engine（含 playwright）在 fixture 内**惰性导入**，使 `-m unit` 无需安装 playwright 即可收集运行。
"""

from __future__ import annotations

import contextlib

import pytest

from selfheal.config import Settings, load_settings
from selfheal.knowledge.base import KnowledgeBackend

# B3：会话级自愈记录聚合（写 HTML 看板的运行时生成路径）
_session_reporters: list = []


def pytest_addoption(parser):
    group = parser.getgroup("selfheal", "AI 自愈开关")
    group.addoption(
        "--selfheal",
        dest="selfheal",
        action="store_true",
        default=None,
        help="强制开启自愈（优先于配置）",
    )
    group.addoption(
        "--no-selfheal",
        dest="selfheal",
        action="store_false",
        help="强制关闭自愈，回退原生 Playwright 行为",
    )
    # 注意：pytest 自带 --trace（进入 pdb），故本框架用 --trace-healing 避免冲突
    group.addoption(
        "--trace-healing",
        dest="trace_healing",
        action="store_true",
        default=None,
        help="录制 Playwright trace（优先于配置，用于视频回放）",
    )
    group.addoption(
        "--no-trace-healing",
        dest="trace_healing",
        action="store_false",
        help="不录制 trace",
    )


@pytest.fixture(scope="session")
def settings() -> Settings:
    return load_settings()


@pytest.fixture(scope="session")
def knowledge(tmp_path_factory) -> KnowledgeBackend:
    """会话级共享知识库（临时 SQLite：验证持久化，且不污染仓库）。"""
    from selfheal.knowledge.sqlite_store import SqliteKnowledgeStore  # 惰性导入

    db_path = tmp_path_factory.mktemp("knowledge") / "knowledge.db"
    store = SqliteKnowledgeStore(str(db_path))
    yield store
    store.close()  # M3：会话结束关闭连接（临时库，Windows 下不关会锁文件）


@pytest.fixture(scope="session")
def browser_manager(settings):
    from selfheal.engine.browser import BrowserManager  # 惰性导入，避免单测强依赖 playwright

    with BrowserManager(settings) as manager:
        yield manager


@pytest.fixture
def context(browser_manager, settings, request):
    """函数级浏览器上下文（基座标准，见 docs/plans/2026-08-05-base-framework.md B3）。

    开启 trace 时录制；teardown 保存 trace 并关闭上下文（修复 context 泄漏，评审 #25）。
    trace 开关：CLI --trace-healing/--no-trace-healing > settings.browser.trace。
    """
    from pathlib import Path

    cli = request.config.getoption("trace_healing")
    trace_enabled = settings.browser.trace if cli is None else cli
    ctx = browser_manager.new_context()
    if trace_enabled:
        ctx.tracing.start(screenshots=True, snapshots=True)
    yield ctx
    if trace_enabled:
        trace_dir = Path(settings.browser.trace_dir)
        trace_dir.mkdir(parents=True, exist_ok=True)
        safe = request.node.name.replace("/", "_").replace("\\", "_")
        trace_path = trace_dir / f"trace-{safe}.zip"
        # trace 保存失败不影响用例结果
        with contextlib.suppress(Exception):
            ctx.tracing.stop(path=str(trace_path))
            _attach_trace_to_allure(trace_path)  # C5：trace 作为证据附件进报告
    ctx.close()


def _attach_trace_to_allure(trace_path) -> None:
    """把已落盘的 trace 附到 Allure 报告（best-effort；未装 allure 或文件缺失则跳过）。"""
    import os

    if not os.path.exists(trace_path):
        return
    try:
        import allure

        allure.attach.file(str(trace_path), name="Playwright Trace（回放）", attachment_type=allure.attachment_type.ZIP)
    except ImportError:  # pragma: no cover - allure 可选
        pass


@pytest.fixture
def page(context):
    """函数级原生页面（基座标准，见 docs/plans/2026-08-05-base-framework.md B3）。

    有意覆盖 pytest-playwright 自带的 page fixture——本框架用自研 BrowserManager 控制生命周期。
    """
    return context.new_page()


@pytest.fixture
def pom(page):
    """POM 工厂：`demo = pom(DemoPage)`。

    POM 面向 Page 接口编写，自愈开/关由注入的 page 决定（注入原生 page 则原生、
    注入 healing_page 则带自愈）。配合 BasePage 使用（见 B1）。
    """

    def _factory(pom_cls):
        return pom_cls(page)

    return _factory


@pytest.fixture
def healing_page(settings, knowledge, context, request):
    """自愈页面插件。开关：CLI > settings.healing.enabled。"""
    from selfheal.engine.healing_locator import HealingPage  # 惰性导入

    cli = request.config.getoption("selfheal")
    page = HealingPage(context.new_page(), settings, knowledge=knowledge, enabled_override=cli)
    _session_reporters.append(page.reporter)  # B3：登记 reporter 供会话结束聚合写看板
    yield page
    page.close()  # M3：释放自愈资源（orchestrator 自建资源；注入的会话级 knowledge 由 fixture 关闭）


@pytest.fixture
def disabled_page(settings, context):
    """强制关闭自愈的页面，用于验证"关闭=原生行为"。"""
    from selfheal.engine.healing_locator import HealingPage  # 惰性导入

    yield HealingPage(context.new_page(), settings, enabled_override=False)


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 - pytest 钩子签名固定
    """会话结束（B3）：把全部自愈记录聚合写入 HTML 看板（运行时生成路径）。

    此前看板只能手工导出；本钩子让 CI / 本地跑完测试即产出 reports/dashboard.html。
    """
    records = []
    stats: dict[str, int] = {}
    for reporter in _session_reporters:
        records.extend(reporter.records)
        for key, value in reporter.stats.items():
            stats[key] = stats.get(key, 0) + value
    if not records:
        return
    try:
        from selfheal.reporting.dashboard import write_dashboard
        from selfheal.reporting.fix_proposals import estimate_cost

        cost = estimate_cost(stats.get("llm_calls", 0), stats.get("vlm_calls", 0))
        write_dashboard(records, "reports/dashboard.html", cost=cost)
    except Exception:  # noqa: BLE001 - 看板生成失败不影响测试结果
        pass
