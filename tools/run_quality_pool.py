#!/usr/bin/env python
"""运行好公司池规则（草案）并写报告与只追加账本。

用法：
    .venv/bin/python tools/run_quality_pool.py --dry-run
    .venv/bin/python tools/run_quality_pool.py
    .venv/bin/python tools/run_quality_pool.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.quality.evaluator import evaluate_company, evaluate_pool  # noqa: E402
from engine.quality.policy import load_policy  # noqa: E402
from engine.quality.report import build_report, render_markdown  # noqa: E402
from engine.quality.snapshot import load_snapshot_dir  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
POLICY_PATH = ROOT / "data" / "policies" / "quality-pool-v1.2.json"
BASELINE_POLICY_PATH = ROOT / "data" / "policies" / "quality-pool-v1.1-draft.json"
POOL_DIR = ROOT / "data" / "quality-pool"
BASELINE_REPORT_4D = POOL_DIR / "report-4d.json"
BASELINE_SNAPSHOT_4E = POOL_DIR / "snapshot-qps-1c1edb9a5fc01bc3"
MANIFEST_PATH = POOL_DIR / "manifest.json"
REPORT_JSON = POOL_DIR / "report.json"
REPORT_MD = ROOT / "docs" / "archive" / "phase4f-quality-pool-report.md"
LEDGER = ROOT / "data" / "ledger" / "quality-pool.jsonl"

STATUS_LABELS = {"in_pool": "入池", "pending": "待定", "out_of_scope": "范围外"}
CONDITION_LABELS = {"pass": "通过", "fail": "不通过", "pending": "待定", "untested": "未检验"}
ROLE_LABELS = {
    "company": "公司本身",
    "controller": "控股股东",
    "actual_controller": "实际控制人",
    "concert_party": "一致行动人",
    "director_officer": "时任董监高",
    "other_shareholder": "其他股东（非控股、非一致行动人）",
    "subsidiary": "子公司/分公司",
    "other_entity": "其他主体",
    "unknown": "主体身份不明",
}


def _now() -> datetime:
    return datetime.now(TZ)


def _pool_hash(codes: list[str]) -> str:
    digest = hashlib.sha256(",".join(sorted(codes)).encode("utf-8")).hexdigest()
    return "qph-" + digest[:16]


def _record_id(record: dict) -> str:
    body = {key: value for key, value in record.items() if key not in ("record_id", "recorded_at")}
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return "qpr-" + digest[:16]


def _read_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    rows = []
    with LEDGER.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _append_ledger(run, *, dry_run: bool) -> int:
    codes = sorted(item.code for item in run.in_pool)
    record = {
        "as_of": run.as_of,
        "policy_id": run.policy_id,
        "policy_digest": run.policy_digest,
        "snapshot_id": run.snapshot_id,
        "counts": run.counts,
        "pool_codes": codes,
        "pool_hash": _pool_hash(codes),
    }
    record["record_id"] = _record_id(record)
    record["recorded_at"] = _now().isoformat(timespec="seconds")
    rows = _read_ledger()
    for row in rows:
        if (
            row.get("as_of") == record["as_of"]
            and row.get("policy_id") == record["policy_id"]
            and row.get("snapshot_id") == record["snapshot_id"]
        ):
            if row.get("record_id") != record["record_id"]:
                print("冲突：同日同规则同快照已有不同名单，拒绝覆盖")
                return 1
            print(f"跳过（已存在）：{record['as_of']} / {record['policy_id']}")
            return 0
    if dry_run:
        print(f"dry-run：将追加 {len(codes)} 只入池记录，pool_hash={record['pool_hash']}")
        return 0
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
    print(f"账本追加 1 条，pool_hash={record['pool_hash']}")
    return 0


def check_ledger() -> int:
    rows = _read_ledger()
    problems = []
    seen = set()
    for index, row in enumerate(rows, start=1):
        if row.get("record_id") != _record_id(row):
            problems.append(f"第 {index} 行 record_id 被改写")
        key = (row.get("as_of"), row.get("policy_id"), row.get("snapshot_id"))
        if key in seen:
            problems.append(f"第 {index} 行重复：{key}")
        seen.add(key)
        if row.get("pool_hash") != _pool_hash(row.get("pool_codes") or []):
            problems.append(f"第 {index} 行 pool_hash 与名单不一致")
    if problems:
        print("\n".join(problems))
        return 1
    print(f"好公司池账本 {len(rows)} 条，0 问题")
    return 0


def _fmt_ratio(value: float | None) -> str:
    return f"{value:.2%}" if value is not None else "—"


def _governance_evidence(company, condition_id: str) -> str:
    if company is None:
        return "缺少公司数据"
    if condition_id == "audit_opinion":
        bad = [
            f"{year} {opinion}"
            for year, opinion in sorted(company.audit_opinions.items())
            if opinion != "标准无保留意见"
        ]
        return "；".join(bad) or "审计意见缺失"
    if condition_id == "no_penalty":
        counted = [item for item in company.penalties if item.counted]
        recorded = [item for item in company.penalties if not item.counted]
        parts = []
        if counted:
            parts.append(
                "计入："
                + "；".join(
                    f"{item.date} {item.event_type}：{item.subject or '—'}（{ROLE_LABELS.get(item.subject_role, item.subject_role or '—')}）"
                    for item in counted
                )
            )
        if recorded:
            parts.append(
                "只记录："
                + "；".join(
                    f"{item.date} {item.event_type}：{item.subject or '—'}（{ROLE_LABELS.get(item.subject_role, item.subject_role or '—')}）"
                    for item in recorded
                )
            )
        return "；".join(parts) or "无处罚公告"
    if condition_id == "pledge_ratio":
        ratio = company.controlling_pledge_ratio
        role = company.pledge_subject_role or "控股股东"
        subject = company.controlling_shareholder or "—"
        if ratio is not None:
            head = f"{subject}；口径：{role}；质押 {ratio:.2%}"
            if company.pledge_detail:
                return f"{head}；{company.pledge_detail}"
            return f"{head}（{company.pledge_report_date or '最新定期报告'}）"
        note = company.pledge_note or "质押数据缺失"
        return f"{subject}（{role}）：{note}"
    return ""


def _status_label(result) -> str:
    if result.status in STATUS_LABELS:
        return STATUS_LABELS[result.status]
    if len(result.failing) == 1 and not result.pending:
        return "只差一条"
    return "未入池"


def _baseline_run():
    snapshot, companies, _stats = load_snapshot_dir(BASELINE_SNAPSHOT_4E)
    policy = load_policy(BASELINE_POLICY_PATH)
    run = evaluate_pool(
        companies,
        policy,
        as_of=snapshot["as_of"],
        snapshot_id=snapshot["snapshot_id"],
        include_sensitivity=False,
    )
    return run, {company.code: company for company in companies}


def _order() -> list[str]:
    return [
        "roe_average",
        "roe_consistency",
        "roe_recent",
        "cash_conversion",
        "fcf_positive",
        "loss_years",
        "net_debt_ebitda",
        "audit_opinion",
        "no_penalty",
        "pledge_ratio",
        "dividend_payout",
    ]


def _comparison(run, companies_by_code, baseline_run, baseline_companies) -> dict:
    current = {item.code: item for item in run.results}
    baseline = {item.code: item for item in baseline_run.results}
    report = json.loads(BASELINE_REPORT_4D.read_text(encoding="utf-8"))
    candidate_codes = [item["code"] for item in report["in_pool"]]
    candidate_codes += [item["code"] for item in report["near_misses"]]
    rows = []
    for code in dict.fromkeys(candidate_codes):
        now = current.get(code)
        before = baseline.get(code)
        if now is None or before is None:
            continue
        old_label = _status_label(before)
        new_label = _status_label(now)
        if old_label == new_label:
            continue
        old_conditions = {item.condition_id: item for item in before.conditions}
        new_conditions = {item.condition_id: item for item in now.conditions}
        changes = []
        for cid in _order():
            old = old_conditions.get(cid)
            new = new_conditions.get(cid)
            if old is None or new is None or old.status == new.status:
                continue
            label = new.label or old.label
            detail = ""
            company = companies_by_code.get(code)
            if cid in ("audit_opinion", "no_penalty", "pledge_ratio"):
                detail = _governance_evidence(company, cid)
            changes.append(
                {
                    "condition_id": cid,
                    "label": label,
                    "from": old.status,
                    "to": new.status,
                    "detail": detail,
                }
            )
        reason_parts = [
            f"{item['label']}：{CONDITION_LABELS.get(item['from'], item['from'])}→{CONDITION_LABELS.get(item['to'], item['to'])}"
            + (f"（{item['detail']}）" if item["detail"] else "")
            for item in changes
        ]
        if not reason_parts:
            reason_parts = [f"由{old_label}改为{new_label}（逐条状态无差异）"]
        rows.append(
            {
                "code": code,
                "name": now.name,
                "batch4e": old_label,
                "batch4f": new_label,
                "changes": changes,
                "reason": "；".join(reason_parts),
            }
        )
    summary = {}
    for row in rows:
        key = f"{row['batch4e']}→{row['batch4f']}"
        summary[key] = summary.get(key, 0) + 1
    return {"count": len(rows), "by_change": summary, "rows": rows}


def _pledge_sources(companies_by_code, baseline_companies, run) -> dict:
    baseline = {item.code: item for item in baseline_companies.values()}
    report_4d = json.loads(BASELINE_REPORT_4D.read_text(encoding="utf-8"))
    pool_4d = {item["code"] for item in report_4d.get("in_pool") or []}
    k1 = []
    k2 = []
    for code, company in companies_by_code.items():
        before = baseline.get(code)
        if before is None:
            continue
        if before.pledge_note == "no_periodic_pledge_table":
            k1.append(
                {
                    "code": code,
                    "name": company.name,
                    "batch4d": "第四批入池" if code in pool_4d else "只差一条",
                    "batch4e": _status_label(next(item for item in run.results if item.code == code)),
                    "subject": company.controlling_shareholder,
                    "role": company.pledge_subject_role,
                    "ratio": company.controlling_pledge_ratio,
                    "source": company.pledge_source_url or "",
                    "note": company.pledge_detail or company.pledge_note or "",
                }
            )
        if company.pledge_subject_role == "第一大股东（无控股股东）":
            k2.append(
                {
                    "code": code,
                    "name": company.name,
                    "subject": company.controlling_shareholder,
                    "ratio": company.controlling_pledge_ratio,
                    "source": company.pledge_source_url or "",
                    "note": company.pledge_detail or company.pledge_note or "",
                }
            )
    k1.sort(key=lambda row: row["code"])
    k2.sort(key=lambda row: row["code"])
    remaining = sum(
        1
        for company in companies_by_code.values()
        if company.governance_in_scope and not company.pledge_available
    )
    return {"k1": k1, "k2": k2, "remaining_pending": remaining}


def _penalty_subjects(companies_by_code) -> list[dict]:
    rows = []
    for code, company in sorted(companies_by_code.items()):
        for event in company.penalties:
            if event.counted:
                rows.append(
                    {
                        "code": code,
                        "name": company.name,
                        "date": event.date,
                        "event_type": event.event_type,
                        "subject": event.subject or "—",
                        "role": ROLE_LABELS.get(event.subject_role, event.subject_role or "—"),
                        "basis": event.subject_basis or event.title,
                    }
                )
    return rows


def _governance_summary(report: dict, run, companies, baseline_run, baseline_companies) -> None:
    by_code = {company.code: company for company in companies}
    baseline = {item.code: item for item in baseline_run.results}
    report_4d = json.loads(BASELINE_REPORT_4D.read_text(encoding="utf-8"))
    pool_4d = {item["code"] for item in report_4d.get("in_pool") or []}
    candidates = pool_4d | {item["code"] for item in report_4d.get("near_misses") or []}

    report["governance_coverage"] = {
        key: {
            "total": len(candidates),
            "available": sum(
                1 for code in candidates if getattr(by_code.get(code), flag, False)
            ),
            "ratio": (
                sum(1 for code in candidates if getattr(by_code.get(code), flag, False))
                / len(candidates)
                if candidates
                else 0.0
            ),
            "note": note,
        }
        for key, flag, note in (
            ("audit", "audit_available", "东方财富 F10 个股资产负债表 OPINION_TYPE"),
            ("penalty", "penalty_available", "东方财富公告检索 + 正文主体判定"),
            ("pledge", "pledge_available", "定期报告股东表；无控股股东按第一大股东口径"),
        )
    }

    results = {item.code: item for item in run.results}
    removed = []
    for code in sorted(pool_4d):
        result = results.get(code)
        if result is None or result.status == "in_pool":
            continue
        company = by_code.get(code)
        for condition in result.conditions:
            if condition.condition_id not in ("audit_opinion", "no_penalty", "pledge_ratio"):
                continue
            if condition.status not in ("fail", "pending"):
                continue
            removed.append(
                {
                    "code": code,
                    "name": result.name,
                    "condition": condition.label,
                    "evidence": _governance_evidence(company, condition.condition_id),
                }
            )
    report["governance_removed"] = removed

    report["governance_comparison"] = _comparison(
        run, by_code, baseline_run, baseline_companies
    )
    report["pledge_sources"] = _pledge_sources(by_code, baseline_companies, run)
    report["penalty_subjects"] = _penalty_subjects(by_code)


def _show(company, result, policy) -> None:
    print(f"{result.code} {result.name}｜{result.board}｜{result.industry or '—'}｜{result.status}")
    if result.reason:
        print(f"说明：{result.reason}")
    print("财年  归母净利润        归母净资产        平均净资产        ROE       经营现金流       资本开支        分红")
    for year in company.window_years:
        item = company.years.get(year)
        if item is None:
            print(f"{year}  缺年报")
            continue
        average = None
        if item.parent_equity is not None and year - 1 in company.years:
            previous = company.years[year - 1].parent_equity
            if previous is not None:
                average = (previous + item.parent_equity) / 2
        roe = (
            item.parent_netprofit / average
            if item.parent_netprofit is not None and average not in (None, 0)
            else None
        )
        print(
            f"{year}  {item.parent_netprofit or 0:>14,.0f}  {item.parent_equity or 0:>14,.0f}  "
            f"{average or 0:>14,.0f}  {(roe or 0):>7.3%}  {item.operating_cashflow or 0:>14,.0f}  "
            f"{item.capex or 0:>14,.0f}  {company.dividends.get(year, 0) or 0:>12,.0f}"
        )
    print()
    print("治理数据：")
    print("- 审计意见：" + "；".join(
        f"{year} {company.audit_opinions.get(year) or '缺失'}"
        for year in sorted(company.audit_opinions)
    ))
    print("- 处罚/立案：")
    if not company.penalties:
        print("  无（公告检索完成）")
    for item in company.penalties:
        flag = "计入" if item.counted else "只记录"
        print(
            f"  [{flag}] {item.date} {item.event_type}：{item.subject or '—'}"
            f"（{ROLE_LABELS.get(item.subject_role, item.subject_role or '—')}）；{item.title}"
        )
    print(
        "- 质押：" + (company.controlling_shareholder or "—")
        + f"；口径：{company.pledge_subject_role or '控股股东'}"
        + "；比例：" + (
            f"{company.controlling_pledge_ratio:.2%}" if company.controlling_pledge_ratio is not None else "不可得"
        )
        + (f"；{company.pledge_detail}" if company.pledge_detail else "")
    )
    print()
    for condition in result.conditions:
        print(f"- {condition.label}：{condition.status}｜{condition.reason or ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", help="快照 JSON；默认取 manifest.current")
    parser.add_argument("--policy", default=str(POLICY_PATH))
    parser.add_argument("--dry-run", action="store_true", help="不写账本")
    parser.add_argument("--no-ledger", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--show", help="从固定快照独立复算并打印一只股票的成绩单")
    args = parser.parse_args()
    if args.check:
        return check_ledger()

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    snapshot_path = Path(args.snapshot) if args.snapshot else POOL_DIR / manifest["current"]
    snapshot, companies, _stats = load_snapshot_dir(snapshot_path)
    policy = load_policy(Path(args.policy))
    if args.show:
        code = str(args.show).zfill(6)
        company = next((item for item in companies if item.code == code), None)
        if company is None:
            print(f"快照中没有 {code}", file=sys.stderr)
            return 1
        _show(company, evaluate_company(company, policy), policy)
        return 0
    run = evaluate_pool(
        companies, policy, as_of=snapshot["as_of"], snapshot_id=snapshot["snapshot_id"]
    )
    baseline_run, baseline_companies = _baseline_run()
    report = build_report(run, policy, snapshot)
    _governance_summary(report, run, companies, baseline_run, baseline_companies)
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(render_markdown(report, run, policy) + "\n", encoding="utf-8")

    counts = run.counts
    print(
        f"as_of={run.as_of} 快照={run.snapshot_id}："
        f"入池 {counts['in_pool']}，未入池 {counts['not_in_pool']}，"
        f"待定 {counts['pending']}，范围外 {counts['out_of_scope']}",
        flush=True,
    )
    print(f"报告：{REPORT_JSON}；{REPORT_MD}", flush=True)
    if args.no_ledger:
        return 0
    return _append_ledger(run, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
