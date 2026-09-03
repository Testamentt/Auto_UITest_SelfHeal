"""自愈回归通知（T19）：把回归结论 + 自愈摘要推送到团队 webhook。

设计：
- 标准库零第三方依赖（urllib/json/argparse），CI 直接可跑；
- payload 组装为**纯函数**（build_summary / build_payload），便于单测快照；
- send 为 best-effort：网络失败只记 warning 返回 False，不抛、不阻塞 CI；
- WEBHOOK_URL 未配置 → CLI 打印 skip 并以退出码 0 结束（不炸流水线）；
- 触发范围由 CI 侧 job 条件控制（当前：仅 main 失败告警；定时回归启用后
  成功发摘要 + 失败发告警，见 .github/workflows/ci.yml 注释）。

用法（CI 步骤）：
    python scripts/notify.py --provider dingtalk --summary-file reports/healing-records.json \
        --run-url <actions-url> --conclusion failure --failed-jobs "unit, e2e"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("selfheal.notify")

# 通知 provider 白名单（payload 格式各异，见 build_payload）
PROVIDERS = ("dingtalk", "wecom", "slack", "generic")


def build_summary(
    records: list[dict[str, Any]],
    cost: dict[str, Any] | None = None,
    *,
    conclusion: str = "success",
    run_url: str = "",
    failed_jobs: str = "",
) -> dict[str, Any]:
    """从自愈记录组装通知摘要（纯函数，供 payload 渲染）。

    records 为 HealingRecord 的 asdict 列表（healing-records.json）；
    统计口径与 metrics 一致：成功率、verified（真自愈）率、策略分布。
    空记录（本轮无自愈）合法，total=0。
    """
    total = len(records)
    success = sum(1 for r in records if r.get("success"))
    verified = sum(1 for r in records if r.get("verified"))
    # 评审 m3（2026-09-03）：strategy=None（如 T13 豁免记录）不计入分布——与 metrics.compute_metrics
    # 的 `if r.strategy:` 口径对齐，避免通知里出现 "None" 键。
    strategies = dict(Counter(r["strategy"] for r in records if r.get("strategy")))
    return {
        "conclusion": conclusion,
        "run_url": run_url,
        "failed_jobs": [j.strip() for j in failed_jobs.split(",") if j.strip()],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "healing_total": total,
        "healing_success": success,
        "healing_success_rate": round(success / total, 3) if total else 0.0,
        "healing_verified": verified,
        "healing_strategy_dist": strategies,
        "cost": cost or {},
    }


def _summary_lines(s: dict[str, Any]) -> list[str]:
    """摘要 → 人类可读行列表（各 provider 共用文案，markdown 明文）。"""
    lines = [
        f"自愈摘要：共 {s['healing_total']} 次，成功率 {s['healing_success_rate']:.0%}，"
        f"真自愈 {s['healing_verified']} 次",
        f"策略分布：{s['healing_strategy_dist'] or '—'}",
    ]
    cost = s.get("cost") or {}
    if cost:
        lines.append(
            f"模型成本：LLM {cost.get('llm_calls', 0)} 次 / VLM {cost.get('vlm_calls', 0)} 次，"
            f"约 ¥{cost.get('total_cost', 0.0):.4f}"
        )
    if s.get("failed_jobs"):
        lines.append(f"失败 job：{', '.join(s['failed_jobs'])}")
    if s.get("run_url"):
        lines.append(f"运行详情：{s['run_url']}")
    return lines


def build_payload(provider: str, summary: dict[str, Any]) -> dict[str, Any]:
    """按 provider 组装 webhook 消息体（纯函数）。

    - dingtalk：{msgtype: markdown, markdown: {text}}（钉钉机器人）
    - wecom：{msgtype: markdown, markdown: {content}}（企业微信机器人）
    - slack：{text}（Incoming Webhook）
    - generic：原样摘要（自建服务 / 兼容其他平台自行适配）
    失败告警标题带 ⛔，成功摘要带 ✅。
    """
    icon = "⛔" if summary.get("conclusion") == "failure" else "✅"
    title = (
        f"{icon} AutoAiSelfHeal 回归{'告警' if summary.get('conclusion') == 'failure' else '摘要'}"
    )
    body = "\n".join([f"**{title}**", *_summary_lines(summary)])
    if provider == "dingtalk":
        return {"msgtype": "markdown", "markdown": {"title": title, "text": body}}
    if provider == "wecom":
        return {"msgtype": "markdown", "markdown": {"content": body}}
    if provider == "slack":
        return {"text": body}
    if provider == "generic":
        return {"title": title, **summary}
    raise ValueError(f"未知 provider: {provider}（可选: {', '.join(PROVIDERS)}）")


def send(url: str, payload: dict[str, Any], timeout_s: float = 10.0) -> bool:
    """POST JSON 到 webhook；2xx 视为成功。best-effort：失败记 warning 返回 False。"""
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s):
            return True
    except Exception:  # noqa: BLE001 - 通知失败不影响 CI 结果
        logger.warning("webhook 推送失败（best-effort 忽略）", exc_info=True)
        return False


def main(argv: list[str] | None = None) -> int:
    # Windows GBK 控制台无法编码 payload 里的 emoji（⛔/✅）：stdout 按 UTF-8 容错输出
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="自愈回归通知推送（T19）")
    parser.add_argument("--provider", default="dingtalk", choices=PROVIDERS)
    parser.add_argument("--summary-file", default="reports/healing-records.json")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--conclusion", default="success", choices=("success", "failure"))
    parser.add_argument("--failed-jobs", default="", help="逗号分隔的失败 job 名")
    args = parser.parse_args(argv)

    webhook_url = os.getenv("WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("WEBHOOK_URL 未配置，跳过通知（exit 0）")
        return 0

    records: list[dict[str, Any]] = []
    cost: dict[str, Any] | None = None
    summary_path = Path(args.summary_file)
    if summary_path.exists():  # 摘要缺失（如 unit 阶段失败）不阻塞告警
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            records, cost = data.get("records", []), data.get("cost")
        except Exception:  # noqa: BLE001 - 摘要解析失败降级为空摘要
            logger.warning("摘要文件解析失败（按空摘要告警）", exc_info=True)
    else:
        print(f"摘要文件不存在（{summary_path}），按空摘要通知")

    summary = build_summary(
        records,
        cost,
        conclusion=args.conclusion,
        run_url=args.run_url,
        failed_jobs=args.failed_jobs,
    )
    ok = send(webhook_url, build_payload(args.provider, summary))
    print(f"通知{'已发送' if ok else '发送失败（已忽略）'}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
