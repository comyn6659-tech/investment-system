#!/usr/bin/env python
"""运行稳定收息型规则（草案）并写报告与只追加账本。

用法：
    .venv/bin/python tools/run_income_pool.py --dry-run
    .venv/bin/python tools/run_income_pool.py
    .venv/bin/python tools/run_income_pool.py --check
    .venv/bin/python tools/run_income_pool.py --show 600519
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

from engine.income.evaluator import evaluate_company, evaluate_pool  # noqa: E402
from engine.income.report import build_report, render_markdown  # noqa: E402
from engine.quality.policy import load_policy  # noqa: E402
from engine.quality.snapshot import load_snapshot_dir  # noqa: E402
from engine.quality.tables import read_table  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
POLICY_PATH = ROOT / "data" / "policies" / "income-pool-v1-draft.json"
INCOME_DIR = ROOT / "data" / "income-pool"
MANIFEST_PATH = INCOME_DIR / "manifest.json"
CANDIDATES_PATH = INCOME_DIR / "candidates.json"
QUALITY_REPORT = ROOT / "data" / "quality-pool" / "report.json"
DIVIDEND_DIR = ROOT / "data" / "cache" / "quality-pool" / "tables" / "dividends"
REPORT_JSON = INCOME_DIR / "report.json"
REPORT_MD = ROOT / "docs" / "phase4g-income-pool-report.md"
LEDGER = ROOT / "data" / "ledger" / "income-pool.jsonl"

STATUS_LABELS = {"in_pool": "入池", "pending": "待定", "out_of_scope": "范围外"}
OWNERSHIP_CATEGORIES = ("央企", "地方国企", "民企", "外资", "其他")


def _now() -> datetime:
    return datetime.now(TZ)


def _pool_hash(codes: list[str]) -> str:
    digest = hashlib.sha256(",".join(sorted(codes)).encode("utf-8")).hexdigest()
    return "iph-" + digest[:16]


def _record_id(record: dict) -> str:
    body = {key: value for key, value in record.items() if key not in ("record_id", "recorded_at")}
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return "ipr-" + digest[:16]


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
    print(f"稳定收息型账本 {len(rows)} 条，0 问题")
    return 0


def _governance_coverage(companies_by_code: dict, candidates: list[dict]) -> dict:
    total = len(candidates)
    return {
        key: {
            "total": total,
            "available": sum(
                1
                for item in candidates
                if getattr(companies_by_code.get(item["code"]), flag, False)
            ),
            "ratio": (
                sum(1 for item in candidates if getattr(companies_by_code.get(item["code"]), flag, False))
                / total
                if total
                else 0.0
            ),
            "note": note,
        }
        for key, flag, note in (
            ("audit", "audit_available", "东方财富 F10 OPINION_TYPE；同 v1.2"),
            ("penalty", "penalty_available", "公告检索 + 主体判定；同 v1.2"),
            ("pledge", "pledge_available", "定期报告股东表；无控股股东按第一大股东口径；同 v1.2"),
        )
    }


def _dividend_coverage(snapshot: dict, companies: list) -> dict:
    entries = [
        entry
        for entry in snapshot.get("tables") or []
        if entry.get("kind") == "dividends"
        and str(entry.get("report_date") or "")[:4] in {str(year) for year in range(2016, 2021)}
    ]
    periods = {str(entry.get("report_date") or "") for entry in entries if entry.get("report_date")}
    rows = sum(int(entry.get("rows") or 0) for entry in entries)
    years = []
    for year in range(2016, 2021):
        evaluated = [
            company
            for company in companies
            if not company.is_st and not company.financial_excluded and year in company.expected_years
        ]
        with_dividend = sum(
            1 for company in evaluated if float(company.dividends.get(year) or 0.0) > 0
        )
        years.append(
            {
                "year": year,
                "evaluated": len(evaluated),
                "with_dividend": with_dividend,
                "without_dividend": len(evaluated) - with_dividend,
            }
        )
    invalid_rows = 0
    for path in sorted(DIVIDEND_DIR.glob("dividends-*.csv.gz")):
        year = path.name.split("dividends-")[1][:4]
        if year not in {str(item) for item in range(2016, 2021)}:
            continue
        for row in read_table(path):
            try:
                float(row.get("PRETAX_BONUS_RMB"))
            except (TypeError, ValueError):
                invalid_rows += 1
    return {
        "periods_expected": 10,
        "periods_available": len(periods),
        "rows": rows,
        "invalid_pretax_rows": invalid_rows,
        "years": years,
    }


def _both_pools(income_codes: set[str], companies_by_code: dict) -> list[dict]:
    quality = json.loads(QUALITY_REPORT.read_text(encoding="utf-8"))
    quality_codes = {item["code"] for item in quality.get("in_pool") or []}
    rows = []
    for code in sorted(income_codes & quality_codes):
        company = companies_by_code.get(code)
        rows.append(
            {
                "code": code,
                "name": company.name if company else "",
                "industry": company.industry if company else None,
            }
        )
    return rows


def _ownership_distribution(
    companies_by_code: dict, quality_codes: set[str], income_codes: set[str]
) -> dict:
    def counts(codes: set[str]) -> dict:
        result = {}
        marked = 0
        for code in codes:
            ownership = companies_by_code.get(code).ownership if companies_by_code.get(code) else None
            if ownership in OWNERSHIP_CATEGORIES:
                result[ownership] = result.get(ownership, 0) + 1
                marked += 1
            else:
                result["未标记"] = result.get("未标记", 0) + 1
        return result

    quality_counts = counts(quality_codes)
    income_counts = counts(income_codes)
    return {
        "quality": quality_counts,
        "income": income_counts,
        "quality_total": len(quality_codes),
        "income_total": len(income_codes),
        "quality_marked": sum(quality_counts.get(item, 0) for item in OWNERSHIP_CATEGORIES),
        "income_marked": sum(income_counts.get(item, 0) for item in OWNERSHIP_CATEGORIES),
    }


def _show(company, result) -> None:
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
    print(f"所有制：{company.ownership or '未标记'}｜{company.ownership_holder or '—'}｜{company.ownership_basis or ''}")
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
        print(f"  [{flag}] {item.date} {item.event_type}：{item.subject or '—'}（{item.subject_role or '—'}）；{item.title}")
    print(
        "- 质押：" + (company.controlling_shareholder or "—")
        + f"；口径：{company.pledge_subject_role or '控股股东'}"
        + "；比例：" + (
            f"{company.controlling_pledge_ratio:.2%}" if company.controlling_pledge_ratio is not None else "不可得"
        )
    )
    print()
    for condition in result.conditions:
        print(f"- {condition.label}：{condition.status}｜{condition.reason or ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="不写账本")
    parser.add_argument("--no-ledger", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--show", help="从固定快照独立复算并打印一只股票的成绩单")
    args = parser.parse_args()
    if args.check:
        return check_ledger()

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    snapshot_path = INCOME_DIR / manifest["current"]
    snapshot, companies, _stats = load_snapshot_dir(snapshot_path)
    policy = load_policy(POLICY_PATH)
    if args.show:
        code = str(args.show).zfill(6)
        company = next((item for item in companies if item.code == code), None)
        if company is None:
            print(f"快照中没有 {code}")
            return 1
        _show(company, evaluate_company(company, policy))
        return 0
    run = evaluate_pool(
        companies, policy, as_of=snapshot["as_of"], snapshot_id=snapshot["snapshot_id"]
    )
    report = build_report(run, policy, snapshot)
    by_code = {company.code: company for company in companies}
    candidates = json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))["candidates"]
    report["governance_coverage"] = _governance_coverage(by_code, candidates)
    report["dividend_coverage"] = _dividend_coverage(snapshot, companies)
    income_codes = {item.code for item in run.in_pool}
    quality_codes = {
        item["code"]
        for item in json.loads(QUALITY_REPORT.read_text(encoding="utf-8")).get("in_pool") or []
    }
    report["both_pools"] = _both_pools(income_codes, by_code)
    report["ownership_distribution"] = _ownership_distribution(by_code, quality_codes, income_codes)
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
