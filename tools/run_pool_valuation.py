#!/usr/bin/env python
"""好公司池 + 收息池全部非银行成员的个股政策估值与正式结论账本。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.policy.conclusion_ledger import (  # noqa: E402
    ConclusionConflict,
    ConclusionLedger,
    ConclusionRecord,
)
from engine.policy import trace as policy_trace  # noqa: E402
from engine.policy.equity import load_policy  # noqa: E402
from engine.policy.freshness import check_freshness  # noqa: E402
from engine.policy.trial import evaluate_company  # noqa: E402
from engine.policy.valuation import load_macro_series  # noqa: E402
from engine.pool_valuation.adapter import BatchStore, build_snapshot, load_evidence  # noqa: E402
from engine.quality.tables import read_table  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
POLICY = ROOT / "data" / "policies" / "equity-policy-v1.1.json"
QUALITY_REPORT = ROOT / "data" / "quality-pool" / "report.json"
INCOME_REPORT = ROOT / "data" / "income-pool" / "report.json"
BANK_REPORT = ROOT / "data" / "bank-pool" / "report.json"
OWNERSHIP = ROOT / "data" / "income-pool" / "snapshot-qps-d8fb7e3718675b93" / "ownership.csv.gz"
MACRO = ROOT / "data" / "policy-trial" / "macro" / "cn-10y-yield.json"
LEDGER = ROOT / "data" / "ledger" / "equity-conclusions.jsonl"
OUT_JSON = ROOT / "data" / "pool-valuation" / "pool-member-valuation.json"
OUT_MD = ROOT / "docs" / "archive" / "phase4j-pool-valuation-report.md"
ANALYSIS_DATE = date(2026, 11, 13)

CONCLUSION_LABELS = {
    "candidate_buy_point": "候选买点",
    "wait": "等待",
    "avoid": "回避",
    "undetermined": "无法判断",
}


def _load_codes() -> tuple[list[str], dict[str, list[str]], dict[str, str]]:
    quality = json.loads(QUALITY_REPORT.read_text(encoding="utf-8"))
    income = json.loads(INCOME_REPORT.read_text(encoding="utf-8"))
    bank = json.loads(BANK_REPORT.read_text(encoding="utf-8"))
    q = {str(row["code"]).zfill(6) for row in quality["in_pool"]}
    i = {str(row["code"]).zfill(6) for row in income["in_pool"]}
    banks = {str(row["code"]).zfill(6) for row in bank["in_pool"]}
    codes = sorted((q | i) - banks)
    pools: dict[str, list[str]] = {code: [] for code in codes}
    for code in q:
        if code in pools:
            pools[code].append("好公司池")
    for code in i:
        if code in pools:
            pools[code].append("稳定收息池")
    cycle = {}
    for row in quality.get("in_pool") or []:
        cycle[str(row["code"]).zfill(6)] = str(row.get("cycle_label") or "未判定")
    return codes, pools, cycle


def _ownership() -> dict[str, str]:
    return {
        str(row["code"]).zfill(6): str(row.get("ownership") or "未标记")
        for row in read_table(OWNERSHIP)
    }


def _reason_group(record: dict) -> str:
    route = record.get("route") or {}
    if route.get("status") == "family_not_implemented":
        return f"路线不支持：{route.get('family') or 'unknown'}"
    reasons = record.get("reason_codes") or []
    if any("TERMINAL" in item for item in reasons):
        return "终值连续性护栏"
    if any("BRIDGE" in item for item in reasons):
        return "现金流勾稽"
    if (record.get("grade") or {}).get("grade") == "C":
        return "等级 C（未满足 A/B 条件）"
    status = record.get("status")
    if status == "insufficient_data":
        return "资料不足"
    if status == "partial":
        return "部分完成"
    if status == "unsupported":
        return "路线不支持"
    return "其他"


def _record_id(record: dict) -> str:
    return ConclusionRecord(
        instrument_id=record["instrument_id"],
        code=record["code"],
        analysis_date=record["analysis_date"],
        policy_version=record["policy_version"],
        policy_digest=record["policy_digest"],
        snapshot_id=record["snapshot_id"],
        price=record["price"],
        price_date=record["price_date"],
        values=record["values"],
        grade=record["grade"],
        margin_of_safety=record["margin_of_safety"],
        conclusion=record["conclusion"],
        binding_condition=record["binding_condition"],
    ).record_id


def _to_ledger(record: dict) -> ConclusionRecord:
    return ConclusionRecord(
        instrument_id=record["instrument_id"],
        code=record["code"],
        analysis_date=record["analysis_date"],
        policy_version=record["policy_version"],
        policy_digest=record["policy_digest"],
        snapshot_id=record["snapshot_id"],
        price=record["price"],
        price_date=record["price_date"],
        values=record["values"],
        grade=record["grade"],
        margin_of_safety=record["margin_of_safety"],
        conclusion=record["conclusion"],
        binding_condition=record["binding_condition"],
        recorded_at=datetime.now(TZ).isoformat(timespec="seconds"),
    )


def run(*, write_ledger: bool) -> dict:
    policy = load_policy(POLICY)
    macro = load_macro_series(MACRO)
    batch = BatchStore()
    codes, pools, cycle = _load_codes()
    owners = _ownership()
    if write_ledger:
        ledger = ConclusionLedger(LEDGER)
        existing = ledger.load()
    else:
        ledger = None
        existing = []
    results = []
    candidates = []
    undetermined = defaultdict(list)
    for code in codes:
        snapshot = build_snapshot(batch, code)
        evidence = load_evidence(
            code,
            name=batch.name_for(code),
            industry=batch.industry_for(code),
            snapshot=snapshot,
        )
        record = evaluate_company(policy, snapshot=snapshot, evidence=evidence, macro=macro, code=code)
        freshness = check_freshness(policy, snapshot, analysis_date=ANALYSIS_DATE)
        if freshness:
            record["status"] = "insufficient_data"
            record["status_reason"] = "；".join(hit.detail for hit in freshness)
            record["freshness"] = [hit.to_dict() for hit in freshness]
            record["trial_conclusion"] = {"conclusion": "undetermined", "reason": record["status_reason"]}
        record["pools"] = pools.get(code, [])
        record["ownership"] = owners.get(code, "未标记")
        family = (record.get("route") or {}).get("family")
        record["cycle_label"] = cycle.get(code) or {
            "non_financial": "非金融企业",
            "cyclical": "明显周期",
            "holding": "控股平台",
            "distressed": "困境",
            "bank": "银行",
        }.get(family, "未判定")
        record["batch_snapshot_id"] = snapshot.snapshot_id
        values = record.get("values") or {}
        trial = record.get("trial_conclusion") or {}
        thresholds = trial.get("thresholds") or {}
        entry = thresholds.get("candidate_entry_price")
        price = record.get("price")
        distance = price / entry - 1.0 if entry and price is not None else None
        record["distance_to_candidate"] = distance
        results.append(record)
        if trial.get("conclusion") == "candidate_buy_point":
            candidates.append(record)
        if trial.get("conclusion") == "undetermined" or record.get("status") != "complete":
            undetermined[_reason_group(record)].append(record)

    conclusions = Counter((row.get("trial_conclusion") or {}).get("conclusion") for row in results)
    statuses = Counter(row.get("status") for row in results)
    grades = Counter((row.get("grade") or {}).get("grade") for row in results)

    ledger_added = []
    ledger_skipped = []
    ledger_conflicts = []
    ledger_eligible = 0
    if ledger is not None:
        for row in results:
            trial = row.get("trial_conclusion") or {}
            if not (
                row.get("status") == "complete"
                and (row.get("grade") or {}).get("grade") in ("A", "B")
                and trial.get("conclusion") in ("candidate_buy_point", "wait", "avoid")
                and row.get("price") is not None
                and row.get("values")
            ):
                continue
            ledger_eligible += 1
            record = ConclusionRecord(
                instrument_id=row["instrument_id"],
                code=row["code"],
                analysis_date=ANALYSIS_DATE.isoformat(),
                policy_version=policy.policy_id,
                policy_digest=policy.policy_digest,
                snapshot_id=row["batch_snapshot_id"],
                price=float(row["price"]),
                price_date=str(row.get("price_as_of") or ""),
                values={key: float(row["values"][key]) for key in ("pessimistic", "base", "optimistic")},
                grade=str((row.get("grade") or {}).get("grade")),
                margin_of_safety=(row.get("grade") or {}).get("margin_of_safety"),
                conclusion=str(trial.get("conclusion")),
                binding_condition=str(
                    (trial.get("thresholds") or {}).get("binding_condition_text") or ""
                ),
                recorded_at=datetime.now(TZ).isoformat(timespec="seconds"),
                trace=policy_trace.stock_trace(
                    policy_path=POLICY,
                    financial=policy_trace.batch_reference(batch),
                    quote={
                        **policy_trace.batch_price_reference(batch),
                        "price_date": str(row.get("price_as_of") or ""),
                    },
                ),
            )
            try:
                outcome = ledger.append([record])
            except ConclusionConflict as exc:
                ledger_conflicts.append({"code": row["code"], "reason": str(exc)})
                continue
            ledger_added.extend(outcome.get("added") or [])
            ledger_skipped.extend(outcome.get("skipped") or [])
        if ledger_conflicts:
            print(f"账本冲突：{len(ledger_conflicts)} 条（未覆盖）")

    payload = {
        "meta": {
            "policy": policy.policy_id,
            "policy_digest": policy.policy_digest,
            "analysis_date": ANALYSIS_DATE.isoformat(),
            "batch_snapshot_id": batch.manifest["snapshot_id"],
            "batch_snapshot_tables": len(batch.manifest.get("tables") or []),
            "batch_snapshot_bytes": batch.manifest.get("total_bytes"),
            "quote_source": "tencent.quote_snapshot",
            "note": "批量输入路径；政策与估值代码未改。银行不估值。",
        },
        "counts": {
            "pool_members": len(results),
            "conclusions": dict(conclusions),
            "statuses": dict(statuses),
            "grades": dict(grades),
            "candidates": len(candidates),
        },
        "ranking": [
            {
                "code": row["code"],
                "name": row.get("name"),
                "price": row.get("price"),
                "price_date": row.get("price_as_of"),
                "candidate_entry_price": (row.get("trial_conclusion") or {}).get("thresholds", {}).get("candidate_entry_price"),
                "distance": row.get("distance_to_candidate"),
                "grade": (row.get("grade") or {}).get("grade"),
                "binding": (row.get("trial_conclusion") or {}).get("thresholds", {}).get("binding_condition_text"),
                "pools": row.get("pools"),
                "ownership": row.get("ownership"),
                "cycle_label": row.get("cycle_label"),
                "conclusion": (row.get("trial_conclusion") or {}).get("conclusion"),
            }
            for row in sorted(
                [
                    row
                    for row in results
                    if (row.get("grade") or {}).get("grade") in ("A", "B")
                    and row.get("status") == "complete"
                    and row.get("distance_to_candidate") is not None
                ],
                key=lambda row: row["distance_to_candidate"],
            )
        ],
        "undetermined": {
            reason: [
                {
                    "code": row["code"],
                    "name": row.get("name"),
                    "status": row.get("status"),
                    "status_reason": row.get("status_reason"),
                    "grade": (row.get("grade") or {}).get("grade"),
                }
                for row in rows
            ]
            for reason, rows in sorted(undetermined.items(), key=lambda item: -len(item[1]))
        },
        "ledger": {
            "path": str(LEDGER.relative_to(ROOT)),
            "existing_before": len(existing),
            "eligible": ledger_eligible,
            "written": len(ledger_added),
            "skipped": len(ledger_skipped),
            "conflicts": len(ledger_conflicts),
            "baseline_before": (
                len(existing) - (ledger_eligible - len(ledger_added))
                if ledger_eligible
                else len(existing)
            ),
            "conflict_details": ledger_conflicts[:20],
            "added_ids": ledger_added[:20],
        },
        "results": results,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT_MD.write_text(render(payload), encoding="utf-8")
    print(
        f"池成员 {len(results)}：候选 {conclusions.get('candidate_buy_point',0)}、"
        f"等待 {conclusions.get('wait',0)}、回避 {conclusions.get('avoid',0)}、"
        f"无法判断 {conclusions.get('undetermined',0)}；账本写入 {len(ledger_added)}"
    )
    return payload


def render(payload: dict) -> str:
    counts = payload["counts"]
    lines = [
        "# 好公司池 + 收息池成员个股估值（批量输入）",
        "",
        f"- 分析日：{payload['meta']['analysis_date']}",
        f"- 政策：`{payload['meta']['policy']}`（{payload['meta']['policy_digest']}）",
        f"- 批量快照：`{payload['meta']['batch_snapshot_id']}`；"
        f"{payload['meta']['batch_snapshot_tables']} 张表，{payload['meta']['batch_snapshot_bytes']/1024/1024:.2f} MB",
        "- 银行不估值；政策与估值代码未改。",
        "",
        "## 1. 结论分布",
        "",
        "| 结论 | 数量 |",
        "|---|---:|",
    ]
    for key, label in CONCLUSION_LABELS.items():
        lines.append(f"| {label} | {counts['conclusions'].get(key, 0)} |")
    lines += [
        f"| 池成员合计 | {counts['pool_members']} |",
        "",
        "| 状态 | 数量 |",
        "|---|---:|",
    ]
    for key, value in sorted(counts["statuses"].items()):
        lines.append(f"| {key} | {value} |")
    lines += ["", "| 等级 | 数量 |", "|---|---:|"]
    for key in ("A", "B", "C"):
        lines.append(f"| {key} | {counts['grades'].get(key, 0)} |")
    lines += [
        "",
        "## 2. 距候选门槛（A/B 且 complete）",
        "",
        "| 排名 | 代码 | 名称 | 现价 | 门槛价 | 距门槛 | 等级 | 起约束条件 | 所属池 | 所有制 | 周期 |",
        "|---:|---|---|---:|---:|---:|---|---|---|---|---|",
    ]
    for index, row in enumerate(payload["ranking"], start=1):
        distance = row["distance"]
        lines.append(
            f"| {index} | {row['code']} | {row['name'] or '—'} | {row['price']:.2f} | "
            f"{row['candidate_entry_price']:.2f} | {distance:+.2%} | {row['grade']} | "
            f"{row.get('binding') or '—'} | {'、'.join(row['pools']) or '—'} | "
            f"{row['ownership']} | {row['cycle_label']} |"
        )
    lines += ["", "## 3. 无法判断的原因分组", ""]
    for reason, rows in payload["undetermined"].items():
        lines.append(f"### {reason}（{len(rows)}）")
        lines.append("")
        lines.append("| 代码 | 名称 | 状态 | 等级 | 说明 |")
        lines.append("|---|---|---|---|---|")
        for row in rows[:30]:
            note = (row.get("status_reason") or "").replace("|", "／")
            if len(note) > 160:
                note = note[:158] + "…"
            lines.append(
                f"| {row['code']} | {row['name'] or '—'} | {row['status']} | {row['grade'] or '—'} | {note} |"
            )
        lines.append("")
    lines += [
        "## 4. 结论账本",
        "",
        f"- 账本：`{payload['ledger']['path']}`",
        f"- 本次应写 {payload['ledger']['eligible']} 条；运行前账本 {payload['ledger']['existing_before']} 条，"
        f"本次新写 {payload['ledger']['written']} 条，已存在跳过 {payload['ledger']['skipped']} 条，"
        f"冲突 {payload['ledger']['conflicts']} 条。",
        f"- 首次写入的基准为 {payload['ledger']['baseline_before']} 条，当前账本 "
        f"{payload['ledger']['existing_before']} 条；旧记录不变，只追加。",
        "- 只追加；同一标的、同一分析日、同一快照内容不同则冲突，不覆盖。",
        "",
        "## 5. 口径",
        "",
        "- 财务输入来自按报告期批量的合并报表与分红表；现价来自全市场行情快照；总股本来自最近一期股本变动。",
        "- 政策与估值代码未改；字段映射见 `docs/archive/phase4j-field-mapping.md`。",
        "- 银行不估值；银行估值 `bank-check-v1-draft` 仍为 draft 且不启用。",
        "",
        "## 6. 抽查复算",
        "",
        "```bash",
        ".venv/bin/python tools/run_pool_valuation.py --show 600519",
        "```",
        "",
        "该命令只从批量快照重建一只股票的 `fin.*`/`div.*`/`profile.*`/`price.*` 事实，"
        "交给原政策代码复算并打印输入、三档价值、等级与结论；不写账本。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-ledger", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--show", help="只从批量快照复算一只股票并打印")
    args = parser.parse_args()
    if args.show:
        policy = load_policy(POLICY)
        macro = load_macro_series(MACRO)
        batch = BatchStore()
        code = str(args.show).zfill(6)
        snapshot = build_snapshot(batch, code)
        evidence = load_evidence(code, name=batch.name_for(code), industry=batch.industry_for(code), snapshot=snapshot)
        record = evaluate_company(policy, snapshot=snapshot, evidence=evidence, macro=macro, code=code)
        print(json.dumps({
            "code": code,
            "snapshot_id": snapshot.snapshot_id,
            "price": record.get("price"),
            "price_date": record.get("price_as_of"),
            "status": record.get("status"),
            "grade": (record.get("grade") or {}).get("grade"),
            "values": record.get("values"),
            "conclusion": (record.get("trial_conclusion") or {}).get("conclusion"),
            "inputs": record.get("inputs"),
        }, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    run(write_ledger=not args.no_ledger and not args.dry_run)
