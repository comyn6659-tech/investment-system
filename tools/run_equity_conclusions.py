#!/usr/bin/env python
"""用已批准政策跑 30 只固定测试集，生成 CLI 同源结论并首次写入结论账本。

- 与 CLI 共用 `Analyzer(use_policy_path=True)` 与 `evaluate_company`，不另写估值；
- 结论逐只与试运行 v1.1 的预演结论比对（30/30）；
- 只有 candidate / wait / avoid 写入 `data/ledger/equity-conclusions.jsonl`；
- 同日同快照重复运行由账本跳过，不重复写入。

用法：
    .venv/bin/python tools/run_equity_conclusions.py
    .venv/bin/python tools/run_equity_conclusions.py --date 2026-11-12
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.pipeline import Analyzer, AnalyzerConfig  # noqa: E402
from engine.policy.conclusion import record_from_result  # noqa: E402
from engine.policy.conclusion_ledger import ConclusionLedger  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
TRIAL = ROOT / "data" / "policy-trial" / "trial-report.json"
JSON_OUT = ROOT / "data" / "policy-trial" / "conclusion-run.json"
MD_OUT = ROOT / "docs" / "archive" / "phase4b-equity-conclusions.md"
LEDGER = ROOT / "data" / "ledger" / "equity-conclusions.jsonl"

_MAP = {
    "candidate_buy_point": "candidate",
    "wait": "wait",
    "avoid": "avoid",
    "undetermined": "undetermined",
}


def _pct(value) -> str:
    return "—" if value is None else f"{value:+.1f}%"


def _money(value) -> str:
    return "—" if value is None else f"{value:,.2f}"


_STATUS = {
    "complete": "完整",
    "partial": "部分完成",
    "insufficient_data": "资料不足",
    "unsupported": "路线不支持",
}


def render_markdown(payload: dict) -> str:
    lines = [
        "# 第四阶段第二批：30 只个股正式结论（已批准政策 v1.1）",
        "",
        f"分析日：{payload['analysis_date']}｜政策：`{payload['policy']['policy_id']}`"
        f"（{payload['policy']['policy_digest']}，approved={payload['policy']['approved']}）",
        f"生成脚本：`tools/run_equity_conclusions.py`｜快照：`data/policy-trial/snapshots/`"
        f"（`as_of=2026-11-11`）",
        "",
        f"**与试运行比对：{payload['match_count']}/{payload['total']} 一致**"
        + ("（0 差异）" if payload["match_count"] == payload["total"] else "（存在差异，见下）"),
        "",
        f"正式结论 {payload['formal_count']} 只，`undetermined` {payload['undetermined_count']} 只；"
        f"首次写入 {payload['formal_count']} 条（本次新增 {len(payload['ledger']['added'])} 条、"
        f"跳过 {len(payload['ledger']['skipped'])} 条），账本累计 {payload['ledger']['total']} 条。",
        "",
        "| # | 代码 | 名称 | 状态 | 等级 | V 悲观 | V 基准 | V 乐观 | 现价 | 候选门槛 | 距门槛 | 结论 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for index, row in enumerate(payload["rows"], start=1):
        lines.append(
            f"| {index} | {row['code']} | {row['name'] or '—'} | "
            f"{_STATUS.get(row['status'], row['status'])} | {row['grade'] or '—'} | "
            f"{'≤0（不展示）' if (row['values'].get('pessimistic') or 0) <= 0 and row['values'].get('pessimistic') is not None else _money(row['values'].get('pessimistic'))} | "
            f"{_money(row['values'].get('base'))} | {_money(row['values'].get('optimistic'))} | "
            f"{_money(row['price'])} | {_money(row['entry'])} | "
            f"{row['distance']} | **{row['decision']}** |"
        )
    lines += [
        "",
        "读法：等级 A/B/C 衡量数据完整度与模型稳定性，不是公司质量；「回避」是价格高于"
        "乐观情景价值，不是对公司的负面判断。三档价值与候选门槛按已批准政策计算，"
        "本报告不构成投资建议。",
        "",
        "## 与试运行的差异",
        "",
    ]
    if payload["mismatches"]:
        for item in payload["mismatches"]:
            lines.append(f"- {item['code']}：试运行 {item['trial']} → CLI {item['cli']}")
    else:
        lines.append("无。30 只的正式结论与试运行 v1.1 预演结论逐只一致。")
    lines += [
        "",
        "## 账本",
        "",
        f"`data/ledger/equity-conclusions.jsonl`：首次写入 {payload['formal_count']} 条；"
        f"本次新增 {len(payload['ledger']['added'])} 条、跳过 {len(payload['ledger']['skipped'])} 条，"
        f"累计 {payload['ledger']['total']} 条；`--check` 结果见复现命令。",
        "",
        "## 复现",
        "",
        "```bash",
        "cd /Users/comyn/Documents/deepseek-harness/投资/investment-system",
        ".venv/bin/python tools/run_equity_conclusions.py --date 2026-11-12",
        ".venv/bin/python -m engine.cli --check-conclusions",
        "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider",
        "```",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="30 只个股正式结论（已批准政策）")
    parser.add_argument("--date", default="2026-11-12", help="分析日 YYYY-MM-DD")
    parser.add_argument("--no-write", action="store_true", help="只计算，不写账本")
    args = parser.parse_args(argv)

    trial = json.loads(TRIAL.read_text(encoding="utf-8"))
    expected = {r["code"]: r for r in trial["records"]}
    now = datetime.fromisoformat(args.date + "T12:00:00").replace(tzinfo=TZ)
    analyzer = Analyzer(AnalyzerConfig(use_policy_path=True))
    policy = analyzer._approved_policy()

    rows = []
    mismatches = []
    formal = []
    for record in trial["records"]:
        code = record["code"]
        result = analyzer.analyze(code, now=now)
        want = _MAP[record["trial_conclusion"]["conclusion"]]
        got = result.decision
        if got != want:
            mismatches.append({"code": code, "trial": want, "cli": got})
        block = (result.payload or {}).get("policy_conclusion") or {}
        values = block.get("values") or {}
        rows.append(
            {
                "code": code,
                "name": record.get("name"),
                "status": result.analysis_status,
                "grade": (block.get("grade") or {}).get("grade"),
                "values": {
                    key: (values.get(key) or {}).get("value")
                    for key in ("pessimistic", "base", "optimistic")
                },
                "price": (block.get("price") or {}).get("value", result.payload.get("price")),
                "entry": (block.get("candidate_entry_price") or {}).get("value"),
                "distance": _pct(block.get("distance_to_candidate_pct")),
                "decision": got,
                "reason_codes": list(result.reason_codes),
            }
        )
        if got in ("candidate", "wait", "avoid"):
            formal.append(record_from_result(result, recorded_at=now.isoformat(timespec="seconds")))

    ledger = ConclusionLedger(LEDGER)
    outcome = {"added": [], "skipped": []} if args.no_write else ledger.append(formal)
    payload = {
        "analysis_date": args.date,
        "generated_at": now.isoformat(timespec="seconds"),
        "policy": {
            "policy_id": policy.policy_id,
            "policy_digest": policy.policy_digest,
            "approved": policy.approved,
        },
        "total": len(rows),
        "match_count": len(rows) - len(mismatches),
        "mismatches": mismatches,
        "formal_count": len(formal),
        "undetermined_count": len(rows) - len(formal),
        "ledger": {
            "path": str(LEDGER.relative_to(ROOT)),
            "added": outcome["added"],
            "skipped": outcome["skipped"],
            "total": len(ledger.load()),
        },
        "rows": rows,
    }
    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MD_OUT.write_text(render_markdown(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "match": f"{payload['match_count']}/{payload['total']}",
                "formal": payload["formal_count"],
                "ledger_added": len(outcome["added"]),
                "ledger_skipped": len(outcome["skipped"]),
                "ledger_total": payload["ledger"]["total"],
                "mismatches": mismatches,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not mismatches else 1


if __name__ == "__main__":
    sys.exit(main())
