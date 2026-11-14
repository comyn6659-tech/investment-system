#!/usr/bin/env python
"""30 只测试股：批量输入路径 vs 原个股快照路径逐只回归。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.data.snapshot import SnapshotStore  # noqa: E402
from engine.evidence.models import EvidenceBook  # noqa: E402
from engine.policy.equity import load_policy  # noqa: E402
from engine.policy.trial import evaluate_company  # noqa: E402
from engine.policy.valuation import load_macro_series  # noqa: E402
from engine.pool_valuation.adapter import BatchStore, build_snapshot, load_evidence  # noqa: E402

POLICY = ROOT / "data" / "policies" / "equity-policy-v1.1.json"
OLD_SNAPSHOTS = ROOT / "data" / "policy-trial" / "snapshots"
OLD_EVIDENCE = ROOT / "data" / "policy-trial" / "evidence"
MACRO = ROOT / "data" / "policy-trial" / "macro" / "cn-10y-yield.json"
OUT_JSON = ROOT / "data" / "pool-valuation" / "regression-30.json"
OUT_MD = ROOT / "docs" / "archive" / "phase4j-regression-30.md"

CODES = (
    "600519", "000858", "600887", "603288", "000895", "600436", "000333",
    "000651", "600660", "600031", "600309", "002415", "300750", "002594",
    "600276", "600900", "601088", "601006", "600941", "601668", "601919",
    "600019", "601899", "601857", "000002", "601012", "600036", "601398",
    "601318", "600030",
)


def _fact_number(snapshot, fact_id):
    fact = snapshot.fact(fact_id)
    if fact is None or not fact.is_usable_number:
        return None
    return float(fact.value)


def run() -> dict:
    policy = load_policy(POLICY)
    macro = load_macro_series(MACRO)
    old_store = SnapshotStore(OLD_SNAPSHOTS)
    evidence_book = EvidenceBook(OLD_EVIDENCE)
    batch = BatchStore()
    rows = []
    for code in CODES:
        venue = "SH" if code.startswith(("6", "9")) else "SZ"
        instrument_id = f"EQUITY:{code}.{venue}"
        latest = old_store.latest_for(instrument_id)
        old_snapshot = old_store.load(latest)
        evidence = evidence_book.get(instrument_id) or load_evidence(
            code, name=old_snapshot.instrument_id, snapshot=old_snapshot
        )
        old = evaluate_company(policy, snapshot=old_snapshot, evidence=evidence, macro=macro, code=code)
        price = _fact_number(old_snapshot, "price.close")
        shares = _fact_number(old_snapshot, "profile.total_shares")
        batch_snapshot = build_snapshot(
            batch,
            code,
            price_override=price,
            price_date_override=old_snapshot.as_of,
            shares_override=shares,
            as_of_override=old_snapshot.as_of,
        )
        new = evaluate_company(policy, snapshot=batch_snapshot, evidence=evidence, macro=macro, code=code)
        old_values = old.get("values") or {}
        new_values = new.get("values") or {}
        base_old = old_values.get("base")
        base_new = new_values.get("base")
        base_diff = (
            abs(base_new / base_old - 1.0)
            if base_old not in (None, 0) and base_new is not None
            else None
        )
        same_status = old.get("status") == new.get("status")
        same_grade = (old.get("grade") or {}).get("grade") == (new.get("grade") or {}).get("grade")
        same_conclusion = (
            (old.get("trial_conclusion") or {}).get("conclusion")
            == (new.get("trial_conclusion") or {}).get("conclusion")
        )
        rows.append(
            {
                "code": code,
                "name": old.get("name"),
                "status_old": old.get("status"),
                "status_new": new.get("status"),
                "grade_old": (old.get("grade") or {}).get("grade"),
                "grade_new": (new.get("grade") or {}).get("grade"),
                "conclusion_old": (old.get("trial_conclusion") or {}).get("conclusion"),
                "conclusion_new": (new.get("trial_conclusion") or {}).get("conclusion"),
                "base_old": base_old,
                "base_new": base_new,
                "base_rel_diff": base_diff,
                "same_status": same_status,
                "same_grade": same_grade,
                "same_conclusion": same_conclusion,
                "status_reason_old": old.get("status_reason"),
                "status_reason_new": new.get("status_reason"),
            }
        )
    passed = [
        row
        for row in rows
        if row["same_status"]
        and row["same_grade"]
        and row["same_conclusion"]
        and (
            (row["base_old"] is None and row["base_new"] is None)
            or (row["base_rel_diff"] is not None and row["base_rel_diff"] <= 0.01)
        )
    ]
    payload = {
        "meta": {
            "policy": policy.policy_id,
            "policy_digest": policy.policy_digest,
            "old_snapshot_dir": str(OLD_SNAPSHOTS.relative_to(ROOT)),
            "batch_snapshot_id": batch.manifest["snapshot_id"],
            "note": "回归时批量路径使用原快照的现价/价格日期/总股本，只替换财务与分红输入来源，以隔离字段差异。",
        },
        "summary": {
            "total": len(rows),
            "passed": len(passed),
            "failed": len(rows) - len(passed),
            "base_diff_max": max((row["base_rel_diff"] or 0 for row in rows), default=None),
        },
        "rows": rows,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT_MD.write_text(render(payload), encoding="utf-8")
    print(f"回归 {len(passed)} / {len(rows)} 通过；最大基准价值相对差 {payload['summary']['base_diff_max']:.4%}")
    return payload


def render(payload: dict) -> str:
    lines = [
        "# 30 只测试股：批量输入路径回归",
        "",
        f"- 政策：`{payload['meta']['policy']}`（{payload['meta']['policy_digest']}）",
        f"- 批量快照：`{payload['meta']['batch_snapshot_id']}`",
        f"- 通过：{payload['summary']['passed']} / {payload['summary']['total']}；"
        f"最大基准价值相对差：{payload['summary']['base_diff_max']:.4%}",
        "",
        payload["meta"]["note"],
        "",
        "| 代码 | 名称 | 状态 | 等级 | 结论 | 基准价值（原 / 批） | 相对差 | 通过 |",
        "|---|---|---|---|---|---:|---:|---|",
    ]
    for row in payload["rows"]:
        lines.append(
            f"| {row['code']} | {row['name'] or '—'} | {row['status_old']} / {row['status_new']} | "
            f"{row['grade_old']} / {row['grade_new']} | {row['conclusion_old']} / {row['conclusion_new']} | "
            f"{('—' if row['base_old'] is None else f'{row[chr(98)+chr(97)+chr(115)+chr(101)+chr(95)+chr(111)+chr(108)+chr(100)]:.4f}')} / "
            f"{('—' if row['base_new'] is None else f'{row[chr(98)+chr(97)+chr(115)+chr(101)+chr(95)+chr(110)+chr(101)+chr(119)]:.4f}')} | "
            f"{('—' if row['base_rel_diff'] is None else f'{row[chr(98)+chr(97)+chr(115)+chr(101)+chr(95)+chr(114)+chr(101)+chr(108)+chr(95)+chr(100)+chr(105)+chr(102)+chr(102)]:.4%}')} | "
            f"{'✓' if row['same_status'] and row['same_grade'] and row['same_conclusion'] and (row['base_rel_diff'] is None or row['base_rel_diff'] <= 0.01) else '✗'} |"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    run()
