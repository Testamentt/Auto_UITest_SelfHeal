"""智能诊断。

借助 LLM 结合现场（截图 / DOM）判定失败根因：
元素不存在 / 不可交互 / 超时 / 弹窗遮挡 等。
TODO: 通过 llm.get_llm 调用模型，结构化输出诊断结果。
"""

from __future__ import annotations

from selfheal.collect.collector import Scene


class Diagnoser:
    def diagnose(self, scene: Scene, selector: str) -> str:
        """返回根因标签。当前为占位实现。"""
        # TODO: 构造 prompt（含 DOM 片段与 selector），经 llm 抽象层获取判定。
        return "unknown"
