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
POLICY_PATH = ROOT / "data" / "policies" / "quality-pool-v1.1-draft.json"
POOL_DIR = ROOT / "data" / "quality-pool"
BASELINE_REPORT = POOL_DIR / "report-4d.json"
MANIFEST_PATH = POOL_DIR / "manifest.json"
REPORT_JSON = POOL_DIR / "report.json"
REPORT_MD = ROOT / "docs" / "phase4e-quality-pool-report.md"
LEDGER = ROOT / "data" / "ledger" / "quality-pool.jsonl"


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


def _governance_evidence(company, condition_id: str) -> str:
    if condition_id == "audit_opinion":
        bad = [
            f"{year} {opinion}"
            for year, opinion in sorted(company.audit_opinions.items())
            if opinion != "标准无保留意见"
        ]
        return "；".join(bad) or "审计意见缺失"
    if condition_id == "no_penalty":
        items = [item for item in company.penalties if item.counted]
        return "；".join(
            f"{item.date} {item.title}"
            + (f"（{item.doc_no}）" if item.doc_no else (f"（{item.url}）" if item.url else ""))
            for item in items
        ) or "处罚数据缺失"
    if condition_id == "pledge_ratio":
        ratio = company.controlling_pledge_ratio
        if ratio is not None:
            return f"{ratio:.2%}（{company.pledge_report_date or '最新定期报告'}）"
        note = company.pledge_note or "质押数据缺失"
        labels = {
            "no_periodic_pledge_table": "最新定期报告股东表未取得（PDF 接口或解析失败）",
            "no_controller_with_company_pledge": "无控股股东，且公司整体质押 >0，控股股东口径不适用",
            "pledge_unknown": "质押状态未知",
            "no_controller": "无控股股东",
        }
        return labels.get(note, note)
    return ""


def _governance_summary(report: dict, run, companies) -> None:
    candidates = set()
    baseline_pool = set()
    if BASELINE_REPORT.exists():
        baseline = json.loads(BASELINE_REPORT.read_text(encoding="utf-8"))
        baseline_pool = {item["code"] for item in baseline.get("in_pool") or []}
        candidates = baseline_pool | {item["code"] for item in baseline.get("near_misses") or []}
    by_code = {company.code: company for company in companies}
    if not candidates:
        candidates = set(by_code)
    total = len(candidates)
    report["governance_coverage"] = {
        key: {
            "total": total,
            "available": sum(1 for code in candidates if getattr(by_code.get(code), flag, False)),
            "ratio": (
                sum(1 for code in candidates if getattr(by_code.get(code), flag, False)) / total
                if total
                else 0.0
            ),
            "note": note,
        }
        for key, flag, note in (
            ("audit", "audit_available", "东方财富 F10 个股资产负债表 OPINION_TYPE"),
            ("penalty", "penalty_available", "东方财富公告检索：立案/行政处罚/公开谴责/纪律处分"),
            ("pledge", "pledge_available", "公司整体质押为 0 直接记 0；否则最新定期报告前十名股东表"),
        )
    }
    results = {item.code: item for item in run.results}
    removed = []
    for code in sorted(baseline_pool):
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
        result = evaluate_company(company, policy)
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
        print("- 处罚/立案：" + ("；".join(
            f"{item.date} {item.event_type} {item.title}"
            for item in company.penalties
        ) or "无（公告检索完成）"))
        print(
            "- 控股股东：" + (company.controlling_shareholder or "无")
            + "；质押比例：" + (
                f"{company.controlling_pledge_ratio:.2%}（{company.pledge_report_date or '—'}）"
                if company.controlling_pledge_ratio is not None else "不可得"
            )
        )
        print()
        for condition in result.conditions:
            print(f"- {condition.label}：{condition.status}｜{condition.reason or ''}")
        return 0
    run = evaluate_pool(
        companies, policy, as_of=snapshot["as_of"], snapshot_id=snapshot["snapshot_id"]
    )
    report = build_report(run, policy, snapshot)
    _governance_summary(report, run, companies)
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
