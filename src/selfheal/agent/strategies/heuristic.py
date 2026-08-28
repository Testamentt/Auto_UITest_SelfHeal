"""启发式匹配策略（Phase 1 主力，零模型依赖）。

不调用 LLM：解析失败现场的 DOM，抽取可交互候选元素的多维属性
（data-testid / id / aria-label / 文本 / name / placeholder），
与「原选择器 + 自然语言描述」的意图做相似度打分，取最优候选生成稳定新定位器。

打分规则（见 _score）：
- 描述与候选文本/aria-label 互为子串 → 强信号（置信度 ~0.85 起）。
- 否则按意图词元在最佳字段的重叠比例计分（封顶 ~0.8）。

DOM 解析与稳定定位器生成复用 agent/dom.py 公共工具。
TODO：属性权重可配置；候选过多时结合可见性/位置二次筛选。
"""

from __future__ import annotations

import re

from selfheal.agent.confidence import KEY_HEURISTIC, calibrate
from selfheal.agent.dom import (
    Element,
    build_stable_selector,
    interactive_candidates,
    parse_interactive_elements,  # score_selector（C4 静态交叉校验）专用
)
from selfheal.agent.strategies.base import RepairCandidate, RepairStrategy
from selfheal.collect.collector import Scene

# 词元：连续字母数字 或 连续中文（CJK）。
# 注意：与 llm/embedding.py 的 _TOKEN_RE（单中文字符）粒度不同是有意的——
# 意图词元重叠用连续片段更语义化；embedding 的 n-gram 需单字形成中文二元组。
# 调整任一处分词时需评估另一处（审查 m7：两处重复定义，勿漂移）。
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]+")
# 参与打分的字段（仅用于可读性，实际计分见 _score）
_FIELDS = ("data-testid", "id", "aria-label", "text", "name", "placeholder")

_CONTAIN_BASE = 0.85  # 描述与文本互为子串的基础置信度
_TOKEN_CAP = 0.80  # 纯词元匹配的置信度上限


def _tokenize(text: str | None) -> set[str]:
    """切成小写词元集合（英文按词、中文按连续片段）。"""
    if not text:
        return set()
    return {t.lower() for t in _TOKEN_RE.findall(text)}


def score_selector(
    dom_snapshot: str | None,
    selector: str,
    original_selector: str,
    description: str | None,
) -> float:
    """跨策略一致性校验（C4）：给一个已生成的稳定定位器算一次 L2 启发式分数（0~1）。

    供视觉策略在"VLM 选中候选"后交叉验证：若 L2 分数也很低，说明该候选与
    「原选择器 + 描述」的意图严重偏离，应降权/拒绝（防静默误修、置信度自报虚高）。
    反查不到该 selector → 返回 0.0（保守拒绝）。
    """
    if not dom_snapshot:
        return 0.0
    intent_tokens = _tokenize(original_selector) | _tokenize(description)
    best = 0.0
    for el in parse_interactive_elements(dom_snapshot):
        if build_stable_selector(el) == selector:
            best = max(best, _score(el, intent_tokens, description))
    return best


def _score(el: Element, intent_tokens: set[str], description: str | None) -> float:
    """计算候选元素与意图的相似度，归一化到 [0, 1]。"""
    # 1) 强信号：描述与候选文本/aria-label 互为子串
    contained = False
    if description:
        for value in (el.field("text"), el.attr("aria-label")):
            if value and (description in value or value in description):
                contained = True
                break
    # 2) 词元重叠：取所有字段中的最佳重叠比例
    ratio = 0.0
    if intent_tokens:
        for name in _FIELDS:
            tokens = _tokenize(el.field(name))
            if tokens:
                ratio = max(ratio, len(intent_tokens & tokens) / len(intent_tokens))
    if contained:
        return min(_CONTAIN_BASE + (0.10 if ratio > 0 else 0.0), 1.0)
    return min(ratio * _TOKEN_CAP, 1.0)


class HeuristicStrategy(RepairStrategy):
    name = "heuristic"

    def repair(self, scene: Scene, original_selector: str, description: str | None = None):
        if not scene.dom_snapshot:
            return None
        intent_tokens = _tokenize(original_selector) | _tokenize(description)
        best_el, best_score = None, 0.0
        # T8：候选来源优先 Playwright 原生解析（有 page 采集），静态 DOM 快照解析兜底
        for el in interactive_candidates(scene):
            score = _score(el, intent_tokens, description)
            if score > best_score:
                best_el, best_score = el, score
        if best_el is None or best_score <= 0:
            return None
        selector = build_stable_selector(best_el)
        if not selector:
            return None
        return RepairCandidate(
            selector=selector,
            # T5：统一置信度标尺出口（本策略恒等，契约见 agent/confidence.py）
            confidence=calibrate(KEY_HEURISTIC, best_score),
            strategy=self.name,
        )
