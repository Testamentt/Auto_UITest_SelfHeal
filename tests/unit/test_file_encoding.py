# -*- coding: utf-8 -*-
# 本项目统一使用 UTF-8（无 BOM）编码。
"""文件编码契约守卫 —— 「全仓统一 UTF-8 + 首行声明」的机器可判定部分。

**守什么**（三条断言，缺一不可）：

1. 文本文件必须能被 **UTF-8 严格解码**。凡是按 GBK / GB18030 / UTF-16 存过的中文，
   到这一步就会抛 `UnicodeDecodeError` —— 这正是最典型的「中文乱码」成因：文件本身
   已经写坏了，而不是显示端选错了字体。
2. 文件**不得带 BOM**。BOM（`\\ufeff`）会让 Python 首行 shebang / PEP 263 cookie 失效、
   把 YAML 首个键名变成隐藏字符、把 Markdown 首行标题顶成正文；它在编辑器里看不见，
   却能在 CI 里变成莫名其妙的解析错误。
3. 首行（有 shebang 时是第 2 行、有 YAML frontmatter 时是 frontmatter 之后）必须写着
   编码声明。声明是给人看的**可见事实**：乱码在 diff 里看不出来，缺一行声明却一眼可见，
   新人接手时也不必猜「这个仓库到底该用什么编码保存」。

**扫描范围**：`git ls-files` + `git ls-files --others --exclude-standard`（未提交的新文件
同样要过关，否则「先提交带病文件再补救」的路径就敞着）。跳过 `LICENSE`（第三方许可原文）、
`docs/archive/`（只读历史）与 `.json`（JSON 规范不支持注释，强行加声明会让解析器报错）。

**为什么要有这条守卫**：编码约定写进规则文档只是宣言，没有强制点就会回潮——
Windows 中文环境下记事本、老版 cmd、部分 IDE 默认 ANSI/GBK，随手一存就是乱码。
本用例把它变成 `-m unit` 门禁的一条断言。
"""

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]

# 声明文案。与 README「文件编码」小节、各文件首行保持一致；改这里必须同步改全仓文件。
NOTICE = "本项目统一使用 UTF-8（无 BOM）编码。"
PY_COOKIE = "# -*- coding: utf-8 -*-"

# 判定风格：py 走 PEP 263 cookie + 中文声明两行；md/html 用 HTML 注释；其余用 # 注释。
_PY_SUFFIXES = (".py",)
_HTML_SUFFIXES = (".md", ".html")
_HASH_SUFFIXES = (".yml", ".yaml", ".toml", ".example", ".cfg", ".ini", ".sh", ".txt")
_HASH_NAMES = (".gitignore", ".gitattributes", ".editorconfig", "commit-msg")

# 不参与检查：第三方许可原文、只读历史。JSON 不在后缀白名单里（规范不支持注释）。
_SKIP_PREFIXES = ("docs/archive/",)
_SKIP_NAMES = ("LICENSE",)

_BOM = b"\xef\xbb\xbf"

# 问题分类标记：让「编码错」「带 BOM」「缺声明」三类断言各管各的，互不串味。
KIND_ENCODING = "encoding"
KIND_BOM = "bom"
KIND_DECLARATION = "declaration"


def _git_paths(*args: str) -> list[str]:
    """调用 git 列出文件（`-z` 避免中文路径被转义）。"""
    out = subprocess.run(["git", *args, "-z"], cwd=_ROOT, capture_output=True).stdout
    return [item for item in out.decode("utf-8").split("\0") if item]


def _iter_text_files():
    """仓库全部需检查的文本文件（tracked + 未提交的新文件）。"""
    seen: set[str] = set()
    for rel in _git_paths("ls-files") + _git_paths("ls-files", "--others", "--exclude-standard"):
        if rel in seen:
            continue
        seen.add(rel)
        if rel in _SKIP_NAMES or any(rel.startswith(p) for p in _SKIP_PREFIXES):
            continue
        if not (
            rel.endswith(_PY_SUFFIXES)
            or rel.endswith(_HTML_SUFFIXES)
            or rel.endswith(_HASH_SUFFIXES)
            or Path(rel).name in _HASH_NAMES
        ):
            continue
        path = _ROOT / rel
        if path.is_file():
            yield rel, path


def _style(rel: str) -> str:
    """文件该用哪种声明形态。"""
    if rel.endswith(_PY_SUFFIXES):
        return "py"
    if rel.endswith(_HTML_SUFFIXES):
        return "html"
    return "hash"


def _expected_lines(style: str) -> list[str]:
    """该风格的声明行（按顺序）。"""
    if style == "py":
        return [PY_COOKIE, f"# {NOTICE}"]
    if style == "html":
        return [f"<!-- {NOTICE} -->"]
    return [f"# {NOTICE}"]


def _declaration_offset(lines: list[str]) -> int:
    """声明应出现在第几行（0 基）。shebang 与 YAML frontmatter 必须留在最前面。"""
    if lines and lines[0].startswith("#!"):
        return 1
    if len(lines) > 2 and lines[0].strip() == "---":
        for index in range(1, min(len(lines), 40)):
            if lines[index].strip() == "---":
                return index + 1
    return 0


