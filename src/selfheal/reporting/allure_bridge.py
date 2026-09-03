"""Allure 报告桥（T18）——环境页 / 标签组织 / 证据附件的唯一接触点。

设计（决策 D15，同 D5 零侵入精神）：
- 核心代码与 Allure 的**唯一**依赖检测点：模块顶部一次性探测 `_HAS_ALLURE`，
  未安装 allure-pytest 时全部 API 降级为安全 no-op，调用方（conftest / hooks）
  无需各自 try-import allure。
- write_environment：`--alluredir` 启用时写 environment.properties（报告环境页）。
- feature_label_for / apply_dynamic_labels：pytest marker → allure 标签映射
  （优先级 erp > healing > e2e > unit，多 marker 只取最高一个，避免标签爆炸）。
- attach_json / attach_file：best-effort 证据附件（自愈记录 / Playwright trace）。

Allure 标签使用 **dynamic API**（须在测试上下文中调用，见 conftest autouse fixture）：
allure.label 的 hook 机制在收集期无 item 上下文，dynamic 在 setup 期生效（实测确认）。
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅类型检查，避免运行期依赖
    from selfheal.config import Settings

try:  # 一次性探测：未装 allure-pytest 时全部 API 为 no-op（[dev] extra 会安装）
    import allure

    _HAS_ALLURE = True
except ImportError:  # pragma: no cover - 无 allure 环境（降级为纯 HTML 看板）
    _HAS_ALLURE = False

# marker → feature 映射；优先级从高到低（多 marker 只取最高，避免标签爆炸）
_FEATURE_PRIORITY: tuple[str, ...] = ("erp", "healing", "e2e", "unit")
_FEATURE_BY_MARKER: dict[str, str] = {
    "erp": "ERP 被测系统",  # T23：管伊佳 ERP 用例（被测系统维度优先于能力维度）
    "healing": "AI 自愈",
    "e2e": "端到端",
    "unit": "单元测试",
}


def feature_label_for(item: Any) -> str | None:
    """按优先级（erp > healing > e2e > unit）返回该测试的唯一 feature 名；无匹配返回 None。"""
    markers = {marker.name for marker in item.iter_markers()}
    for name in _FEATURE_PRIORITY:
        if name in markers:
            return _FEATURE_BY_MARKER[name]
    return None


def apply_dynamic_labels(node: Any) -> bool:
    """给当前测试动态打 Allure 标签（epic + feature）。

    须在测试上下文中调用（conftest autouse fixture 内）；无 allure / 无匹配
    marker → False（零影响）。
    """
    if not _HAS_ALLURE:
        return False
    feature = feature_label_for(node)
    if feature is None:
        return False
    allure.dynamic.epic("AutoAiSelfHeal")
    allure.dynamic.feature(feature)
    return True


def write_environment(results_dir: str | os.PathLike | None, settings: Settings) -> bool:
    """写 environment.properties（Allure 报告环境页数据源）。

    `--alluredir` 未启用（results_dir 为空）或无 allure → False；写失败不抛
    （best-effort）。须在测试会话**结束后**写：allure-pytest 不会清理既有
    results 目录，晚写不丢。
    """
    if not results_dir or not _HAS_ALLURE:
        return False
    try:
        directory = Path(results_dir)
        directory.mkdir(parents=True, exist_ok=True)
        lines = [
            f"Python={sys.version.split()[0]}",
            f"Platform={platform.platform()}",
            f"Browser.Channel={settings.browser.channel}",
            f"Browser.Trace={settings.browser.trace}",
            f"Healing.Enabled={settings.healing.enabled}",
            f"Healing.ConfidenceThreshold={settings.healing.confidence_threshold}",
            f"Healing.EarlyAcceptThreshold={settings.healing.early_accept_threshold}",
            f"CI={'true' if os.getenv('CI') else 'false'}",
            f"Git.Commit={os.getenv('GITHUB_SHA', 'local')}",
        ]
        (directory / "environment.properties").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001 - 环境页失败不影响测试
        return False


def attach_json(payload: dict, name: str = "附件") -> bool:
    """JSON 证据附件（best-effort：无 allure / 附着失败 → False，不抛）。"""
    if not _HAS_ALLURE:
        return False
    try:
        allure.attach(
            json.dumps(payload, ensure_ascii=False, indent=2),
            name=name,
            attachment_type=allure.attachment_type.JSON,
        )
        return True
    except Exception:  # noqa: BLE001 - 附件失败不影响测试
        return False


def attach_file(path: str | os.PathLike, name: str, type_name: str = "ZIP") -> bool:
    """文件证据附件（trace zip 等；best-effort：无 allure / 文件缺失 / 失败 → False）。"""
    if not _HAS_ALLURE or not os.path.exists(path):
        return False
    try:
        allure.attach.file(
            str(path),
            name=name,
            attachment_type=getattr(allure.attachment_type, type_name),
        )
        return True
    except Exception:  # noqa: BLE001 - 附件失败不影响测试
        return False
