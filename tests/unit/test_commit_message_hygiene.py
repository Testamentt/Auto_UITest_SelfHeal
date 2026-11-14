# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""单元测试：提交信息卫生检查（不触网、不依赖 git 仓库状态）。

被守护的规则：提交信息只写「改了什么 + 为什么」，不写内部工单编号（`T*` / `M*` / `R*` /
`H*` / `V*` / `B*` / `C*` / `L**` / `P*` / `m*`）与协作过程叙述 / AI 署名。
允许公开 ADR 编号（D 系列）与检索层级术语 `L1`–`L4`。

**为什么单测要覆盖脚本**：`commit-msg` hook 与 CI 都只是调用同一个脚本；脚本判错，
整条强制链就形同虚设。这里锁定"该拒的拒、该放的放"，并覆盖两个 Windows 真实坑：
BOM 前缀（`Out-File -Encoding utf8` 会写 BOM）与 GBK 控制台输出 emoji 崩掉。
"""

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_commit_messages as ccm  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "message",
    [
        "refactor: 清理注释（M17）",
        "fix(ci): 修好 publish（T19）",
        "docs: 登记修复进度（R12）",
        "test(e2e): 补链式场景（M15-3）",
        "refactor: 收口轮整理",
        "feat: 新增探测（L17）",
        "fix: 半开探针（V5）",
    ],
)
def test_rejects_internal_ids_and_narration(message):
    """内部编号与协作过程叙述必须被拒。"""
    problems = ccm.check_text(message, "test")
    assert problems, f"应被拒绝但通过了：{message}"


@pytest.mark.parametrize(
    "message",
    [
        "refactor: 测试注释改为机理描述",
        "feat(engine): 动作前快速探测（D22）",
        "fix(llm): 韧性层熔断（D25）",
        "docs: 分层约束说明（D19）",
        "fix(agent): L1 精确命中改硬短路",  # L1 是层级术语
        "refactor: 修复 v4.1 端点切换",  # 模型版本号不是工单编号
    ],
)
def test_allows_clean_messages(message):
    """干净提交信息与公开编号必须放行。"""
    problems = ccm.check_text(message, "test")
    assert problems == [], f"误杀：{message} -> {problems}"


def test_rejects_ai_attribution():
    """AI 协作署名必须被拒。"""
    problems = ccm.check_text(
        "feat: 新增能力\n\nCo-authored-by: Claude <noreply@example.com>", "test"
    )
    assert any("署名" in item for item in problems), problems


def test_ignores_git_comment_lines():
    """git 模板注释行（# 开头）不算提交内容。"""
    problems = ccm.check_text("# 请为你的变更写一条说明（M17 示例）\nfeat: 新增能力", "test")
    assert problems == []


def test_file_check_tolerates_utf8_bom(tmp_path):
    """带 BOM 的提交信息文件不应因首字节错位而误判 / 漏判。"""
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_bytes("refactor: 清理注释（M17）\n".encode("utf-8-sig"))
    problems = ccm.check_file(str(msg))
    assert any("M17" in item for item in problems), problems


def test_main_returns_nonzero_for_bad_message(tmp_path, capsys):
    """main() 对违规信息返回 1（hook 依赖退出码阻断提交）。"""
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("fix: 修好门禁（T19）\n", encoding="utf-8")
    assert ccm.main(["--file", str(msg)]) == 1


def test_main_returns_zero_for_clean_message(tmp_path):
    """main() 对合规信息返回 0。"""
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text("refactor: 提交信息检查脚本\n", encoding="utf-8")
    assert ccm.main(["--file", str(msg)]) == 0
