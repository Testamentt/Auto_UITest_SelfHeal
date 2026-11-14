# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""注释卫生守卫 —— 「去 AI 味」规则的机器可判定部分。

**产物内容**（注释 / docstring / 配置注释 / HTML 注释 / Markdown 正文）里不允许出现：

- 内部工单与批次编号：`T` 数字、`M` 数字、`R` 数字、`H` 数字、`V` 数字、`B` 数字、
  `C` 数字、`L` 两位数字、`P` 数字、`m` 数字。
- 例外：公开 ADR 编号（`docs/decisions.md` 定义的 D 序列）与检索层级术语 `L1`–`L4`。

**扫描范围 = 仓库全部 tracked 文件**（`git ls-files`），而不是某个目录白名单。
理由：编号的主要来源恰恰是文档与 CI 配置，只扫 `src/scripts/tests` 会留出最大盲区。

**为什么按 AST + tokenize 而非全文正则**：字符串字面量里有测试数据（`#old`、`fpA`）、
CSS 颜色（`#666`）、模型版本（`deepseek/deepseek-v4.1-flash`）。全文正则会把这些判成违规，
从而逼迫作者改测试数据——那才是真正的"为规则服务"。这里只检查**人写的注释**：
Python 走 `tokenize` 取 `COMMENT`、`ast` 取 docstring；YAML / TOML / INI 取 `#` 行；
HTML 取 `<!-- -->`；Markdown 剥掉围栏代码块后检查正文（代码块内的示例代码仍按代码处理）。