def check_encoding(rel: str, raw: bytes) -> list[tuple[str, str]]:
    """检查单个文件的编码与 BOM，返回 [(类别, 说明)]。"""
    problems: list[tuple[str, str]] = []
    if raw.startswith(_BOM):
        problems.append(
            (KIND_BOM, f"{rel}: 带 UTF-8 BOM（首字节 EF BB BF），会让首行声明与 YAML 首键失效")
        )
        raw = raw[len(_BOM) :]
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        problems.append(
            (
                KIND_ENCODING,
                f"{rel}: 无法按 UTF-8 解码（{exc.reason}，字节偏移 {exc.start}），"
                "多为按 GBK/ANSI 存盘，中文会显示为乱码",
            )
        )
    return problems


def check_declaration(rel: str, raw: bytes) -> list[tuple[str, str]]:
    """检查首行编码声明。非 UTF-8 的文件由 check_encoding 报告，这里不重复报。"""
    if raw.startswith(_BOM):
        raw = raw[len(_BOM) :]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return []
    lines = text.splitlines()
    offset = _declaration_offset(lines)
    want = _expected_lines(_style(rel))
    actual = lines[offset : offset + len(want)]
    if actual == want:
        return []
    found = " | ".join(actual) if actual else "（空文件）"
    return [
        (
            KIND_DECLARATION,
            f"{rel}: 第 {offset + 1} 行起缺少 UTF-8 编码声明\n"
            f"      期望: {' | '.join(want)}\n"
            f"      实际: {found}",
        )
    ]


def check_text_file(rel: str, raw: bytes) -> list[tuple[str, str]]:
    """检查单个文件的完整编码契约，返回 [(类别, 说明)]（空列表 = 合规）。"""
    return check_encoding(rel, raw) + check_declaration(rel, raw)


def _sweep(kind: str) -> list[str]:
    """全仓扫描，挑出指定类别的问题。"""
    return [
        message
        for rel, path in _iter_text_files()
        for problem_kind, message in check_text_file(rel, path.read_bytes())
        if problem_kind == kind
    ]


def test_files_decode_as_utf8():
    """全仓文本文件必须能按 UTF-8 严格解码（GBK / GB18030 / UTF-16 存盘一律失败）。"""
    failures = _sweep(KIND_ENCODING)
    assert not failures, "以下文件不是合法 UTF-8：\n  " + "\n  ".join(failures)


def test_files_have_no_bom():
    """全仓文本文件不得带 UTF-8 BOM。"""
    failures = _sweep(KIND_BOM)
    assert not failures, "以下文件带 BOM，请另存为「UTF-8 无 BOM」：\n  " + "\n  ".join(failures)


def test_files_declare_encoding():
    """全仓文本文件首行必须带 UTF-8 编码声明（shebang / frontmatter 例外见 _declaration_offset）。"""
    failures = _sweep(KIND_DECLARATION)
    assert not failures, (
        "以下文件缺少首行编码声明（规范见 README「文件编码」小节）：\n  " + "\n  ".join(failures)
    )


def test_guard_detects_gbk_file():
    """守卫自检：按 GBK 存盘的中文文件必须被判为编码违规。"""
    raw = "# 中文注释\n".encode("gbk")
    kinds = [kind for kind, _ in check_text_file("gbk.py", raw)]
    assert KIND_ENCODING in kinds, "GBK 文件应当被判为编码违规"


def test_guard_detects_missing_declaration():
    """守卫自检：缺首行声明要报错，且提示里能看出期望内容。"""
    raw = '"""没写声明的模块。"""\n'.encode()
    problems = check_text_file("plain.py", raw)
    assert [kind for kind, _ in problems] == [KIND_DECLARATION], problems
    assert PY_COOKIE in problems[0][1]


def test_guard_detects_bom():
    """守卫自检：带 BOM 的文件要报错（BOM 无形，只能靠字节判断）。"""
    raw = _BOM + f"<!-- {NOTICE} -->\n# 标题\n".encode()
    kinds = [kind for kind, _ in check_text_file("bom.md", raw)]
    assert KIND_BOM in kinds, "带 BOM 的文件应当被判为违规"


def test_guard_allows_shebang(tmp_path):
    """守卫自证：有 shebang 的文件，声明顺延到第 2 行，不得误判。"""
    raw = f"#!/usr/bin/env python\n{PY_COOKIE}\n# {NOTICE}\n".encode()
    (tmp_path / "tool.py").write_bytes(raw)
    assert check_text_file("tool.py", raw) == []


def test_guard_allows_yaml_frontmatter():
    """守卫自证：带 YAML frontmatter 的 Markdown，声明顺延到 frontmatter 之后，不得误判。"""
    raw = f"---\nname: demo\ndescription: 示例\n---\n<!-- {NOTICE} -->\n# 标题\n".encode()
    assert check_text_file(".claude/skills/demo/SKILL.md", raw) == []


def test_guard_allows_compliant_files():
    """守卫自证：三种声明形态合规时不得误报。"""
    cases = {
        "ok.py": f"{PY_COOKIE}\n# {NOTICE}\n".encode(),
        "ok.html": f"<!-- {NOTICE} -->\n<html></html>\n".encode(),
        "ok.yml": f"# {NOTICE}\nkey: value\n".encode(),
    }
    for rel, raw in cases.items():
        assert check_text_file(rel, raw) == [], rel
