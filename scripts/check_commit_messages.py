#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""提交信息卫生检查：拒绝内部工单编号与 AI 协作署名。

**为什么需要独立脚本**：`git log` 的可检查范围与 pytest 不同——pytest 只能看到工作区，
提交信息只能从 git 历史里取。两者共用同一份正则，避免"规则两处定义、两处漂移"。

**用法**

```bash
# 检查最近一条提交信息（本地提交前自检）
python scripts/check_commit_messages.py --last

# 检查一段区间（CI 里检查 PR 的全部提交）
python scripts/check_commit_messages.py origin/main..HEAD

# 只检查某个文件内容（供 commit-msg hook 复用）
python scripts/check_commit_messages.py --file .git/COMMIT_EDITMSG
```

退出码：0 = 全部通过；1 = 发现问题（打印 `文件:行` 或 `commit:问题` 形式的清单）。
"""

from __future__ import annotations

import argparse
import contextlib
import re
import subprocess
import sys

# 内部工单 / 批次编号。与 tests/unit/test_comment_hygiene.py 的 _TICKET 保持同一形状：
# 前后不接字母 / 数字 / 点，避免把 v4.1（模型版本）、log-2 之类判成编号。
# **必须含 `D[0-9]+`**：公开 ADR 要经 `_is_allowed` 放行；若这里不匹配 D 序列，
# allowlist 分支永远不可达（死代码），"ADR 被放行"就只是没人验证过的假设。
TICKET = re.compile(
    r"(?<![A-Za-z0-9_.])"
    r"(T[0-9]+|M[0-9]+|R[0-9]+|H[0-9]|V[0-9]+|B[0-9]|C[0-9]|L[0-9]{2}|P[0-9]|m[0-9]|D[0-9]+)"
    r"(?![A-Za-z0-9_.])"
)

# 公开 ADR（D 序列）与检索层级术语 L1–L4 放行。
ALLOWED_TIER = {f"L{i}" for i in range(1, 5)}

# 协作署名：提交历史里不该出现"谁用什么工具写的"。
ATTRIBUTION = re.compile(
    r"(co-authored-by:\s*.*(ai|gpt|claude|copilot|assistant)"
    r"|generated\s+by\s+(ai|gpt|claude|copilot)"
    r"|ai\s*协助|由\s*ai\s*(生成|编写|补))",
    re.IGNORECASE,
)

# 过程叙述：只描述协作过程、不解释设计动机的句子。
NARRATION = re.compile(r"(审查发现|评审后|复核加固|收口轮|本批|立项|审计复现|评估结论)")


def _is_allowed(token: str) -> bool:
    """公开 ADR 编号（D 开头 + 数字）与 L1–L4 放行。"""
    if token in ALLOWED_TIER:
        return True
    return token.startswith("D") and token[1:].isdigit()


def check_text(text: str, label: str) -> list[str]:
    """检查一段提交信息文本，返回问题清单。"""
    problems: list[str] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        if line.startswith("#"):  # git 的注释行（模板提示）不算提交内容
            continue
        for match in TICKET.finditer(line):
            token = match.group(1)
            if _is_allowed(token):
                continue
            problems.append(f"{label}:{line_no} 提交信息含内部编号 {token!r}：{line.strip()[:90]}")
        if ATTRIBUTION.search(line):
            problems.append(f"{label}:{line_no} 提交信息含 AI 协作署名：{line.strip()[:90]}")
        if NARRATION.search(line):
            problems.append(f"{label}:{line_no} 提交信息含协作过程叙述：{line.strip()[:90]}")
    return problems


def check_file(path: str) -> list[str]:
    # utf-8-sig：PowerShell 的 `Out-File -Encoding utf8` 会带 BOM，
    # 不剥掉会让首行以 \ufeff 开头，编号锚定与提示信息都跟着错位。
    with open(path, encoding="utf-8-sig") as fh:
        return check_text(fh.read(), path)


def check_commits(rev_range: str, cwd: str | None = None) -> list[str]:
    """逐个提交检查 subject + body。

    **记录分隔符必须用 `%x1e`（ASCII RS），不能用 `%x00`**：`git log --format=...%x00` 会在
    每条记录后追加换行，实际分隔是 `\\x00\\n`，用它去 `split("\\x00\\x00")` 会让所有提交粘成
    一个 chunk —— 空 body 的提交更会被 `len(parts) < 3` 直接跳过（实测 30 条区间只查出 1 条，
    且那 1 条还是误报）。区间模式是 CI 的唯一调用路径，分隔符写错等于门禁形同虚设。

    `cwd` 用于指定仓库根目录（默认当前进程工作目录）；单测需要指向临时仓时必须显式传。
    """
    out = subprocess.run(
        ["git", "log", "--format=%H%x00%s%x00%b%x1e", rev_range],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if out.returncode != 0:
        raise SystemExit(f"git log 失败（区间 {rev_range!r}）：{out.stderr.strip()}")

    problems: list[str] = []
    for record in out.stdout.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("\x00")
        if len(parts) < 2:
            continue
        sha, subject = parts[0].strip(), parts[1]
        body = parts[2] if len(parts) > 2 else ""
        label = f"{sha[:8]} {subject[:50]}"
        problems.extend(check_text(subject, label))
        problems.extend(check_text(body, label))
    return problems


def _ensure_utf8_output() -> None:
    """中文 Windows 控制台默认 GBK：直接打印 emoji / 全角符号会让检查器自己崩掉。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            # 被重定向到不支持的文件对象时忽略：编码兜底本身不该再抛错。
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_output()
    parser = argparse.ArgumentParser(description="提交信息卫生检查（内部编号 / AI 署名）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="检查指定文件（commit-msg hook 用）")
    group.add_argument("--last", action="store_true", help="检查最近一条提交")
    group.add_argument("rev_range", nargs="?", help="检查区间，如 origin/main..HEAD")
    args = parser.parse_args(argv)

    if args.file:
        problems = check_file(args.file)
    elif args.last:
        problems = check_commits("-1")
    else:
        problems = check_commits(args.rev_range)

    if problems:
        print(
            "❌ 提交信息不符合规范（只写「改了什么 + 为什么」，不写批次编号与协作过程）：",
            file=sys.stderr,
        )
        for item in problems:
            print("   " + item, file=sys.stderr)
        print(
            "\n允许的编号：公开 ADR（D 系列，见 docs/decisions.md）与检索层级术语 L1–L4。",
            file=sys.stderr,
        )
        return 1

    print("✅ 提交信息检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
