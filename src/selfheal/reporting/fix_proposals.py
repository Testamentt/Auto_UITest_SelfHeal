"""修复建议输出（Phase 5 A 语义建议 + D2 T15 修复写回人审清单 + T17 成本估算）。

- append_review_proposal：L3 语义命中但未达自动采纳阈值 → 追加人审清单（reports/review-queue.md）。
- write_fix_proposal：修复成功后输出「原→新」PR 化建议（Markdown 行 + JSON 文件，**不自动改库**，供人确认后合入）。
- estimate_cost：T17 按 LLM/VLM 调用次数估算费用（默认单价可覆盖）。

全部 best-effort：写失败仅记 warning，绝不阻塞自愈主流程（R4 不静默吞错）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REPORT_DIR = Path("reports")
REVIEW_QUEUE_PATH = REPORT_DIR / "review-queue.md"
FIX_PROPOSALS_DIR = REPORT_DIR / "fix-proposals"
FIX_PROPOSALS_MD = REPORT_DIR / "fix-proposals.md"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_review_proposal(
    *,
    original_selector: str,
    new_selector: str,
    confidence: float,
    page_url: str,
    strategy: str,
    reason: str,
) -> None:
    """L3：追加一条人审建议（sim 未达自动采纳阈值时调用）。best-effort。"""
    try:
        REVIEW_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = (
            f"- [{_now()}] {original_selector} → {new_selector} "
            f"(sim={confidence:.2f}, page={page_url}, strategy={strategy}, reason={reason})\n"
        )
        with REVIEW_QUEUE_PATH.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:  # noqa: BLE001 - 写建议失败不阻塞流水线
        logger.warning("人审建议写入失败", exc_info=True)


def write_fix_proposal(
    *,
    original_selector: str,
    new_selector: str,
    strategy: str,
    confidence: float,
    page_url: str,
    root_cause: str | None,
    verified: bool,
) -> None:
    """T15：修复成功后输出 PR 化建议（Markdown 行 + JSON 独立文件）。

    只生成建议、**不自动改库/合入**（record["applied"]=False），供人确认后再落地。
    """
    try:
        FIX_PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
        # m2：同一时刻取一次时间（文件名与记录内容一致）；
        # 毫秒精度防"同秒同 selector"文件名覆盖（修复前秒级精度会互相覆盖）。
        now = datetime.now(timezone.utc)
        stamp = now.isoformat(timespec="milliseconds").replace(":", "-").replace("+00:00", "Z")
        safe_name = (
            "".join(c if c.isalnum() or c in "._-" else "_" for c in original_selector)[:60] or "proposal"
        )
        record = {
            "created_at": now.isoformat(timespec="seconds"),
            "original_selector": original_selector,
            "new_selector": new_selector,
            "strategy": strategy,
            "confidence": confidence,
            "page_url": page_url,
            "root_cause": root_cause,
            "verified": verified,
            "applied": False,  # 人确认后才改库/合入，默认未应用
        }
        (FIX_PROPOSALS_DIR / f"{stamp}-{safe_name}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with FIX_PROPOSALS_MD.open("a", encoding="utf-8") as f:
            f.write(
                f"| {_now()} | {html_escape(original_selector)} | {html_escape(new_selector)} | "
                f"{strategy} | {confidence:.2f} | {'✅ 真自愈' if verified else '⚠️ flaky'} |\n"
            )
    except Exception:  # noqa: BLE001 - 写建议失败不阻塞流水线
        logger.warning("修复建议写入失败", exc_info=True)


def html_escape(text: str) -> str:
    """转义 Markdown 表格中的竖线/换行，避免破坏表格。"""
    return (text or "").replace("|", "\\|").replace("\n", " ")


# T17 成本估算：调用单价（元/次，粗略默认值，可按 provider 覆盖）
_DEFAULT_UNIT_COST = {"llm": 0.02, "vlm": 0.05}


def estimate_cost(llm_calls: int = 0, vlm_calls: int = 0, *, unit_cost: dict | None = None) -> dict:
    """T17：按调用次数估算费用（默认单价可覆盖）。纯函数、零副作用、可单测。"""
    costs = {**_DEFAULT_UNIT_COST, **(unit_cost or {})}
    llm_cost = llm_calls * float(costs.get("llm", 0.0))
    vlm_cost = vlm_calls * float(costs.get("vlm", 0.0))
    return {
        "llm_calls": llm_calls,
        "vlm_calls": vlm_calls,
        "llm_cost": round(llm_cost, 4),
        "vlm_cost": round(vlm_cost, 4),
        "total_cost": round(llm_cost + vlm_cost, 4),
    }
