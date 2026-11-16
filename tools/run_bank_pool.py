#!/usr/bin/env python
"""运行银行规则（草案）并写报告与只追加账本。

用法：
    .venv/bin/python tools/run_bank_pool.py --dry-run
    .venv/bin/python tools/run_bank_pool.py
    .venv/bin/python tools/run_bank_pool.py --check
    .venv/bin/python tools/run_bank_pool.py --show 600036
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

from engine.bank.evaluator import BANK_TEST_CODES, evaluate_company, evaluate_pool  # noqa: E402
from engine.bank.report import build_report, render_markdown  # noqa: E402
from engine.quality.policy import load_policy  # noqa: E402
from engine.quality.snapshot import load_snapshot_dir  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
POLICY_PATH = ROOT / "data" / "policies" / "bank-pool-v1.json"
BANK_DIR = ROOT / "data" / "bank-pool"
MANIFEST_PATH = BANK_DIR / "manifest.json"
REPORT_JSON = BANK_DIR / "report.json"
REPORT_MD = ROOT / "docs" / "archive" / "phase4h-bank-pool-report.md"
REHEARSAL_JSON = BANK_DIR / "valuation-rehearsal.json"
LEDGER = ROOT / "data" / "ledger" / "bank-pool.jsonl"

STATUS_LABELS = {"in_pool": "入池", "pending": "待定", "out_of_scope": "范围外"}
OWNERSHIP_CATEGORIES = ("央企", "地方国企", "民企", "外资", "其他")


def _now() -> datetime:
    return datetime.now(TZ)


def _pool_hash(codes: list[str]) -> str:
    digest = hashlib.sha256(",".join(sorted(codes)).encode("utf-8")).hexdigest()
    return "bph-" + digest[:16]


def _record_id(record: dict) -> str:
    body = {key: value for key, value in record.items() if key not in ("record_id", "recorded_at")}
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return "bpr-" + digest[:16]


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
        if row.get("record_type") == "exclusion":
            continue  # 红线出池记录由监测账本校验，不参与名单池哈希
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
    print(f"银行账本 {len(rows)} 条，0 问题")
    return 0


def _number(row, key):
    value = row.get(key)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _data_coverage(companies, policy) -> dict:
    banks = [
        company
        for company in companies
        if "银行" in f"{company.industry or ''} {company.financial_exclusion_reason or ''}"
        and not company.is_st
        and len(company.window_years) >= int(policy.value("min_fiscal_years"))
    ]
    total = len(banks)
    result = {}
    for key, label in (
        ("roe", "10 年 ROE（加权，年报）"),
        ("npl", "最新不良贷款率"),
        ("npl_change", "不良率 5 年变化"),
        ("provision", "最新拨备覆盖率"),
        ("core_tier1", "最新核心一级资本充足率"),
        ("nim", "最新净息差（只展示）"),
        ("dividend", "2016–2025 现金分红"),
        ("audit", "审计意见（2021–2025）"),
        ("penalty", "处罚/立案/公开谴责"),
        ("pledge", "控股股东/第一大股东质押"),
    ):
        available = 0
        for company in banks:
            rows = [dict(item) for item in company.bank_reports]
            annual = {
                int(str(row.get("report_date") or "")[:4]): row
                for row in rows
                if str(row.get("report_type") or "") == "年报"
            }
            latest = max(rows, key=lambda row: str(row.get("report_date") or "")) if rows else None
            if key == "roe":
                ok = bool(company.window_years) and all(
                    annual.get(year) is not None and _number(annual[year], "roe_weighted") is not None
                    for year in company.window_years
                )
            elif key == "npl":
                ok = any(_number(row, "npl_ratio") is not None for row in rows)
            elif key == "npl_change":
                ok = False
                npl_rows = [row for row in rows if _number(row, "npl_ratio") is not None]
                if npl_rows:
                    latest_npl_row = max(npl_rows, key=lambda row: str(row.get("report_date") or ""))
                    day = str(latest_npl_row.get("report_date") or "")[:10]
                    try:
                        year = int(day[:4])
                        target = f"{year - 5:04d}{day[4:]}"
                    except ValueError:
                        target = ""
                    prior = next(
                        (row for row in rows if str(row.get("report_date") or "")[:10] == target),
                        None,
                    )
                    ok = prior is not None and _number(prior, "npl_ratio") is not None
            elif key == "provision":
                ok = any(_number(row, "provision_coverage") is not None for row in rows)
            elif key == "core_tier1":
                ok = any(_number(row, "core_tier1_ratio") is not None for row in rows)
            elif key == "nim":
                ok = any(_number(row, "net_interest_margin") is not None for row in rows)
            elif key == "dividend":
                ok = all(
                    year in company.dividend_available_years for year in company.window_years
                )
            elif key == "audit":
                ok = bool(company.audit_available)
            elif key == "penalty":
                ok = bool(company.penalty_available)
            elif key == "pledge":
                ok = bool(company.pledge_available)
            else:
                ok = False
            if ok:
                available += 1
        result[key] = {
            "total": total,
            "available": available,
            "ratio": available / total if total else 0.0,
            "note": "东方财富 F10 银行主要指标" if key not in ("div",) else "",
        }
    result["roe"]["note"] = "ROEJQ 加权 ROE，2016–2025 年报"
    result["npl"]["note"] = "NONPERLOAN；最新报告期"
    result["npl_change"]["note"] = "最新报告期 − 5 年前同一报告期"
    result["provision"]["note"] = "BLDKBBL；最新报告期"
    result["core_tier1"]["note"] = "HXYJBCZL；最新报告期"
    result["nim"]["note"] = "NET_INTEREST_MARGIN；只展示"
    result["dividend"]["note"] = "RPT_SHAREBONUS_DET 2016–2025"
    result["audit"]["note"] = "OPINION_TYPE，companyType=3（银行报表）"
    result["penalty"]["note"] = "公告检索 + 主体判定；同 v1.2"
    result["pledge"]["note"] = "定期报告股东表；同 v1.2"
    return result


def _ownership_distribution(companies_by_code, codes) -> dict:
    counts: dict[str, int] = {}
    marked = 0
    for code in codes:
        company = companies_by_code.get(code)
        ownership = company.ownership if company else None
        if ownership in OWNERSHIP_CATEGORIES:
            counts[ownership] = counts.get(ownership, 0) + 1
            marked += 1
        else:
            counts["未标记"] = counts.get("未标记", 0) + 1
    return {"total": len(codes), "marked": marked, **counts}


def _rehearsal_summary() -> dict:
    if not REHEARSAL_JSON.exists():
        return {}
    data = json.loads(REHEARSAL_JSON.read_text(encoding="utf-8"))
    rows = data.get("banks") or []
    summary = {"total": len(rows), "grade_b": 0, "grade_c": 0, "insufficient": 0}
    for row in rows:
        grade = row.get("grade")
        if grade == "B":
            summary["grade_b"] += 1
        elif grade == "C":
            summary["grade_c"] += 1
        else:
            summary["insufficient"] += 1
    return summary


def _show(company, result) -> None:
    print(f"{result.code} {result.name}｜{result.board}｜{result.industry or '—'}｜{result.status}")
    if result.reason:
        print(f"说明：{result.reason}")
    rows = sorted(company.bank_reports, key=lambda row: str(row.get("report_date") or ""))
    print("报告期      类型   ROE(加权)  不良率  拨备覆盖率  核心一级  净息差  归母净利润")
    for row in rows:
        date = str(row.get("report_date") or "")[:10]
        if date[:4] < "2016":
            continue
        print(
            f"{date}  {str(row.get('report_type') or '—'):<4}  "
            f"{_number(row, 'roe_weighted') or 0:>8.2f}  "
            f"{_number(row, 'npl_ratio') or 0:>7.2f}  "
            f"{_number(row, 'provision_coverage') or 0:>10.2f}  "
            f"{_number(row, 'core_tier1_ratio') or 0:>8.2f}  "
            f"{_number(row, 'net_interest_margin') or 0:>6.2f}  "
            f"{_number(row, 'parent_netprofit') or 0:>16,.0f}"
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
    parser.add_argument("--show")
    parser.add_argument("--policy", default=str(POLICY_PATH))
    args = parser.parse_args()
    if args.check:
        return check_ledger()

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    snapshot, companies, _stats = load_snapshot_dir(BANK_DIR / manifest["current"])
    policy = load_policy(Path(args.policy))
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
    report["data_coverage"] = _data_coverage(companies, policy)
    report["ownership_distribution"] = _ownership_distribution(
        by_code, {item.code for item in run.in_pool}
    )
    report["valuation_rehearsal_summary"] = _rehearsal_summary()
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
