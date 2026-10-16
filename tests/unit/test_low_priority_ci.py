"""单元测试：低优先级工程一致性项（L16 / L17 / L18 / L19）。

这些项的失效方式都是"静默"的（marker 注册了没人用、CI 缺门禁、本机配置污染结果），
单靠读代码看不出来，所以断言都落在**真实产物**上：

- L16：5 个自愈闭环 e2e 真的带 `healing` marker（否则 Allure「AI 自愈」分组永不出现）；
- L17：CI workflow 有 timeout-minutes / concurrency，且第三方 action 不跟随浮动分支；
- L18：e2e 基线守卫拦得住被本机 gitignored 配置污染的 e2e（dry_run / 关自愈）；
- L19：真实模型冒烟被 `live` marker 隔离（默认 e2e 过滤不跑付费调用）。

收集类断言用**子进程**跑 pytest（真实收集结果，不受本进程 args / marker 表达式影响），
比 `pytester` 更贴近 CI 实际命令且无插件依赖。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

pytestmark = pytest.mark.unit

# L16：5 个自愈闭环 e2e（须带 healing marker）
HEALING_E2E_FILES = (
    "tests/e2e/test_healing_flow.py",
    "tests/e2e/test_knowledge_reuse.py",
    "tests/e2e/test_popup_flow.py",
    "tests/e2e/test_strategy_threshold_e2e.py",
    "tests/e2e/test_ab_scenarios.py",
)

# L19：真实模型调用（外呼付费 API）的两个冒烟模块（须带 live marker）
LIVE_SMOKE_FILES = (
    "tests/e2e/test_llm_smoke.py",
    "tests/e2e/test_visual_smoke.py",
)


def run_pytest(*args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """在仓库根以子进程跑 pytest（返回 CompletedProcess；stdout 统一按 UTF-8 解码）。"""
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
    )


def collected_nodeids(output: str) -> set[str]:
    """从 `--collect-only -q` 输出提取 nodeid 集合（只认 tests/ 开头的行）。"""
    return {line.strip() for line in output.splitlines() if line.strip().startswith("tests/")}


# --- L16：healing marker 真被自愈闭环 e2e 使用 ---


def test_all_healing_e2e_cases_carry_healing_marker():
    """L16：5 个自愈 e2e 的每个用例都能被 `-m healing` 收到（与 `-m e2e` 集合一致）。"""
    all_e2e = run_pytest("--collect-only", "-q", "-m", "e2e", *HEALING_E2E_FILES)
    assert all_e2e.returncode == 0, all_e2e.stdout + all_e2e.stderr
    every = collected_nodeids(all_e2e.stdout)
    assert every, "前置条件失败：这些文件没收集到 e2e 用例"

    healing = run_pytest("--collect-only", "-q", "-m", "healing", *HEALING_E2E_FILES)
    assert healing.returncode == 0, healing.stdout + healing.stderr
    tagged = collected_nodeids(healing.stdout)
    assert tagged == every, f"缺 healing marker 的用例：{sorted(every - tagged)}"


def test_healing_marker_hits_every_file():
    """L16：5 个文件各自至少贡献一条 healing 用例（防"只标了其中一个文件"）。"""
    result = run_pytest("--collect-only", "-q", "-m", "healing", *HEALING_E2E_FILES)
    assert result.returncode == 0, result.stdout + result.stderr
    tagged = collected_nodeids(result.stdout)
    for path in HEALING_E2E_FILES:
        assert any(nodeid.startswith(f"{path}::") for nodeid in tagged), f"{path} 无用例带 healing"


# --- L17：CI 超时 / 并发组 / action 版本固定 ---


def _workflow() -> dict:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def test_jobs_have_timeout_minutes():
    """L17：unit 15 / e2e 30——卡死不再拖到 GitHub 默认 6 小时。"""
    jobs = _workflow()["jobs"]
    assert jobs["unit"]["timeout-minutes"] == 15
    assert jobs["e2e"]["timeout-minutes"] == 30


def test_workflow_has_concurrency_group():
    """L17：同一 ref 的并发运行只保留最新（并发 push 不再同时写 gh-pages）。"""
    concurrency = _workflow()["concurrency"]
    assert concurrency["group"] == "ci-${{ github.ref }}"
    assert concurrency["cancel-in-progress"] is True


def test_actions_are_pinned_to_release_tags():
    """L17：不跟随浮动分支（master/main/HEAD）——上游一改行为会直接打到发布链路。"""
    floating = {"master", "main", "HEAD"}
    refs: list[str] = []
    for job in _workflow()["jobs"].values():
        for step in job.get("steps", []):
            if "uses" in step:
                refs.append(step["uses"])
    assert refs, "前置条件失败：workflow 里没有 uses 步骤？"

    for ref in refs:
        action, _, version = ref.partition("@")
        assert version, f"{ref} 未固定版本"
        assert version not in floating, f"{action} 跟随浮动分支 @{version}"
        # 允许：语义化 tag（v1 / v1.15）或 40 位 commit SHA
        is_tag = re.fullmatch(r"v?\d+(\.\d+)*", version)
        assert is_tag or re.fullmatch(r"[0-9a-f]{40}", version), (
            f"{action}@{version} 既不是 release tag 也不是 commit SHA"
        )


def test_allure_report_action_pinned_to_verified_tag():
    """L17：allure-report-action 从 @master 改为核实存在的 v1.15（非浮动）。"""
    refs = [
        step["uses"]
        for job in _workflow()["jobs"].values()
        for step in job.get("steps", [])
        if "uses" in step
    ]
    allure_refs = [ref for ref in refs if ref.startswith("simple-elf/allure-report-action@")]
    assert allure_refs == ["simple-elf/allure-report-action@v1.15"]


# --- L18：e2e 基线守卫（本机 gitignored 配置不得污染 e2e） ---


def _fake_settings(*, dry_run: bool = False, enabled: bool = True):
    from selfheal.config import Settings

    settings = Settings()
    settings.healing.dry_run = dry_run
    settings.healing.enabled = enabled
    return settings


def _fake_request(node_markers: tuple[str, ...], *, settings=None, cli_selfheal=None):
    """最小 fake request：guard 只用到 node.iter_markers / config.getoption / getfixturevalue。"""
    markers = [SimpleNamespace(name=name) for name in node_markers]
    return SimpleNamespace(
        node=SimpleNamespace(iter_markers=lambda: iter(markers)),
        config=SimpleNamespace(getoption=lambda name: cli_selfheal),
        getfixturevalue=lambda name: settings,
    )


def test_e2e_baseline_violation_flags_dry_run():
    """L18：dry_run 打开 = 自愈不真正重试 → e2e 会集体失败，必须显式判为污染。"""
    from tests.conftest import e2e_baseline_violation

    violation = e2e_baseline_violation(_fake_settings(dry_run=True))
    assert violation is not None
    assert "本机配置会污染 e2e" in violation and "dry_run" in violation
    assert "--no-selfheal" in violation  # 给出显式 CLI 覆盖的出路


def test_e2e_baseline_violation_flags_disabled_healing():
    """L18：本机配置关掉自愈（enabled=false）同样是"跑成了另一种模式"。"""
    from tests.conftest import e2e_baseline_violation

    violation = e2e_baseline_violation(_fake_settings(enabled=False))
    assert violation is not None and "enabled=false" in violation


def test_e2e_baseline_violation_clean_by_default():
    """L18：默认配置（dry_run=false + enabled=true）→ 不误报。"""
    from tests.conftest import e2e_baseline_violation

    assert e2e_baseline_violation(_fake_settings()) is None


def test_e2e_baseline_violation_ignores_cli_override():
    """L18：显式 CLI 覆盖（--selfheal / --no-selfheal）不算污染。"""
    from tests.conftest import e2e_baseline_violation

    polluted = _fake_settings(dry_run=True, enabled=False)
    assert e2e_baseline_violation(polluted, cli_selfheal=True) is None
    assert e2e_baseline_violation(polluted, cli_selfheal=False) is None


def test_e2e_baseline_guard_fixture_fails_on_dry_run():
    """L18：autouse 守卫本体在 e2e 用例 + dry_run 时真的 fail（不是只写了个纯函数）。"""
    import inspect

    from tests import conftest

    guard = inspect.unwrap(conftest._e2e_baseline_guard)
    request = _fake_request(("e2e",), settings=_fake_settings(dry_run=True))
    with pytest.raises(pytest.fail.Exception, match="本机配置会污染 e2e"):
        guard(request)


def test_e2e_baseline_guard_passes_with_cli_override():
    """L18：CLI 覆盖时守卫放行（显式开关是有意为之，不是污染）。"""
    import inspect

    from tests import conftest

    guard = inspect.unwrap(conftest._e2e_baseline_guard)
    for cli in (True, False):
        request = _fake_request(("e2e",), settings=_fake_settings(dry_run=True), cli_selfheal=cli)
        assert guard(request) is None


def test_e2e_baseline_guard_covers_erp_items():
    """L18：erp 用例（e2e + erp）同样受守卫保护。"""
    from tests import conftest

    assert conftest.is_guarded_e2e_item(_fake_request(("e2e", "erp")).node) is True
    assert conftest.is_guarded_e2e_item(_fake_request(("unit",)).node) is False


def test_e2e_baseline_guard_ignores_unit_items():
    """L18：unit 用例不读本机配置（零开销、零误伤）——settings fixture 根本不被请求。"""
    import inspect

    from tests import conftest

    called: list[str] = []
    guard = inspect.unwrap(conftest._e2e_baseline_guard)
    request = SimpleNamespace(
        node=SimpleNamespace(iter_markers=lambda: iter([SimpleNamespace(name="unit")])),
        config=SimpleNamespace(getoption=lambda name: None),
        getfixturevalue=lambda name: called.append(name) or _fake_settings(dry_run=True),
    )
    assert guard(request) is None
    assert called == []


# --- L19：live marker 把真实模型调用隔离出常规 e2e ---


def module_level_markers(path: str) -> set[str]:
    """静态解析模块级 `pytestmark = ...` 的 marker 名集合（不 import，环境无关）。"""
    import ast

    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
    markers: set[str] = set()
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if not any(getattr(t, "id", None) == "pytestmark" for t in targets):
            continue
        # 兼容 `pytestmark = pytest.mark.e2e` 与 `[pytest.mark.e2e, pytest.mark.live]`
        values = node.value.elts if isinstance(node.value, ast.List) else [node.value]
        for value in values:
            if (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Attribute)
                and value.value.attr == "mark"
            ):
                markers.add(value.attr)
    return markers


def test_live_marker_registered_in_pyproject():
    """L19：`live` 必须注册（--strict-markers 下未注册会让全仓收集直接报错）。"""
    import tomllib

    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    markers = config["tool"]["pytest"]["ini_options"]["markers"]
    assert any(str(m).startswith("live:") for m in markers)


def test_live_smoke_modules_declare_live_marker():
    """L19：两个真实模型冒烟模块的模块级 pytestmark 同时含 e2e 与 live。"""
    for path in LIVE_SMOKE_FILES:
        markers = module_level_markers(path)
        assert {"e2e", "live"} <= markers, f"{path} 模块级 marker 缺 live/e2e：{sorted(markers)}"


def test_live_marker_covers_smoke_cases_and_is_excluded_by_default():
    """L19：`-m live` 收得到冒烟用例，默认过滤 `-m "e2e and not live"` 一条都不收。"""
    plain = run_pytest("--collect-only", "-q", *LIVE_SMOKE_FILES)
    assert plain.returncode == 0, plain.stdout + plain.stderr
    every = collected_nodeids(plain.stdout)
    assert every, "前置条件失败：冒烟模块没收集到用例（缺 key 不应阻断收集）"

    live = run_pytest("--collect-only", "-q", "-m", "live", *LIVE_SMOKE_FILES)
    assert live.returncode == 0, live.stdout + live.stderr
    assert collected_nodeids(live.stdout) == every

    default = run_pytest(
        "--collect-only", "-q", "-m", "e2e and not erp and not live", *LIVE_SMOKE_FILES
    )
    assert default.returncode == 5, default.stdout + default.stderr  # 5 = 无测试被收集
    assert collected_nodeids(default.stdout) == set()


def test_ci_e2e_step_excludes_live():
    """L19：CI 的 e2e 步骤真的带 `not live` 过滤（防"marker 打了但 CI 仍外呼付费模型"）。"""
    steps = _workflow()["jobs"]["e2e"]["steps"]
    e2e_runs = [str(s["run"]) for s in steps if "run" in s and "pytest -m" in str(s["run"])]
    assert e2e_runs, "前置条件失败：e2e job 里没找到 pytest -m 步骤"
    assert any("not live" in run for run in e2e_runs), e2e_runs
