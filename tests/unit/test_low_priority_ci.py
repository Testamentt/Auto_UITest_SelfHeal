"""单元测试：低优先级工程一致性项（L16 / L17 起，后续项在本文件续写）。

这些项的失效方式都是"静默"的（marker 注册了没人用、CI 缺门禁、本机配置污染结果），
单靠读代码看不出来，所以断言都落在**真实产物**上：

- L16：5 个自愈闭环 e2e 真的带 `healing` marker（否则 Allure「AI 自愈」分组永不出现）；
- L17：CI workflow 有 timeout-minutes / concurrency，且第三方 action 不跟随浮动分支。

收集类断言用**子进程**跑 pytest（真实收集结果，不受本进程 args / marker 表达式影响），
比 `pytester` 更贴近 CI 实际命令且无插件依赖。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

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
