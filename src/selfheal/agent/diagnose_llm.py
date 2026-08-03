"""LLM 智能诊断（Phase 2，继承规则式）。

借助 LLM 结合精简 DOM + 失败上下文判定根因，白名单约束输出：
- 调用异常（网络 / SDK）→ 重试，耗尽后**降级回规则式**（super().diagnose，Phase 1 行为）。
- 模型有回复但越界 / 解析失败 → 返回 "unknown"。
绝不向上抛异常，保证闭环在模型不可用时仍可运行。
"""

from __future__ import annotations

from selfheal.agent.diagnose import Diagnoser, FailureContext
from selfheal.agent.llm_io import build_compact_dom, extract_json, safe_str
from selfheal.collect.collector import Scene
from selfheal.llm.base import ChatMessage, LLMClient

# 允许的根因白名单
_ALLOWED_CAUSES = frozenset({"not_found", "not_visible", "covered", "timeout", "unknown"})

_PROMPT_TEMPLATE = """你是 Web UI 自动化测试专家。定位器匹配失败，请判断根因。
原始选择器: {selector}
页面 URL: {url}
失败信息: {failure_type} {failure_msg}
当前页面可交互元素索引:
{compact_dom}
要求: 只输出一个 JSON 对象，不要输出解释:
{{"root_cause": "not_found|not_visible|covered|timeout|unknown", "reason": "30字以内的中文原因"}}"""


class LLMDiagnoser(Diagnoser):
    """LLM 根因诊断器；不可用时回退规则式。"""

    def __init__(self, client: LLMClient, *, max_attempts: int = 1):
        super().__init__()
        self._client = client
        self._max_attempts = max_attempts

    def diagnose(
        self,
        scene: Scene,
        selector: str,
        failure: FailureContext | None = None,
    ) -> str:
        prompt = _PROMPT_TEMPLATE.format(
            selector=selector,
            url=scene.url,
            failure_type=(failure.failure_type if failure else None) or "",
            failure_msg=(failure.message if failure else None) or "",
            compact_dom="\n".join(build_compact_dom(scene.dom_snapshot)) or "(空)",
        )
        for _ in range(max(1, self._max_attempts)):
            try:
                reply = self._client.chat([ChatMessage("user", prompt)])
            except Exception:  # noqa: BLE001 - 调用失败尝试下轮，耗尽后规则式兜底
                continue
            data = extract_json(reply)
            cause = safe_str(data, "root_cause") if data else ""
            if cause in _ALLOWED_CAUSES:
                return cause
            return "unknown"  # 模型回复了但越界/解析失败 → 未知
        return super().diagnose(scene, selector)  # 调用异常耗尽 → 规则式兜底（Phase 1 行为）
