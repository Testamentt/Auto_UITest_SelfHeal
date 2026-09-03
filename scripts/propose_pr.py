"""修复建议自动开草稿 PR（T22）：把 T15 人审清单变成一键可审的草稿 PR。

职责边界（守 T15 人审原则）：
- 本脚本只做「PR 内容组装 + 查重 + 调 gh 开**草稿** PR」，**绝不自动合并**、不改代码库；
- 分支与产物提交由 CI 步骤完成（见 .github/workflows/ci.yml propose-pr job）；
- 无建议产物 / 已有同名标签 open PR / gh 不可用 → 全部安全跳过（exit 0，不炸流水线）。

用法（CI）：
    python scripts/propose_pr.py \
        --proposals-dir reports/fix-proposals \
        --head-branch selfheal/proposals \
        --label selfheal-proposal
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("selfheal.propose_pr")

CHECKLIST = """## ⚠️ 人审清单（T22 · 草稿 PR，绝不自动合并）

- [ ] 逐条核对「原 → 新」映射是否与真实 UI 一致（截图 / 手工点验）
- [ ] 确认 `verified` 标记：真自愈（原定位器确已失效）≠ flaky 侥幸通过
- [ ] 按建议**手动更新 POM/测试代码**（本 PR 仅提交建议文件作为存档，不含代码改动）
- [ ] 合并本 PR 仅表示建议已采纳；代码改动请另行提交
"""


def load_proposals(proposals_dir: str | Path) -> list[dict[str, Any]]:
    """读取 fix-proposals 目录全部 JSON 建议；坏文件跳过、目录缺失返回空。"""
    directory = Path(proposals_dir)
    if not directory.exists():
        return []
    proposals: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("original_selector"):
                proposals.append(data)
        except Exception:  # noqa: BLE001 - 单个坏文件不阻塞其余建议
            logger.warning("建议文件解析失败（跳过）: %s", path, exc_info=True)
    return proposals


def _escape(text: str) -> str:
    """转义 Markdown 表格中的竖线/换行（与 T15 fix_proposals.html_escape 同款约定；
    本地实现保持脚本零 selfheal 依赖——propose-pr CI job 不安装项目包）。"""
    return (text or "").replace("|", "\\|").replace("\n", " ")


def build_pr_body(proposals: list[dict[str, Any]]) -> str:
    """建议列表 → PR body（人审 checklist 头 + 摘要表；纯函数可快照）。"""
    lines = [
        CHECKLIST,
        "## 建议摘要",
        "",
        "| 时间 | 原定位器 | 新定位器 | 策略 | 置信度 | 验证 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for p in proposals:
        verified = "✅ 真自愈" if p.get("verified") else "⚠️ flaky"
        lines.append(
            f"| {p.get('created_at', '')} | `{_escape(p.get('original_selector', ''))}` "
            f"| `{_escape(p.get('new_selector', ''))}` | {p.get('strategy', '')} "
            f"| {float(p.get('confidence', 0.0)):.2f} | {verified} |"
        )
    lines += [
        "",
        f"共 **{len(proposals)}** 条建议（`applied=false`，人确认后按建议手动改代码）。",
    ]
    return "\n".join(lines) + "\n"


def _run_gh(args: list[str], timeout_s: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout_s)


def has_open_pr(label: str) -> bool:
    """已有该标签的 open PR → True（防重复开）。gh 不可用按 False 处理（由 create 兜底报错）。"""
    try:
        result = _run_gh(["pr", "list", "--label", label, "--state", "open", "--json", "number"])
    except Exception:  # noqa: BLE001 - gh 缺失/超时：按无 open PR 处理
        logger.warning("gh pr list 失败（按无 open PR 处理）", exc_info=True)
        return False
    return result.returncode == 0 and result.stdout.strip() not in ("", "[]")


def create_draft_pr(head_branch: str, title: str, body: str, label: str) -> bool:
    """调 gh 开草稿 PR（best-effort：失败记 warning 返回 False，不抛）。"""
    try:
        result = _run_gh(
            [
                "pr",
                "create",
                "--draft",
                "--head",
                head_branch,
                "--title",
                title,
                "--body",
                body,
                "--label",
                label,
            ]
        )
        if result.returncode != 0:
            logger.warning("gh pr create 失败：%s", (result.stderr or "")[:300])
            return False
        return True
    except Exception:  # noqa: BLE001 - gh 缺失/网络失败不炸 CI
        logger.warning("gh pr create 异常（best-effort 忽略）", exc_info=True)
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="T22：修复建议自动开草稿 PR")
    parser.add_argument("--proposals-dir", default="reports/fix-proposals")
    parser.add_argument("--head-branch", default="selfheal/proposals")
    parser.add_argument("--label", default="selfheal-proposal")
    parser.add_argument("--run-url", default="", help="触发本次运行的 CI 链接（写进 PR 标题摘要）")
    args = parser.parse_args(argv)

    proposals = load_proposals(args.proposals_dir)
    if not proposals:
        print("无修复建议产物，跳过开 PR（exit 0）")
        return 0
    suffix = f"（run: {args.run_url}）" if args.run_url else ""
    title = f"🔧 自愈修复建议人审清单（{len(proposals)} 条）{suffix}".strip()
    body = build_pr_body(proposals)

    if has_open_pr(args.label):
        print(f"已存在标签 [{args.label}] 的 open PR，跳过重复创建（exit 0）")
        return 0
    ok = create_draft_pr(args.head_branch, title, body, args.label)
    print(f"草稿 PR{'已创建' if ok else '创建失败（已忽略）'}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