**为什么要有这条守卫**：规则只写进文档（README「提交规范」）只是宣言，没有强制点就会回潮。
本用例把它变成 `-m unit` 门禁的一条断言——改注释时若带回编号，单测直接红。
"""

import ast
import io
import re
import subprocess
import tokenize
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_SCAN_SUFFIXES = (".py", ".yml", ".yaml", ".toml", ".md", ".html", ".cfg", ".ini", ".sh")

# 工单类编号。前后不接字母 / 数字 / 点，避免把 V4.1（模型版本）与 log-2 判成编号。
# **必须包含 `D[0-9]+`**：公开 ADR 要经 `_is_allowed` 判定后放行，若这里不匹配 D 序列，
# allowlist 分支永远走不到（死代码），"ADR 被放行"的自检用例也就成了空转的绿灯。
_TICKET = re.compile(
    r"(?<![A-Za-z0-9_.])"
    r"(T[0-9]+|M[0-9]+|R[0-9]+|H[0-9]|V[0-9]+|B[0-9]|C[0-9]|L[0-9]{2}|P[0-9]|m[0-9]|D[0-9]+)"
    r"(?![A-Za-z0-9_.])"
)

# 允许出现的编号：公开 ADR（D 序列）+ 检索层级术语 L1–L4。
_ALLOWED_PREFIX = ("D",)
_ALLOWED_TIER = {f"L{i}" for i in range(1, 5)}


def _iter_files():
    """仓库全部 tracked 文件（`git ls-files` 是权威清单，天然排除 ignored 产物）。"""
    listed = subprocess.run(
        ["git", "ls-files"], cwd=_ROOT, capture_output=True, text=True, encoding="utf-8"
    ).stdout.split()
    for rel in listed:
        path = _ROOT / rel
        if path.suffix not in _SCAN_SUFFIXES or not path.is_file():
            continue
        yield path


def _python_comments(src: str) -> list[tuple[int, str]]:
    """取出 .py 的全部 `#` 注释（含行尾注释），跳过字符串内部的 `#`。"""
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out.append((tok.start[0], tok.string))
    except tokenize.TokenError:  # 语法残缺时退化为逐行扫描，避免守卫本身报错
        for i, line in enumerate(src.splitlines(), 1):
            idx = line.find("#")
            if idx >= 0:
                out.append((i, line[idx:]))
    return out


def _python_docstrings(src: str) -> list[tuple[int, str]]:
    """取出模块 / 类 / 函数 docstring（ast 常量，含行号）。"""
    out = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            value = first.value.value
            if isinstance(value, str):
                out.append((first.lineno, value))
    return out


def _hash_comments(src: str) -> list[tuple[int, str]]:
    """YAML / TOML / INI / shell 等以 `#` 开头的注释行。"""
    return [
        (i, line.strip())
        for i, line in enumerate(src.splitlines(), 1)
        if line.strip().startswith("#")
    ]


def _html_comments(src: str) -> list[tuple[int, str]]:
    out = []
    for match in re.finditer(r"<!--(.*?)-->", src, re.S):
        line = src[: match.start()].count("\n") + 1
        out.append((line, match.group(0)))
    return out


def _markdown_prose(src: str) -> list[tuple[int, str]]:
    """Markdown 正文（剥掉围栏代码块），逐行返回行号与内容。

    代码块里的示例代码属于文档内容而非注释，按"代码"处理、交交 .py 规则；这里只查散文。
    """
    out = []
    in_fence = False
    for i, line in enumerate(src.splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append((i, line))
    return out


def _comment_blocks(path: Path) -> list[tuple[int, str]]:
    src = path.read_text(encoding="utf-8")
    suffix = path.suffix
    if suffix == ".py":
        return _python_comments(src) + _python_docstrings(src)
    if suffix == ".html":
        return _html_comments(src)
    if suffix == ".md":
        return _markdown_prose(src)
    return _hash_comments(src)


def _is_allowed(token: str) -> bool:
    """公开 ADR（D 序列）与层级术语 L1–L4 放行。"""
    if token in _ALLOWED_TIER:
        return True
    return token.startswith(_ALLOWED_PREFIX) and token[1:].isdigit()


def _violations(path: Path) -> list[str]:
    bad = []
    for lineno, text in _comment_blocks(path):
        for match in _TICKET.finditer(text):
            token = match.group(1)
            if _is_allowed(token):
                continue
            try:
                shown = path.relative_to(_ROOT)
            except ValueError:
                shown = path.name
            bad.append(f"{shown}:{lineno} 出现内部编号 {token!r}：{text.strip()[:80]}")
    return bad


def test_no_internal_ticket_ids_in_comments():
    """全仓注释 / docstring / 文档正文不得出现内部工单编号（公开 D 编号与 L1–L4 除外）。"""
    failures = []
    for path in _iter_files():
        failures.extend(_violations(path))
    assert not failures, (
        "以下位置含内部编号，按注释卫生规则改为自解释描述（保留动机、去掉编号）：\n  "
        + "\n  ".join(failures)
    )


def test_guard_detects_violation(tmp_path):
    """护栏自检：确保扫描器真能拦截，避免"空转的绿色"。"""
    sample = tmp_path / "sample.py"
    sample.write_text(
        '"""模块说明（M12 修复后）。"""\n'
        "\n"
        "def test_x():\n"
        '    """用例说明（T99 回归）。"""\n'
        "    value = 1  # L17：解释\n"
        "    assert value == 1\n",
        encoding="utf-8",
    )
    found = _violations(sample)
    assert len(found) == 3, found
    assert any("M12" in item for item in found)
    assert any("T99" in item for item in found)
    assert any("L17" in item for item in found)


def test_guard_allows_public_adr_and_tier_terms(tmp_path):
    """护栏自证：公开 ADR 编号与 L1–L4 层级术语必须放行，否则规则会误伤正常文档。

    **注意**：本用例只有在 `_TICKET` 能匹配到 `D` 序列时才**不是空转**——若正则里没有
    `D[0-9]+`，`_is_allowed` 的 ADR 分支永远不可达，把整个 allowlist 删掉本用例依然通过
    （"测了但测不到"）。下面的 `test_adr_allowlist_is_reachable` 专门锁这一点。
    """
    sample = tmp_path / "ok.py"
    sample.write_text(
        '"""分层护栏（D19）与韧性层（D25）。"""\n'
        "\n"
        "def test_x():\n"
        '    """L1 精确命中 → L3 语义检索（D6 兜底）。"""\n'
        "    value = 1  # D20 逐候选裁决\n"
        "    assert value == 1\n",
        encoding="utf-8",
    )
    assert _violations(sample) == []


def test_adr_allowlist_is_reachable():
    """ADR allowlist 必须**真的被走到**（防"假绿灯"）。

    断言三件事，缺一不可：
    1. `D` 编号能被 `_TICKET` 匹配（否则 allowlist 分支不可达）；
    2. 匹配到的 token 被 `_is_allowed` 放行（公共 ADR 不误伤）；
    3. 把 allowlist 关掉时同一 token **会**被判违规——证明确实经过了该分支，
       而不是"正则压根没匹配上所以没报错"。
    """
    token = "D25"
    match = _TICKET.search(token)
    assert match is not None, "_TICKET 匹配不到 D 序列 → ADR allowlist 是死代码"
    assert _is_allowed(match.group(1)) is True

    # 反向验证：临时清空 allowlist，同一 token 必须变成违规（证明分支被真实执行）
    original_prefix, original_tier = _ALLOWED_PREFIX, _ALLOWED_TIER
    try:
        globals()["_ALLOWED_PREFIX"] = ()
        globals()["_ALLOWED_TIER"] = set()
        assert _is_allowed(match.group(1)) is False
    finally:
        globals()["_ALLOWED_PREFIX"] = original_prefix
        globals()["_ALLOWED_TIER"] = original_tier


def test_guard_skips_markdown_code_fences(tmp_path):
    """护栏自证：Markdown 围栏代码块内的示例代码不按散文检查（否则文档无法展示反例）。"""
    sample = tmp_path / "doc.md"
    sample.write_text(
        "# 标题\n"
        "\n"
        "正常段落，讲设计动机（D19）。\n"
        "\n"
        "```python\n"
        "# 反例：这里演示 M12 这类编号为何不该出现\n"
        "value = 1\n"
        "```\n"
        "\n"
        "结尾段落。\n",
        encoding="utf-8",
    )
    assert _violations(sample) == []
