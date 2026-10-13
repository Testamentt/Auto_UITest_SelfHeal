"""单元测试：低优先级工程一致性项（L16 起，后续项在本文件续写）。

这些项的失效方式都是"静默"的（marker 注册了没人用、CI 缺门禁、本机配置污染结果），
单靠读代码看不出来，所以断言都落在**真实产物**上：

- L16：5 个自愈闭环 e2e 真的带 `healing` marker（否则 Allure「AI 自愈」分组永不出现）。

收集类断言用**子进程**跑 pytest（真实收集结果，不受本进程 args / marker 表达式影响），
比 `pytester` 更贴近 CI 实际命令且无插件依赖。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

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
