"""自愈定位器 —— 插件层代理（见 RULE.md 决策 D5 / D6）。

设计要点：
- **接口兼容代理**：HealingPage / HealingLocator 与 Playwright 的 Page / Locator 接口兼容，
  面向 Page 接口编写的 POM 代码，开 / 关自愈都能运行（你的第 1 点：POM 无缝切换）。
- **只拦截小集合动作 + 链式定位**：HEALABLE 动作包"失败→自愈→重试"；链式定位
  （locator()/first/nth/filter/get_by_* 等）递归包裹并记录链操作，自愈后重放整条链
  （你的第 2 点：链式调用同样具备自愈能力）。其余 API 原样透传。
- **可开关、零侵入**：enabled=False 时动作直通、不做任何修复，行为等同原生（你的第 2 点：插件/挂件）。
- **兜底（D6）**：AI 不确定时按 on_uncertain 处理——use_fallback（默认）/ pause（仅交互模式）/ fail。

Playwright 为运行期可选依赖：TimeoutError 导入做了降级，保证本模块可在无浏览器环境被单元测试导入。
"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from selfheal.agent.diagnose import FailureContext
from selfheal.agent.orchestrator import HealOutcome, SelfHealOrchestrator
from selfheal.config import HealingConfig, Settings
from selfheal.engine.popup_guard import PopupGuard
from selfheal.engine.smart_wait import wait_until_stable as _wait_until_stable
from selfheal.knowledge.base import KnowledgeBackend
from selfheal.knowledge.factory import build_knowledge_store
from selfheal.reporting.hooks import HealingReporter

if TYPE_CHECKING:  # 仅类型检查时导入
    from playwright.sync_api import Locator, Page

logger = logging.getLogger(__name__)

# 运行期可选依赖 playwright：未安装时降级，使纯逻辑单测可导入本模块
try:  # pragma: no cover - 取决于运行环境是否安装 playwright
    from playwright.sync_api import FrameLocator as _PWFrameLocator
    from playwright.sync_api import TimeoutError as _PWTimeoutError
except ImportError:  # pragma: no cover
    _PWFrameLocator = None
    _PWTimeoutError = None


class HealingFailedError(Exception):
    """自愈失败且无可用兜底时抛出，携带诊断报告。"""


def _is_timeout_error(exc: BaseException) -> bool:
    """判定是否为 Playwright 定位/等待超时（自愈的触发条件）。

    优先按 playwright TimeoutError 实例判断；类名粗判（"Timeout" in name）仅作
    无 playwright 环境下的兜底（m8 已知风险：自定义异常类名含 Timeout 会误触发自愈，
    影响面小：最多多跑一轮闭环，随后按结果正常处理）。
    """
    if _PWTimeoutError is not None and isinstance(exc, _PWTimeoutError):
        return True
    return "Timeout" in type(exc).__name__  # 兜底：无 playwright 时按类名粗判


def _interactive() -> bool:
    """是否处于可交互终端（pause 模式仅在此生效，CI 中恒为 False）。"""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


# --- 链式定位：自愈目标的两档语义 ---
# 引入新 selector 的链环（locator()/get_by_* 等）会成为"叶子修复目标"；
# 纯定位操作（first/last/nth/filter）沿用父目标，只进 chain_ops 用于描述与重放。
# frame_locator 刻意不在其中：跨 iframe 自愈当前不支持，保持原生透传。
CHAIN_SELECTOR_METHODS = frozenset(
    {
        "locator",
        "get_by_role",
        "get_by_text",
        "get_by_label",
        "get_by_placeholder",
        "get_by_alt_text",
        "get_by_title",
        "get_by_test_id",
    }
)
CHAIN_POSITIONAL_METHODS = frozenset({"nth", "filter"})
CHAIN_PROPERTIES = frozenset({"first", "last"})
CHAIN_METHODS = CHAIN_SELECTOR_METHODS | CHAIN_POSITIONAL_METHODS

# auto_description 的链提示只保留最近几层（防描述过长稀释 L2 词元 / L3 embedding）；
# 注意截断只影响描述，_replay_chain 重放始终全量（正确性依赖）。
CHAIN_HINT_LIMIT = 3

# 判定 Locator 形态的核心方法（区分真实/假 Locator 与 FrameLocator/标量）
_LOCATOR_API = ("locator", "nth", "click")


def _is_locator_like(result: Any) -> bool:
    """链操作结果是否是可自愈的 Locator 形态（排除 FrameLocator 与标量）。"""
    if result is None or isinstance(result, (str, bytes, int, float, bool, list, dict, tuple)):
        return False
    if _PWFrameLocator is not None and isinstance(result, _PWFrameLocator):
        return False
    return all(hasattr(result, m) for m in _LOCATOR_API)


def _child_selector(name: str, args: tuple, kwargs: dict) -> str:
    """链环引入的叶子修复目标字符串（二次自愈修叶子时交给编排器做意图匹配）。

    locator(x) 直接用子 selector；get_by_* 转成可被启发式词元化的描述串。
    """
    if name == "locator":
        return str(args[0]) if args else ""
    if name == "get_by_role":
        role = str(args[0]) if args else ""
        label = str(kwargs.get("name", ""))
        return f"[role={role}]{(' ' + label) if label else ''}".strip()
    if name == "get_by_text":
        return f"text={args[0] if args else ''}"
    if name == "get_by_label":
        return f"[aria-label={args[0] if args else ''}]"
    if name == "get_by_placeholder":
        return f"[placeholder={args[0] if args else ''}]"
    if name == "get_by_alt_text":
        return f"[alt={args[0] if args else ''}]"
    if name == "get_by_title":
        return f"[title={args[0] if args else ''}]"
    if name == "get_by_test_id":
        return f"[data-testid={args[0] if args else ''}]"
    return ""


class HealingLocator:
    """带自愈能力的 Locator 代理。

    通过 __getattr__ 透传原生 Locator 的全部属性；仅当开关开启时拦截两类 API：
    - HEALABLE 动作（click/fill 等）→ 包一层"失败→自愈→重试"；
    - 链式定位（locator()/first/nth/filter/get_by_* 等）→ 递归包裹为 HealingLocator，
      记录链操作（chain_ops），自愈后可在修复的根定位器上重放整条链。

    自愈目标两档（覆盖不同断裂点）：
    - 第一次自愈修根（self._selector）+ 重放链，覆盖"根选择器断裂"；
    - 重试仍失败则第二次自愈直接修叶子（self._leaf_selector），覆盖"中段/叶子断裂"。
    """

    # 需要纳入自愈的动作方法（定位失败最可能发生处）
    HEALABLE = (
        "click",
        "dblclick",
        "fill",
        "check",
        "uncheck",
        "press",
        "select_option",
        "hover",
        "tap",
        "set_input_files",
    )

    def __init__(
        self,
        locator: Locator,
        page: Page,
        selector: str,
        cfg: HealingConfig,
        orchestrator: SelfHealOrchestrator,
        *,
        fallback: str | None = None,
        description: str | None = None,
        enabled: bool = True,
        popup_guard: PopupGuard | None = None,
        chain_ops: tuple[tuple[str, tuple, dict, bool], ...] = (),
        leaf_selector: str | None = None,
    ):
        self._locator = locator
        self._page = page
        self._selector = selector  # 根选择器：第一次自愈修根 + 链重放用
        self._cfg = cfg
        self._orch = orchestrator
        self._fallback = fallback
        self._description = description
        self._enabled = enabled
        self._popup_guard = popup_guard
        # 链操作记录：不可变元组，_chain 派生时拼接（天然防共享链污染）
        self._chain_ops = chain_ops
        # 叶子修复目标：最后一次引入新 selector 的链环的 selector；无则等于根选择器
        self._leaf_selector = leaf_selector or selector
        # B1：最近一次自愈 outcome，供 _healing_action 重试成功后 commit_pending
        self._last_heal_outcome: HealOutcome | None = None

    def __getattr__(self, name: str) -> Any:
        # 仅对实例上不存在的属性触发；__init__ 设置的属性不走这里
        if self._enabled and name in CHAIN_PROPERTIES:
            return self._chain(name, (), {}, True)
        if self._enabled and name in CHAIN_METHODS:
            return lambda *a, **kw: self._chain(name, a, kw)
        attr = getattr(self._locator, name)
        if self._enabled and name in self.HEALABLE and callable(attr):
            return self._healing_action(name, attr)
        return attr

    def _chain(self, name: str, args: tuple, kwargs: dict, is_property: bool = False) -> Any:
        """执行链操作并递归包裹 Locator 返回值；FrameLocator/标量透传（跨 iframe 自愈不支持）。"""
        if is_property:
            result = getattr(self._locator, name)
        else:
            result = getattr(self._locator, name)(*args, **kwargs)
        if not _is_locator_like(result):
            return result
        new_op = (name, args, kwargs, is_property)
        if name in CHAIN_SELECTOR_METHODS:
            child_leaf = _child_selector(name, args, kwargs) or self._leaf_selector
        else:
            child_leaf = self._leaf_selector  # 纯定位操作沿用父叶子目标
        return HealingLocator(
            result,
            self._page,
            self._selector,  # 根选择器不变：第一次自愈修根 + 重放链
            self._cfg,
            self._orch,
            fallback=self._fallback,
            description=self._description,
            enabled=self._enabled,
            popup_guard=self._popup_guard,
            chain_ops=self._chain_ops + (new_op,),
            leaf_selector=child_leaf,
        )

    def _replay_chain(self, root_locator: Any, action_name: str, args: tuple, kwargs: dict) -> Any:
        """在修复后的根定位器上重放整条链操作 + 叶子动作（property 走 getattr、method 走调用）。"""
        target = root_locator
        for op_name, op_args, op_kwargs, is_property in self._chain_ops:
            if is_property:
                target = getattr(target, op_name)
            else:
                target = getattr(target, op_name)(*op_args, **op_kwargs)
        return getattr(target, action_name)(*args, **kwargs)

    def _chain_hint(self) -> str:
        """把链操作格式化为自然语言（仅保留最近 CHAIN_HINT_LIMIT 层，防描述稀释意图）。

        注意：截断只影响描述文本，_replay_chain 重放始终全量（正确性依赖）。
        """
        ops = list(self._chain_ops)
        if not ops:
            return ""
        truncated = len(ops) > CHAIN_HINT_LIMIT
        recent = ops[-CHAIN_HINT_LIMIT:]
        text = "、".join(self._format_op(*op) for op in recent)
        if truncated:
            text = f"…{text}"
        return f"（经过 {text} 后的元素）"

    @staticmethod
    def _format_op(name: str, args: tuple, kwargs: dict, is_property: bool) -> str:
        if is_property:
            return name
        arg_str = ", ".join(repr(a) for a in args)
        kw_str = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
        body = arg_str
        if kw_str:
            body = f"{body}{', ' if body else ''}{kw_str}"
        return f"{name}({body})"

    def _build_auto_description(self, selector: str) -> str:
        """无用户描述时自动生成定位意图描述（根/叶选择器 + 页面 + 链操作提示）。"""
        url = ""
        with contextlib.suppress(Exception):
            url = self._page.url
        return f"元素{selector!r}，位于{url or '当前页面'}{self._chain_hint()}"

    def _healing_action(self, name: str, action: Callable) -> Callable:
        """把原生动作包成：前置可忽略等待 → 失败（超时）→ 自愈 → 用新定位器重试。"""

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            self._wait_before_action()  # T6：动作前可选的短稳等待（best-effort，失败不阻塞）
            try:
                return action(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 需甄别超时后决定自愈或上抛
                if not _is_timeout_error(exc):
                    raise
                # 先尝试清除弹窗（"被遮挡"类失败的高频根因）；清掉则直接重试原动作
                if self._popup_guard is not None and self._popup_guard.dismiss_if_present():
                    try:
                        return action(*args, **kwargs)
                    except Exception:  # noqa: BLE001 - 弹窗非根因，继续走自愈闭环
                        pass
                # 第一次自愈：修根（self._selector）+ 重放整条链，覆盖"根选择器断裂"
                relocated = self._heal_and_resolve(exc)
                try:
                    result = self._replay_chain(relocated, name, args, kwargs)
                    self._commit_success()  # B1：重试成功 → 沉淀暂存的修复
                    return result
                except Exception as retry_exc:  # noqa: BLE001 - 自愈后的选择器仍失败
                    if not _is_timeout_error(retry_exc):
                        raise
                    # 第二次自愈（T4）：跳过知识缓存，直接修叶子（链意图拼入描述），
                    # 覆盖"中段/叶子选择器断裂"；重试用修复后的叶子、不再重放链；
                    # 上限一次、结果为裸调用不包裹，故不会死循环。
                    relocated2 = self._heal_and_resolve(retry_exc, use_knowledge=False, leaf=True)
                    result = getattr(relocated2, name)(*args, **kwargs)
                    self._commit_success()  # B1：二次重试成功 → 沉淀暂存的修复
                    return result

        wrapped.__name__ = name
        wrapped.__doc__ = f"自愈包装动作 {name}：失败（超时）→ 自愈 → 重试。"
        return wrapped

    def _wait_before_action(self) -> None:
        """T6：动作前可选的智能等待（healing.action_wait.enabled）。

        对目标 locator 做一次短稳等待（复用 engine/smart_wait.py 的 wait_until_stable），
        缓解"元素仍在加载/抖动就执行动作"导致的误报超时。**best-effort**：
        等待失败仅记 debug、不阻塞动作——等待是增强而非正确性前提，
        避免把"等待"变成新的超时源（元素不存在时动作照常执行 → 走既有自愈/超时语义）。
        """
        if not self._enabled:
            return
        aw = self._cfg.action_wait
        if not aw.enabled:
            return
        try:
            _wait_until_stable(self._locator, timeout_ms=aw.timeout_ms, stable_ms=aw.stable_ms)
        except Exception:  # noqa: BLE001 - 等待失败不阻塞动作（见 docstring）
            logger.debug("动作前置智能等待跳过（元素未达稳定，不阻塞动作）", exc_info=True)

    def _heal_and_resolve(
        self, exc: BaseException | None = None, use_knowledge: bool = True, leaf: bool = False
    ) -> Locator:
        """运行闭环并决定用哪个定位器重试（含 D6 兜底）。

        leaf=True 时修叶子目标（self._leaf_selector），否则修根（self._selector）。
        use_knowledge=False 用于二次自愈（跳过缓存）。
        顶层兜底（H1）：闭环内部异常（采集/知识读取/策略等）不替换原始定位失败异常，
        而是转为"自愈内部失败"outcome 走 D6 兜底（fallback/pause/fail），保留触发自愈的语义。
        """
        selector = self._leaf_selector if leaf else self._selector
        desc = self._description
        if not desc:
            desc = self._build_auto_description(selector)
        elif leaf and self._chain_ops:
            desc = f"{desc}{self._chain_hint()}"
        failure = FailureContext(
            failure_type=type(exc).__name__ if exc is not None else None,
            message=str(exc) if exc is not None else None,
        )
        try:
            outcome = self._orch.run(selector, desc, failure=failure, use_knowledge=use_knowledge)
        except Exception as run_exc:  # noqa: BLE001 - 闭环内部异常兜底，不替换原始异常
            logger.warning("自愈闭环内部异常，按不确定兜底处理", exc_info=True)
            outcome = HealOutcome(
                success=False, root_cause=f"heal_internal:{type(run_exc).__name__}"
            )
        self._last_heal_outcome = (
            outcome  # B1：供重试成功后 commit_pending（无 attempt_id 则 no-op）
        )
        if outcome.success and outcome.new_selector:
            return self._page.locator(outcome.new_selector)
        return self._resolve_uncertain(outcome)

    def _commit_success(self) -> None:
        """重试验证成功后触发知识沉淀（B1）：把 run() 暂存的修复落库 + 审计。"""
        outcome = self._last_heal_outcome
        if outcome is not None and outcome.attempt_id and self._orch is not None:
            self._orch.commit_pending(outcome.attempt_id)

    def _resolve_uncertain(self, outcome: HealOutcome) -> Locator:
        """AI 不确定时的兜底分支（决策 D6）。"""
        mode = self._cfg.on_uncertain
        if mode == "use_fallback" and self._fallback:
            return self._page.locator(self._fallback)
        if mode == "pause" and _interactive():
            input(f"[自愈] 定位器 {self._selector!r} 失败且无法自动修复，请人工处理后回车继续…")
            return self._page.locator(self._selector)
        raise HealingFailedError(self._failure_report(outcome))

    def _failure_report(self, outcome: HealOutcome) -> str:
        proposed = (
            f", 建议定位器(dry-run)={outcome.proposed_selector!r}"
            if outcome.proposed_selector
            else ""
        )
        return (
            f"自愈失败：原定位器={self._selector!r}, 描述={self._description!r}, "
            f"根因={outcome.root_cause!r}, 最高置信度={outcome.confidence}, "
            f"备用定位器={self._fallback!r}, on_uncertain={self._cfg.on_uncertain!r}{proposed}"
        )

    def wait_until_stable(
        self, timeout_ms: int = 10000, stable_ms: int = 300, poll_ms: int = 100
    ) -> None:
        """智能等待：等元素可见且位置/尺寸稳定（可选增强，POM 显式调用）。"""
        _wait_until_stable(self._locator, timeout_ms, stable_ms, poll_ms)


class HealingPage:
    """带自愈能力的 Page 代理（插件层）。

    仅扩展 locator()（增加 description / fallback 两个自愈增强参数），其余 API 全部透传。
    POM 面向 Page 接口编写，开关自愈都能跑；enabled=False 时行为与原生 Page 一致。
    """

    def __init__(
        self,
        page: Page,
        settings: Settings,
        *,
        knowledge: KnowledgeBackend | None = None,
        reporter: HealingReporter | None = None,
        enabled_override: bool | None = None,
    ):
        self._page = page
        self._settings = settings
        self._enabled = settings.healing.enabled if enabled_override is None else enabled_override
        self._reporter = reporter or HealingReporter()
        if self._enabled:
            # 仅开启时构建知识库 / 闭环 / 弹窗处理（关闭时零开销、等同原生 Page，不产生文件副作用）
            self._knowledge = knowledge or build_knowledge_store(settings)
            self._orchestrator = SelfHealOrchestrator(
                page, settings, self._knowledge, self._reporter
            )
            self._popup_guard = PopupGuard(page, self._knowledge)
        else:
            self._knowledge = knowledge
            self._orchestrator = None
            self._popup_guard = None

    @property
    def healing_enabled(self) -> bool:
        return self._enabled

    @property
    def reporter(self) -> HealingReporter:
        return self._reporter

    def close(self) -> None:
        """释放自愈资源（orchestrator 自建的知识库连接 / LLM 客户端，审查 M3）。

        注入的 knowledge 由注入方负责；关闭后本实例不可再用于自愈。
        """
        if self._orchestrator is not None:
            self._orchestrator.close()

    def locator(
        self,
        selector: str,
        *,
        description: str | None = None,
        fallback: str | None = None,
        **kwargs: Any,
    ) -> HealingLocator:
        """返回自愈定位器。description / fallback 为自愈增强参数（关闭时被接受但忽略）。"""
        raw = self._page.locator(selector, **kwargs)
        return HealingLocator(
            raw,
            self._page,
            selector,
            self._settings.healing,
            self._orchestrator,
            fallback=fallback,
            description=description,
            enabled=self._enabled,
            popup_guard=self._popup_guard,
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._page, name)
