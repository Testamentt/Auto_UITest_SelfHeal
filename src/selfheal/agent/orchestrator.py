"""自愈闭环编排器 —— 本项目最核心的链路。

流程：现场采集 →（知识库优先）→ 智能诊断 → 多策略修复 → 验证与沉淀 → 报告审计。
Playwright 仅作类型依赖（TYPE_CHECKING），核心逻辑可在无浏览器环境下被单元测试覆盖。

A1 重构（T12）后本类降为 **Router + 组合根**：run() 只做三件事——
ContextAssembler 组装上下文 → FixGenerator 生成提案 → PersistenceHandler 阈值路由。
业务逻辑已下沉到 `agent/context.py` / `agent/fix_generator.py` / `agent/persistence.py`，
本类保留薄委托器以兼容既有内部方法调用与测试。
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from selfheal.agent.context import (
    ContextAssembler,
    HealingContext,
    HealOutcome,
    selector_exists,
)
from selfheal.agent.diagnose import Diagnoser, FailureContext
from selfheal.agent.diagnose_llm import LLMDiagnoser
from selfheal.agent.fix_generator import FixGenerator, counting_proxy
from selfheal.agent.persistence import PersistenceHandler
from selfheal.agent.strategies import HeuristicStrategy, SemanticStrategy, VisualStrategy
from selfheal.agent.strategies.base import RepairStrategy
from selfheal.config import Settings
from selfheal.knowledge.base import KnowledgeBackend
from selfheal.knowledge.factory import build_knowledge_store
from selfheal.llm import get_embedding_for_settings
from selfheal.llm.base import LLMClient, VisionClient
from selfheal.llm.factory import get_llm_for_settings, get_vision_for_settings
from selfheal.reporting.hooks import HealingRecord, HealingReporter

if TYPE_CHECKING:  # 仅类型检查时导入，避免运行期强依赖 playwright
    from playwright.sync_api import Page

logger = logging.getLogger(__name__)


def _safe_close(obj) -> None:
    """best-effort 关闭带 close() 的对象（无 close / 关闭失败均静默，不掩盖业务）。"""
    close = getattr(obj, "close", None)
    if close is not None:
        with contextlib.suppress(Exception):
            close()


# 策略注册表：名字 → 策略类。新增策略在此登记即可被调度（注入 FixGenerator，测试 monkeypatch 仍生效）。
_STRATEGY_REGISTRY: dict[str, type[RepairStrategy]] = {
    "heuristic": HeuristicStrategy,
    "semantic": SemanticStrategy,
    "visual": VisualStrategy,
}


class SelfHealOrchestrator:
    """编排「感知-诊断-决策-修复」闭环（A1 重构后降为 Router + 组合根）。

    knowledge / reporter 可注入（默认自建），便于测试替换与跨用例复用知识库。
    """

    def __init__(
        self,
        page: Page | None,
        settings: Settings,
        knowledge: KnowledgeBackend | None = None,
        reporter: HealingReporter | None = None,
        llm_client: LLMClient | None = None,
        vision_client: VisionClient | None = None,
    ):
        self._page = page
        self._settings = settings
        self._knowledge = knowledge or build_knowledge_store(settings)
        self._owns_knowledge = knowledge is None  # 注入的资源由注入方负责关闭
        self._reporter = reporter or HealingReporter()
        # LLM 可用才构建；缺省时按配置判定，不可用则 None（降级回规则式 / 跳过语义策略）
        self._llm_client = llm_client if llm_client is not None else get_llm_for_settings(settings)
        self._owns_llm = llm_client is None
        # VLM 可用才构建；不可用则 None（视觉策略返回 None 被跳过）
        self._vision_client = (
            vision_client if vision_client is not None else get_vision_for_settings(settings)
        )
        self._owns_vision = vision_client is None
        # Embedding 可用才构建；不可用则 None（L1/L3 语义检索被跳过，零回归）
        self._embedding = get_embedding_for_settings(settings)
        # 规则式诊断：零成本恒跑，为成功路径提供根因归因（不触发 LLM，省 Token）
        rule_diagnoser = Diagnoser()
        # LLM 诊断：仅失败/低置信路径触发；client 包计数代理（B2：诊断调用计入成本统计）
        llm_diagnoser = (
            LLMDiagnoser(counting_proxy(self._llm_client, "llm_calls", self._reporter.stats))
            if self._llm_client
            else None
        )
        # 三个协作者（A1）：上下文组装 / 修复提案 / 持久化审计
        self._assembler = ContextAssembler(page, settings)
        self._fix_gen = FixGenerator(
            settings,
            self._knowledge,
            self._reporter,
            rule_diagnoser,
            llm_diagnoser,
            self._llm_client,
            self._vision_client,
            self._embedding,
            page,
            _STRATEGY_REGISTRY,
        )
        self._persister = PersistenceHandler(
            settings, self._knowledge, self._reporter, self._embedding, page
        )

    def run(
        self,
        original_selector: str,
        description: str | None = None,
        failure: FailureContext | None = None,
        use_knowledge: bool = True,
    ) -> HealOutcome:
        """执行一次完整自愈闭环（Router：组装 → 生成 → 路由）。"""
        context = self._assembler.assemble(original_selector, description)
        # T13：高风险页豁免——URL 命中 exclude_url_patterns 则不触发自愈（只报告，不动作）。
        # 豁免事件也记审计（m4）："只报告"的落点，供看板/审计追溯。
        if context.excluded:
            self._reporter.record(
                HealingRecord(
                    original_selector=original_selector,
                    new_selector=None,
                    strategy=None,
                    confidence=0.0,
                    root_cause="high_risk_page_excluded",
                    success=False,
                )
            )
            return HealOutcome(success=False, root_cause="high_risk_page_excluded")
        proposal = self._fix_gen.generate(context, failure, use_knowledge)
        return self._persister.resolve(context, proposal)

    def commit_pending(self, attempt_id: str) -> None:
        """B1：验证成功后沉淀知识 + 审计（委托 PersistenceHandler；幂等防重复提交）。"""
        self._persister.commit_pending(attempt_id)

    def close(self) -> None:
        """释放本实例**自建**的资源（知识库连接 / LLM / VLM 客户端，审查 M3）。

        注入的 knowledge / llm_client / vision_client 由注入方负责关闭（owns 标记）。
        释放失败不掩盖业务（best-effort）；幂等（重复 close 安全）。
        """
        if self._owns_knowledge:
            _safe_close(self._knowledge)
        if self._owns_llm:
            _safe_close(self._llm_client)
        if self._owns_vision:
            _safe_close(self._vision_client)

    def __enter__(self) -> SelfHealOrchestrator:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # --- 薄委托器：兼容既有内部方法调用与测试（逻辑已下沉协作者） ---

    def _counting_client(self, client, key: str):
        """T17：计数代理（委托 FixGenerator，供诊断/测试复用）。"""
        return self._fix_gen._counting_client(client, key)

    def _selector_exists(self, selector: str) -> bool:
        """校验选择器能否在当前页面定位到元素（缓存验证 / T16 verified 共用）。"""
        return selector_exists(self._page, selector)

    def _lookup_knowledge(
        self,
        scene,
        selector: str,
        dom_fingerprint: str | None = None,
        page_fingerprint: str = "",
        element_context=None,
    ):
        """知识库优先查询（委托 FixGenerator；保留旧签名兼容测试）。"""
        return self._fix_gen.lookup_knowledge(
            HealingContext(
                scene=scene,
                original_selector=selector,
                description=None,
                dom_fingerprint=dom_fingerprint,
                page_fingerprint=page_fingerprint,
                element_context=element_context,
            )
        )

    def _best_candidate(self, scene, selector: str, description: str | None):
        """按 strategy_order 取最优候选（委托 FixGenerator；保留旧签名兼容测试）。"""
        return self._fix_gen.best_candidate(
            HealingContext(
                scene=scene,
                original_selector=selector,
                description=description,
                dom_fingerprint=None,
                page_fingerprint="",
                element_context=None,
            )
        )

    def _persist(
        self,
        scene,
        original_selector: str,
        outcome: HealOutcome,
        dom_fingerprint: str | None = None,
        page_fingerprint: str = "",
        element_context=None,
    ) -> None:
        """修复成功后沉淀 + 审计（委托 PersistenceHandler；保留旧签名兼容测试）。"""
        self._persister._persist(
            HealingContext(
                scene=scene,
                original_selector=original_selector,
                description=None,
                dom_fingerprint=dom_fingerprint,
                page_fingerprint=page_fingerprint,
                element_context=element_context,
            ),
            outcome,
        )
