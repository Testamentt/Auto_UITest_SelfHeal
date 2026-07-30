"""修复策略抽象基类。"""

from __future__ import annotations

import abc
from dataclasses import dataclass

from selfheal.collect.collector import Scene


@dataclass
class RepairCandidate:
    selector: str
    confidence: float
    strategy: str


class RepairStrategy(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def repair(
        self, scene: Scene, original_selector: str, description: str | None = None
    ) -> RepairCandidate | None:
        """基于现场产出一个修复候选；无法修复时返回 None。"""
