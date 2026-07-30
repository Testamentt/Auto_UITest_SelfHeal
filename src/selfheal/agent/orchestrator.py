"""自愈闭环编排器 —— 本项目最核心的链路。

流程：现场采集 → (知识库优先) → 智能诊断 → 多策略修复 → 验证与沉淀 → 报告审计。
TODO: 串联 collect / knowledge / diagnose / strategies / reporting。
"""

from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import Page

from selfheal.collect.collector import Scene, SceneCollector
from selfheal.config import Settings


@dataclass
class HealOutcome:
    success: bool
    new_selector: str | None = None
    confidence: float = 0.0
    strategy: str | None = None
    root_cause: str | None = None


class SelfHealOrchestrator:
    def __init__(self, page: Page, settings: Settings):
        self._page = page
        self._settings = settings
        self._collector = SceneCollector(page)

    def run(self, original_selector: str, description: str | None = None) -> HealOutcome:
        scene = self._collector.capture()

        # 1) 知识库优先：命中修复案例 / 弹窗特征则直接复用
        if self._settings.healing.knowledge_first:
            cached = self._lookup_knowledge(scene, original_selector)
            if cached:
                return cached

        # 2) 智能诊断根因
        root_cause = self._diagnose(scene)

        # 3) 按配置顺序尝试多策略修复
        for name in self._settings.healing.strategy_order:
            outcome = self._try_strategy(name, scene, original_selector, description)
            if outcome and outcome.success:
                outcome.root_cause = root_cause
                self._persist(scene, original_selector, outcome)  # 4) 沉淀
                return outcome

        return HealOutcome(success=False, root_cause=root_cause)

    # --- 以下为桩，待实现 ---
    def _lookup_knowledge(self, scene: Scene, selector: str) -> HealOutcome | None:
        # TODO: 查 knowledge.store
        return None

    def _diagnose(self, scene: Scene) -> str | None:
        # TODO: 调 agent.diagnose.Diagnoser
        return None

    def _try_strategy(self, name, scene, selector, description) -> HealOutcome | None:
        # TODO: 从 strategies 注册表取策略执行，过滤置信度阈值
        return None

    def _persist(self, scene: Scene, original_selector: str, outcome: HealOutcome) -> None:
        # TODO: 写入知识库 + reporting 审计
        ...
