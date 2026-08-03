"""语义定位策略（Phase 2，注入可选 LLMClient）。

把元素的自然语言描述 + 精简 DOM 交给 LLM，让其输出稳定定位器与置信度。

**防幻觉护栏**：LLM 返回的 selector 必须能在精简 DOM 索引中真实存在
（基于 dom.build_stable_selector 产出的稳定定位器判定），否则拒绝 ——
模型编造的定位器直接返回 None，不参与编排。
不可用（无 client / 无 description / 调用异常 / 解析失败）一律返回 None。
"""

from __future__ import annotations

from selfheal.agent.dom import build_stable_selector, parse_interactive_elements
from selfheal.agent.llm_io import build_compact_dom, extract_json, safe_float, safe_str
from selfheal.agent.strategies.base import RepairCandidate, RepairStrategy
from selfheal.collect.collector import Scene
from selfheal.llm.base import ChatMessage, LLMClient

_PROMPT_TEMPLATE = """你是 Web UI 自动化测试专家。原定位器已失效，请根据描述在页面中重新定位目标元素。
已失效的原始选择器: {selector}
目标描述: {description}
当前页面可交互元素索引（每行一条，含可用的稳定 selector）:
{compact_dom}
要求: 只输出一个 JSON 对象，不要输出解释:
{{"selector": "上面索引中存在的稳定 selector，找不到则留空字符串", "confidence": 0.0~1.0}}"""


class SemanticStrategy(RepairStrategy):
    name = "semantic"

    def __init__(self, client: LLMClient | None = None):
        self._client = client

    def repair(self, scene: Scene, original_selector: str, description: str | None = None):
        if self._client is None or not description or not scene.dom_snapshot:
            return None
        # 护栏白名单：页面中真实存在、可由 build_stable_selector 生成的稳定定位器
        real_selectors = {
            s
            for el in parse_interactive_elements(scene.dom_snapshot)
            if (s := build_stable_selector(el))
        }
        if not real_selectors:
            return None

        rows = build_compact_dom(scene.dom_snapshot)
        prompt = _PROMPT_TEMPLATE.format(
            selector=original_selector,
            description=description,
            compact_dom="\n".join(rows),
        )
        try:
            reply = self._client.chat([ChatMessage("user", prompt)])
            data = extract_json(reply)
        except Exception:  # noqa: BLE001 - 模型异常返回 None
            return None
        if not data:
            return None

        selector = safe_str(data, "selector")
        confidence = safe_float(data, "confidence")
        # 护栏：selector 必须在真实索引中存在；置信度越界 / 空 → 拒绝
        if selector not in real_selectors or not (0.0 <= confidence <= 1.0):
            return None
        return RepairCandidate(
            selector=selector, confidence=round(confidence, 3), strategy=self.name
        )
