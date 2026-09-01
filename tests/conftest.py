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
from selfheal.reporting.allure_bridge import (
    _HAS_ALLURE,
    apply_dynamic_labels,
    attach_file,
    write_environment,
)

# B3：会话级自愈记录聚合（写 HTML 看板的运行时生成路径）
_session_reporters: list = []


@pytest.fixture(autouse=True)
def _allure_auto_labels(request):
    """T18：按 marker 优先级（healing > e2e > unit）自动打 Allure epic/feature 标签。

    无 allure-pytest（_HAS_ALLURE=False）时零开销直通；dynamic 标签须在测试
    上下文（fixture/setup 期）调用，故放 autouse fixture 而非收集钩子。
    """
    if _HAS_ALLURE:
        apply_dynamic_labels(request.node)
    yield


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
    """把已落盘的 trace 附到 Allure 报告（T18 统一经 allure_bridge：best-effort）。"""
    attach_file(trace_path, name="Playwright Trace（回放）")


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


# --- T23：管伊佳 ERP 被测系统（marker erp；需本地 ERP 环境，CI 不跑） ---


@pytest.fixture(scope="session")
def erp_admin(settings):
    """ERP 管理员 API 客户端（测试数据造数/清理；凭证缺失时 skip 整组用例）。"""
    import pytest as _pytest

    from tests.e2e.api.erp_client import ErpApiError, ErpClient, ErpCredentials

    sut = settings.sut
    try:
        credentials = ErpCredentials.from_env(sut.api_username_env, sut.api_password_env)
    except ErpApiError as exc:
        _pytest.skip(f"ERP 造数凭证未配置：{exc}")
    client = ErpClient(sut.api_base_url, credentials)
    client.login()
    yield client


@pytest.fixture
def erp_page(settings, knowledge, context):
    """ERP 被测页面：HealingPage（自愈开）+ UI 登录态（测试账号，.env 凭证）。"""
    import os

    from selfheal.engine.healing_locator import HealingPage  # 惰性导入
    from tests.e2e.pages.erp.home_page import ErpHomePage
    from tests.e2e.pages.erp.login_page import ErpLoginPage

    sut = settings.sut
    username = os.getenv(sut.ui_username_env, "")
    password = os.getenv(sut.ui_password_env, "")
    if not username or not password:
        import pytest as _pytest

        _pytest.skip(f"ERP UI 凭证未配置（{sut.ui_username_env}/{sut.ui_password_env}）")

    page = HealingPage(context.new_page(), settings, knowledge=knowledge)
    login = ErpLoginPage(page, sut.base_url).open_login()
    login.login(username, password)
    home = ErpHomePage(page, sut.base_url)
    assert home.is_logged_in(), f"ERP UI 登录未成功（url={page.url}）"
    yield page


def is_xdist_worker(config) -> bool:
    """T21：xdist worker 进程判定（仅 worker 有 workerinput；controller / 单进程均无）。"""
    return getattr(config, "workerinput", None) is not None


def _write_shard(directory, worker_id: str, records: list, stats: dict) -> None:
    """T21：xdist worker 把自愈记录写为独立分片（controller 统一聚合，避免互相覆盖丢数据）。"""
    import json
    from dataclasses import asdict

    directory.mkdir(parents=True, exist_ok=True)
    payload = {"records": [asdict(r) for r in records], "stats": stats}
    (directory / f".healing-shard-{worker_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _read_shards(directory):
    """T21：合并全部 worker 分片 → (records, stats)；无分片返回空（单进程行为不变）。"""
    import json
    from pathlib import Path as _Path

    from selfheal.reporting.hooks import HealingRecord

    records: list = []
    stats: dict[str, int] = {}
    if not _Path(directory).exists():
        return records, stats
    for shard in sorted(_Path(directory).glob(".healing-shard-*.json")):
        try:
            data = json.loads(shard.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 单个分片损坏不阻塞聚合
            continue
        records.extend(HealingRecord(**d) for d in data.get("records", []))
        for key, value in data.get("stats", {}).items():
            stats[key] = stats.get(key, 0) + value
    return records, stats


def _cleanup_shards(directory) -> None:
    """T21：聚合完成后清理分片临时文件（best-effort）。"""
    from pathlib import Path as _Path

    for shard in _Path(directory).glob(".healing-shard-*.json"):
        with contextlib.suppress(Exception):
            shard.unlink()


def _finalize_healing_reports(config, records: list, stats: dict) -> None:
    """T21：看板 / 通知摘要的统一出口（xdist 感知）。

    - worker 进程：仅写分片（记录不丢，dashboard 由 controller 统一生成）；
    - controller / 单进程：合并 worker 分片 + 本进程记录 → 写 dashboard + healing-records.json
      → 清理分片。无 xdist 时无分片，行为与既有单进程路径完全一致（零回归）。
    """
    from pathlib import Path

    reports_dir = Path("reports")
    if is_xdist_worker(config):
        if records:
            _write_shard(reports_dir, config.workerinput["workerid"], records, stats)
        return
    shard_records, shard_stats = _read_shards(reports_dir)
    all_records = list(records) + shard_records
    merged_stats = dict(stats)
    for key, value in shard_stats.items():
        merged_stats[key] = merged_stats.get(key, 0) + value
    if not all_records:
        return
    try:
        from selfheal.reporting.dashboard import write_dashboard
        from selfheal.reporting.fix_proposals import estimate_cost

        cost = estimate_cost(merged_stats.get("llm_calls", 0), merged_stats.get("vlm_calls", 0))
        write_dashboard(all_records, "reports/dashboard.html", cost=cost)
        _write_healing_records_json(all_records, cost)  # T19：通知摘要数据源（CI artifact）
    except Exception:  # noqa: BLE001 - 看板生成失败不影响测试结果
        pass
    finally:
        _cleanup_shards(reports_dir)


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001 - pytest 钩子签名固定
    """会话结束（B3）：聚合全部自愈记录写入 HTML 看板（T21：xdist 分片聚合感知）。

    此前看板只能手工导出；本钩子让 CI / 本地跑完测试即产出 reports/dashboard.html。
    """
    # T18：Allure 环境页（--alluredir 启用时写入；会话结束后写，不被 allure-pytest 清理）
    try:
        results_dir = session.config.getoption("allure_report_dir", None)
        if results_dir:
            write_environment(results_dir, load_settings())
    except Exception:  # noqa: BLE001 - 环境页失败不影响测试结果
        pass
    records = []
    stats: dict[str, int] = {}
    for reporter in _session_reporters:
        records.extend(reporter.records)
        for key, value in reporter.stats.items():
            stats[key] = stats.get(key, 0) + value
    _finalize_healing_reports(session.config, records, stats)


def _write_healing_records_json(records: list, cost: dict) -> None:
    """T19：把会话自愈记录 + 成本摘要落盘 JSON，供 scripts/notify.py 组装通知。

    best-effort：写出失败不影响测试结果；文件随 CI artifact 上传（healing-records）。
    """
    import json
    from dataclasses import asdict
    from pathlib import Path

    try:
        reports = Path("reports")
        reports.mkdir(parents=True, exist_ok=True)
        payload = {
            "records": [asdict(r) for r in records],
            "cost": cost,
        }
        (reports / "healing-records.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001 - 摘要落盘失败不影响测试结果
        pass
