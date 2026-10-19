"""架构守卫测试 —— 分层倒置护栏（2026-09-03 子代理复核 V5/m1 立项；2026-10-10 M10-③ 扩展）。

**不变量**（源码级静态扫描；判定走 AST，注释 / docstring / 字符串里的路径名不算）：

- `llm/`、`knowledge/`：不得反向 import `selfheal.agent`（V5 m1：`llm/io.py` 曾反向依赖
  `agent.dom` 构建精简 DOM）。
- `engine/`（能力建设层）、`collect/`（数据采集层）：不得 import 上层
  `selfheal.agent` / `selfheal.knowledge` / `selfheal.reporting`（M10：DTO 与装配点曾落在
  引擎层，导致 `import selfheal.engine.*` 在 import 期拉起整个 Agent 层）。
- `dom/`（中立 DOM 工具包）：不得 import 任何业务层（agent / knowledge / reporting /
  engine / collect）——否则 engine 与 collect 会经由 dom 被动拉起上层，护栏被绕过。

**为什么用 AST 而非正则**：正则会把注释与 docstring 里出现的模块名误判为违规
（本文件自身、`llm/io.py` 的历史说明都提到过旧路径）；AST 只看真实的 Import / ImportFrom
节点，且 `if TYPE_CHECKING:` 内的导入同样计入（它照样表达跨层依赖）。

护栏自身有自检用例（`test_guard_detects_violation`）：确保扫描器真能拦截违规，
避免"空转的绿色"。
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SRC = Path(__file__).resolve().parents[2] / "src" / "selfheal"

# 层 → 禁止 import 的上层包（按"包或子模块"前缀匹配）
_FORBIDDEN: dict[str, tuple[str, ...]] = {
    "llm": ("selfheal.agent",),
    "knowledge": ("selfheal.agent",),
    "engine": ("selfheal.agent", "selfheal.knowledge", "selfheal.reporting"),
    "collect": ("selfheal.agent", "selfheal.knowledge", "selfheal.reporting"),
    "dom": (
        "selfheal.agent",
        "selfheal.knowledge",
        "selfheal.reporting",
        "selfheal.engine",
        "selfheal.collect",
    ),
}


def _imported_modules(source: str) -> list[tuple[int, str]]:
    """源码文本里全部**绝对导入**的 (行号, 模块名)；相对导入是包内引用，不跨层，忽略。"""
    tree = ast.parse(source)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # from .x import y：本包内，天然不跨层
                continue
            found.append((node.lineno, node.module or ""))
    return found


def _hits(source: str, forbidden: tuple[str, ...]) -> list[tuple[int, str]]:
    """源码文本里命中禁止清单的导入（行号, 模块名），按出现顺序返回。"""
    return [
        (lineno, module)
        for lineno, module in _imported_modules(source)
        if any(module == bad or module.startswith(f"{bad}.") for bad in forbidden)
    ]


def _scan_layer(layer: str) -> list[str]:
    """扫描某层全部 .py，返回 "相对路径:行号 -> 模块" 形式的违规清单（空列表 = 合规）。"""
    forbidden = _FORBIDDEN[layer]
    violations: list[str] = []
    for path in sorted((_SRC / layer).rglob("*.py")):
        rel = path.relative_to(_SRC)
        for lineno, module in _hits(path.read_text(encoding="utf-8"), forbidden):
            violations.append(f"{rel}:{lineno} -> {module}")
    return violations


@pytest.mark.parametrize("layer", sorted(_FORBIDDEN))
def test_layer_does_not_import_upper_layers(layer):
    """各层不得反向 import 上层（分层倒置护栏，见模块 docstring）。"""
    violations = _scan_layer(layer)
    assert violations == [], f"{layer}/ 层出现反向依赖（分层倒置）: {violations}"


def test_guard_detects_violation():
    """护栏自检：导入违规必须被拦下，而 docstring / 注释 / 相对导入里的同名字样不算。

    没有这条用例，"扫描器写错 → 恒绿"的分层失守不会被任何测试发现。
    """
    source = (
        '"""engine 层不该 `from selfheal.agent import x`——这里只是文档文字，不算违规。"""\n'
        "# 注释里的 selfheal.knowledge 同样不算\n"
        "import selfheal.knowledge.store\n"
        "from selfheal.reporting import hooks\n"
        "from .sibling import helper\n"
        "from __future__ import annotations\n"
    )
    hits = _hits(source, _FORBIDDEN["engine"])
    assert [module for _, module in hits] == ["selfheal.knowledge.store", "selfheal.reporting"]
    assert [lineno for lineno, _ in hits] == [3, 4]


def test_guard_ignores_layer_internal_and_unrelated_imports():
    """层内互引与无关依赖（stdlib / config / dom / contracts）不误报——护栏只拦跨层反向依赖。"""
    source = (
        "import ast\n"
        "from selfheal.config import Settings\n"
        "from selfheal.contracts import HealOutcome\n"
        "from selfheal.dom import Element\n"
        "from selfheal.engine.popup_guard import PopupGuard\n"
    )
    assert _hits(source, _FORBIDDEN["engine"]) == []
