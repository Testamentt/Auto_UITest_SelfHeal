# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""单元测试：提交信息门禁（`scripts/check_commit_messages.py`）。

**为什么这些用例重要**：该脚本是 CI 里提交信息门禁的唯一调用路径。它曾有一个
"区间模式基本失效"的缺陷——用 `split("\\x00\\x00")` 去切 `git log --format=...%x00`
的输出，而 git 的记录分隔实际是 `\\x00\\n`，于是：

- 空 body 的提交（最常见）全部被 `len(parts) < 3` 跳过；
- 非空 body 的多条提交粘成一个 chunk，只有第一条被检查。

实测：真实仓 `HEAD~30..HEAD` 有 16 条带内部编号的 subject，脚本只报 1 条。
因为单测从未覆盖 `check_commits`，"741 passed" 抓不到这个缺陷。
下面的合成仓用例把"必须逐条检查"钉死。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_commit_messages as ccm  # noqa: E402

# ---------------------------------------------------------------------------
# 合成仓：精确控制每条提交的 subject / body
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8"
    )
    assert result.returncode == 0, f"git {' '.join(args)} 失败：{result.stderr}"
    return result.stdout


def _commit(repo: Path, message: str) -> None:
    """写入一次改动并提交（message 原样作为提交信息）。

    `encoding="utf-8"` 必填：Windows 下 `text=True` 默认用 GBK 解码子进程输出，
    提交信息含中文时 `_readerthread` 会抛 UnicodeDecodeError。
    """
    (repo / "f.txt").write_text(message, encoding="utf-8")
    _git(repo, "add", "f.txt")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "PATH": __import__("os").environ.get("PATH", ""),
        "HOME": str(repo),
    }
    result = subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert result.returncode == 0, f"提交失败：{result.stderr}"


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")
    # 基座提交：给后续 `HEAD~N..HEAD` 留出可解析的父提交
    _commit(root, "chore: 初始化")
    return root


# ---------------------------------------------------------------------------
# 1. 区间模式必须逐条检查（核心回归）
# ---------------------------------------------------------------------------


def test_range_mode_checks_every_commit_with_empty_body(repo):
    """回归：空 body 的提交也必须被检查（此前 `len(parts)<3` 把它们全部跳过）。

    这是最常见的提交形态（`git commit -m "xxx"` 只有 subject、没有 body）。
    """
    _commit(repo, "feat: 编号 T19 的改动")
    _commit(repo, "feat: 编号 M17 的改动")
    _commit(repo, "feat: 编号 R12 的改动")

    problems = ccm.check_commits("HEAD~3..HEAD", cwd=str(repo))
    hits = [p for p in problems if "内部编号" in p]
    assert len(hits) == 3, f"应逐条查出 3 条违规，实际 {len(hits)} 条：{problems}"


def test_range_mode_checks_all_commits_with_multiline_body(repo):
    """回归：带 body 的多条提交不得粘成一个 chunk（此前只查第一条）。"""
    _commit(repo, "feat: 第一条 T19")
    _commit(repo, "feat: 第二条 M17\n\n正文说明，无编号。")
    _commit(repo, "feat: 第三条 R12\n\n正文说明，无编号。")

    problems = ccm.check_commits("HEAD~3..HEAD", cwd=str(repo))
    hits = [p for p in problems if "内部编号" in p]
    assert len(hits) == 3, f"应逐条查出 3 条违规，实际 {len(hits)} 条：{problems}"


def test_range_mode_reports_subject_and_body_separately(repo):
    """同日提交的 subject 与 body 都要查（body 里的编号同样违规）。"""
    _commit(repo, "feat: 干净的信息\n\n但正文写了 T99，这是违规。")
    problems = ccm.check_commits("HEAD~1..HEAD", cwd=str(repo))
    assert any("T99" in p for p in problems), problems


def test_last_mode_detects_violation_in_empty_body_commit(repo):
    """回归：`--last` 对空 body 提交同样要检出（此前漏检）。"""
    _commit(repo, "fix: 含 T19 的提交")
    problems = ccm.check_commits("-1", cwd=str(repo))
    assert any("T19" in p for p in problems), problems


def test_clean_history_passes(repo):
    """反向验证：全是合规信息时不得报任何问题（防"什么都报"式误拦）。"""
    _commit(repo, "feat: 初始化")
    _commit(repo, "feat: 增加快速探测（见 D22）")
    _commit(repo, "fix: 修正 L3 阈值倒挂校验\n\n按 D23 的层级约束校验。")
    assert ccm.check_commits("HEAD~2..HEAD", cwd=str(repo)) == []


# ---------------------------------------------------------------------------
# 2. 允许清单（ADR / 层级术语）不含死代码
# ---------------------------------------------------------------------------


def test_adr_and_tier_tokens_are_allowed_via_real_branch():
    """ADR 必须经 `_is_allowed` 真实放行（而非"正则没匹配上"）。

    此前 `TICKET` 里没有 `D[0-9]+`，`_is_allowed` 的 D 分支永远不可达——
    把整个允许清单删掉，相关测试照样通过（"测了但测不到"）。

    注：`L1`–`L4` 不被 `TICKET` 匹配（正则只收两位数的 `L` 编号），因此它们本就不会被
    判违规，无需经 allowlist——这里只锁 ADR 这条真实分支。
    """
    for token in ("D22", "D23", "D25"):
        match = ccm.TICKET.search(token)
        assert match is not None, f"TICKET 匹配不到 {token} → ADR 允许分支是死代码"
        assert ccm._is_allowed(match.group(1)) is True

    # 反向验证：`_is_allowed` 不是"一律放行"——工单类编号必须被判 False
    assert ccm._is_allowed("T19") is False
    assert ccm._is_allowed("M17") is False


def test_work_item_tokens_are_rejected():
    """真正的工单编号仍须被拒（允许清单不能放宽成"什么都放行"）。"""
    problems = ccm.check_text("feat: 完成 T19 与 M17 与 R12", "label")
    assert len(problems) >= 3, problems


def test_sha_like_and_version_like_tokens_not_flagged():
    """版本号 / 单词不能被误判成工单编号（`v4.1`、`log-2`、`fpA`）。"""
    assert ccm.check_text("feat: 升级 deepseek v4.1 与 log-2 输出", "label") == []


# ---------------------------------------------------------------------------
# 3. 协作署名与过程叙述
# ---------------------------------------------------------------------------


def test_attribution_is_rejected():
    problems = ccm.check_text("feat: 某功能\n\nCo-authored-by: Claude <noreply@x>", "label")
    assert any("署名" in p for p in problems), problems


def test_comment_lines_are_ignored():
    """git 模板注释行（`#` 开头）不算提交内容。"""
    assert ccm.check_text("# 这是模板提示，含 T99 也不算\nfeat: 正常信息", "label") == []


# ---------------------------------------------------------------------------
# 4. 命令行入口
# ---------------------------------------------------------------------------


def test_cli_file_mode_detects_violation(tmp_path, capsys):
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("feat: 含 T19 的信息\n", encoding="utf-8")
    assert ccm.main(["--file", str(msg)]) == 1
    assert "不符合规范" in capsys.readouterr().err


def test_cli_file_mode_passes_clean_message(tmp_path, capsys):
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("feat: 增加快速探测（见 D22）\n", encoding="utf-8")
    assert ccm.main(["--file", str(msg)]) == 0
    assert "通过" in capsys.readouterr().out
