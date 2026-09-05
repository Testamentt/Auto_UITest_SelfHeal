"""架构守卫测试（2026-09-03 子代理复核 V5 / m1）：

llm/ 与 knowledge/ 是 agent 层之下的抽象/设施层，**禁止反向 import selfheal.agent**
（此前 llm/io.py 反向导入 agent.dom 构建精简 DOM，已将 build_compact_dom 上移
agent/dom/compact.py 修复）。本测试以静态源码扫描固化该分层约束，防止回归。
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SRC = Path(__file__).resolve().parents[2] / "src" / "selfheal"
# 匹配 from selfheal.agent... / import selfheal.agent...（含 from selfheal.agent.dom import x）
_FORBIDDEN = re.compile(r"^\s*(?:from|import)\s+selfheal\.agent\b", re.MULTILINE)


@pytest.mark.parametrize("layer", ["llm", "knowledge"])
def test_lower_layers_do_not_import_agent(layer):
    violations = [
        str(path.relative_to(_SRC))
        for path in sorted((_SRC / layer).rglob("*.py"))
        if _FORBIDDEN.search(path.read_text(encoding="utf-8"))
    ]
    assert violations == [], f"{layer}/ 层出现 agent 反向依赖: {violations}"
