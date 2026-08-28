"""置信度归一化层（T5：跨策略置信度标定统一 / 按策略阈值）。

背景：各策略产出的 confidence 语义不同，却曾共用全局阈值裁决（不可比的数被同一把尺子量）：

| 策略键        | 原始置信度来源                                        | 标尺语义                          |
| ------------- | ----------------------------------------------------- | --------------------------------- |
| heuristic     | 规则打分（强子串 0.85 起 / 词元封顶 0.8）              | 设计上即可信的"采纳概率"标尺      |
| semantic_l3   | 知识库向量余弦相似度（另有内置采纳规则 verified/fresh）| 相似度，非概率（有自身门槛）      |
| semantic_llm  | LLM 自报值                                            | 系统性偏高、跨模型不可比          |
| visual        | VLM 自报 × (0.4 + 0.6 × L2 融合分)（C4 已交叉降权）     | 已融合、接近采纳概率              |

设计方案（方案 C，经计划确认）：
- **可插拔校准注册表**：`CALIBRATORS[strategy_key] = fn(raw, shrink_self_reported)`，把原始输出
  映射到统一的"采纳概率"标尺；新增/替换校准只需登记，不改各路由点。
- **默认恒等（identity）**：未开启收缩时所有策略原样通过 → 缺省行为与 T4 完成后完全一致、
  零回归；收缩一律需显式开关（`healing.shrink_self_reported`），待真实模型校准数据沉淀后再启用调参。
- **收缩语义**：对自报型（semantic_llm）做保守收缩（raw²，经验性、单调、保 0/1 端点），
  缓解"LLM 自报虚高 → 虚高置信被直接采纳"；属经验映射，随真实数据标定后可替换（roadmap 待解决问题）。

所有策略必须在**产出 RepairCandidate 前**调用 calibrate()，使报告 / 知识库 / 裁决共用同一标尺。
"""

from __future__ import annotations

from collections.abc import Callable

# 各策略的置信度标尺键（策略产出点使用，与 _STRATEGY_REGISTRY 的策略名解耦——同一策略名
# 可含多个段，如 semantic 的 L3 相似度段与 LLM 自报段标尺不同，需分别登记）。
KEY_HEURISTIC = "heuristic"
KEY_SEMANTIC_L3 = "semantic_l3"
KEY_SEMANTIC_LLM = "semantic_llm"
KEY_VISUAL = "visual"

# 自报型策略键集合：LLM/VLM 自报置信度系统性偏高，收缩仅作用于这些键（L3 相似度非自报，不收缩）
_SELF_REPORTED = frozenset({KEY_SEMANTIC_LLM})


def _identity(raw: float, shrink_self_reported: bool = False) -> float:
    """恒等映射（默认）：原样返回，保证缺省行为与 T4 后完全一致。"""
    return round(raw, 3)


def _shrink(raw: float, shrink_self_reported: bool = False) -> float:
    """自报置信度保守收缩：raw²（幂收缩，单调、保 0/1 端点）。

    例：0.9→0.81、0.8→0.64、0.75→0.5625——高自报被实质压缩，低置信更快跌破接受阈值。
    经验性映射：待真实模型校准数据（roadmap 待解决问题）沉淀后应替换为数据标定函数。
    """
    if not shrink_self_reported:
        return round(raw, 3)
    return round(raw * raw, 3)


# 校准注册表：strategy_key → (raw, shrink_self_reported) -> effective confidence。
# 可插拔扩展点：新策略 / 数据标定后在此登记或替换，路由点无需改动。
CALIBRATORS: dict[str, Callable[[float, bool], float]] = {
    KEY_HEURISTIC: _identity,  # 规则打分，设计上即可信（标尺契约见模块 docstring）
    KEY_SEMANTIC_L3: _identity,  # 余弦相似度，相似度标尺；采纳另有 semantic._accept 内置门槛
    KEY_SEMANTIC_LLM: _shrink,  # LLM 自报：可选保守收缩（shrink_self_reported 开启时生效）
    KEY_VISUAL: _identity,  # 已含 C4 L2 融合降权，视为采纳概率标尺（不二次收缩防双重惩罚）
}


def calibrate(strategy_key: str, raw: float, *, shrink_self_reported: bool = False) -> float:
    """把策略原始置信度映射到统一采纳标尺（T5 归一化出口）。

    所有策略产出 RepairCandidate 前必须调用：报告 / 知识库 / 裁决共用 effective 值。
    未知策略键按恒等处理并保持值域（防御：新策略忘登记不改变行为，而非抛异常）。
    raw 必须为 [0, 1] 内数值（调用方策略已做护栏校验）；越界按原值返回并保留 round 精度。
    """
    fn = CALIBRATORS.get(strategy_key, _identity)
    effective = fn(raw, shrink_self_reported)
    # 防御值域漂移：收缩/校准实现不得把有效置信度推出 [0, 1]（如出现则钳制，不静默吞掉）
    if not (0.0 <= effective <= 1.0):
        effective = min(max(effective, 0.0), 1.0)
    return round(effective, 3)
