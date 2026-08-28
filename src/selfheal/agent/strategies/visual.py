"""视觉定位策略（Phase 3）。

把截图 + 描述 + 候选稳定定位器交给多模态模型（VLM），让模型从候选中选出与描述
最匹配的元素并返回 JSON（selector + confidence）。

**防幻觉护栏**：VLM 返回的 selector 必须是候选集中真实存在者（由
dom.build_stable_selector 生成），编造的定位器直接拒绝。
不可用（无 client / 无截图 / 无描述 / 异常 / 解析失败）一律返回 None，被编排器跳过。
"""

from __future__ import annotations

from selfheal.agent.confidence import KEY_VISUAL, calibrate
from selfheal.agent.dom import build_stable_selector, interactive_candidates
from selfheal.agent.llm_io import extract_json, safe_float, safe_str
from selfheal.agent.strategies.base import RepairCandidate, RepairStrategy
from selfheal.agent.strategies.heuristic import score_selector
from selfheal.collect.collector import Scene
from selfheal.llm.base import VisionClient

_PROMPT_TEMPLATE = """你是 Web UI 自动化测试专家。原定位器已失效，请根据描述在截图中识别目标元素。
目标描述: {description}
已失效的原始选择器: {selector}
当前页面候选稳定定位器如下，你的答案必须是其中之一:
{candidates}
要求: 只输出一个 JSON 对象，不要输出解释:
{{"selector": "上面候选之一，找不到则留空字符串", "confidence": 0.0~1.0}}"""


class VisualStrategy(RepairStrategy):
    name = "visual"

    def __init__(self, client: VisionClient | None = None):
        self._client = client

    def repair(self, scene: Scene, original_selector: str, description: str | None = None):
        if self._client is None or not description or not scene.screenshot:
            return None
        candidates = [
            s
            # T8：候选来源优先 Playwright 原生解析（有 page 采集），静态 DOM 快照解析兜底
            for el in interactive_candidates(scene)
            if (s := build_stable_selector(el))
        ]
        if not candidates:
            return None
        prompt = _PROMPT_TEMPLATE.format(
            description=description,
            selector=original_selector,
            candidates="\n".join(candidates),
        )
        try:
            reply = self._client.analyze_image(scene.screenshot, prompt)
            data = extract_json(reply)
        except Exception:  # noqa: BLE001 - VLM 异常返回 None，被编排器跳过
            return None
        if not data:
            return None
        selector = safe_str(data, "selector")
        confidence = safe_float(data, "confidence")
        # 护栏：selector 必须是真实候选之一；置信度越界 → 拒绝
        if selector not in candidates or not (0.0 <= confidence <= 1.0):
            return None
        # C4 跨策略一致性校验：VLM 的挑选与「原选择器 + 描述」的 L2 意图做交叉验证。
        # VLM 置信度是自报值（可能虚高）——若 L2 分数也低，说明大概率"找错区域"，
        # 按线性融合降权；l2=1.0 时不变、l2=0 时压到 0.4×conf（通常低于接受阈值 → 转人审/失败）。
        l2_score = score_selector(scene.dom_snapshot, selector, original_selector, description)
        final_conf = round(confidence * (0.4 + 0.6 * l2_score), 3)
        return RepairCandidate(
            selector=selector,
            # T5：视觉段统一标尺出口（C4 已融合 L2，恒等不二次收缩，契约见 agent/confidence.py）
            confidence=calibrate(KEY_VISUAL, final_conf),
            strategy=self.name,
        )
