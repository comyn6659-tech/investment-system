#!/usr/bin/env python
"""银行数据补齐、治理复用、所有制标记与固定快照构建。

步骤（可断点续跑）：
    .venv/bin/python tools/build_bank_pool_data.py --fetch-bank
    .venv/bin/python tools/build_bank_pool_data.py --fetch-governance --workers 6
    .venv/bin/python tools/build_bank_pool_data.py --fetch-ownership
    .venv/bin/python tools/build_bank_pool_data.py --build-tables
    .venv/bin/python tools/build_bank_pool_data.py --build-snapshot
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.quality.snapshot import build_snapshot_dir, load_snapshot_dir  # noqa: E402
from engine.quality.tables import read_table, write_table  # noqa: E402
from tools.build_governance_data import (  # noqa: E402
    CACHE as GOVERNANCE_CACHE,
    TABLE_DIR as GOVERNANCE_TABLE_DIR,
    _get,
    build_tables as build_governance_tables,
    fetch_company,
    now,
)
from tools.build_income_pool_data import (  # noqa: E402
    _fallback_holder,
    _manual_ownership_review,
    _orginfo,
    classify_ownership,
)

TZ = ZoneInfo("Asia/Singapore")
POOL_4F = ROOT / "data" / "quality-pool" / "snapshot-qps-3ebbd24be612bf7e"
BANK_DIR = ROOT / "data" / "bank-pool"
TABLE_DIR = ROOT / "data" / "cache" / "bank-pool" / "tables"
RAW_DIR = ROOT / "data" / "cache" / "bank-pool" / "raw"
DIVIDEND_DIR = ROOT / "data" / "cache" / "quality-pool" / "tables" / "dividends"
MANIFEST_PATH = BANK_DIR / "manifest.json"
OWNERSHIP_TABLE = TABLE_DIR / "ownership.csv.gz"

BANK_INDICATOR_COLUMNS = [
    "code", "report_date", "report_type", "roe_weighted", "parent_netprofit",
    "npl_ratio", "provision_coverage", "core_tier1_ratio", "tier1_ratio",
    "capital_adequacy_ratio", "net_interest_margin", "source_id", "source_url", "fetched_at",
]
BANK_PROFILE_COLUMNS = [
    "code", "name", "total_shares", "total_assets_2025", "source_id", "source_url", "fetched_at",
]
OWNERSHIP_COLUMNS = [
    "code", "name", "ownership", "holder", "holder_role", "basis", "source_id", "source_url", "fetched_at",
]


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def bank_rows() -> list[dict]:
    industry = {
        str(row["code"]).zfill(6): row.get("industry")
        for row in read_table(POOL_4F / "industry.csv.gz")
    }
    universe = {
        str(row["code"]).zfill(6): row
        for row in read_table(POOL_4F / "universe.csv.gz")
    }
    codes = sorted(code for code, name in industry.items() if "银行" in str(name or ""))
    return [
        {
            "code": code,
            "name": universe.get(code, {}).get("name") or "",
            "industry": industry.get(code),
        }
        for code in codes
    ]


def _symbol(code: str) -> str:
    return ("SH" if code.startswith("6") else "SZ") + code


def fetch_bank_indicators(code: str) -> list[dict]:
    path = RAW_DIR / f"{code}.json"
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload["rows"]
    response = _get(
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        params={
            "reportName": "RPT_F10_FINANCE_MAINFINADATA",
            "columns": "ALL",
            "pageSize": "100",
            "pageNumber": "1",
            "source": "WEB",
            "client": "WEB",
            "filter": f'(SECURITY_CODE="{code}")(REPORT_DATE>=\'2016-01-01\')',
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
        },
        timeout=40,
    )
    rows = ((response.json().get("result") or {}).get("data")) or []
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"code": code, "fetched_at": _now(), "rows": rows},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return rows


def fetch_profile(code: str, name: str, total_assets: float | None) -> dict:
    response = _get(
        "https://emweb.securities.eastmoney.com/PC_HSF10/CapitalStockStructure/PageAjax",
        params={"code": _symbol(code)},
        timeout=30,
    )
    gbjg = response.json().get("gbjg") or []
    total_shares = float(gbjg[0]["TOTAL_SHARES"]) if gbjg and gbjg[0].get("TOTAL_SHARES") else None
    return {
        "code": code,
        "name": name,
        "total_shares": total_shares,
        "total_assets_2025": total_assets,
        "source_id": "eastmoney.f10.capital_structure",
        "source_url": "https://emweb.securities.eastmoney.com/PC_HSF10/CapitalStockStructure/PageAjax",
        "fetched_at": _now(),
    }


def fetch_bank(workers: int = 6) -> list[dict]:
    banks = bank_rows()
    balance = {
        str(row["SECURITY_CODE"]).zfill(6): row
        for row in read_table(POOL_4F / "balance-2025.csv.gz")
    }
    profiles: list[dict] = []
    indicator_rows: list[dict] = []
    lock_rows: list[dict] = []

    def work(item: dict) -> tuple[list[dict], dict]:
        code = item["code"]
        raws = fetch_bank_indicators(code)
        converted = []
        for row in raws:
            converted.append(
                {
                    "code": code,
                    "report_date": str(row.get("REPORT_DATE") or "")[:10],
                    "report_type": row.get("REPORT_TYPE"),
                    "roe_weighted": row.get("ROEJQ"),
                    "parent_netprofit": row.get("PARENTNETPROFIT"),
                    "npl_ratio": row.get("NONPERLOAN"),
                    "provision_coverage": row.get("BLDKBBL"),
                    "core_tier1_ratio": row.get("HXYJBCZL"),
                    "tier1_ratio": row.get("FIRST_ADEQUACY_RATIO"),
                    "capital_adequacy_ratio": row.get("NEWCAPITALADER"),
                    "net_interest_margin": row.get("NET_INTEREST_MARGIN"),
                    "source_id": "eastmoney.f10.bank_indicators",
                    "source_url": "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_FINANCE_MAINFINADATA",
                    "fetched_at": _now(),
                }
            )
        total_assets = None
        if balance.get(code) and balance[code].get("TOTAL_ASSETS"):
            try:
                total_assets = float(balance[code]["TOTAL_ASSETS"])
            except (TypeError, ValueError):
                total_assets = None
        if total_assets is None:
            try:
                response = _get(
                    "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/zcfzbAjaxNew",
                    params={
                        "companyType": "3",
                        "reportDateType": "0",
                        "reportType": "1",
                        "dates": "2025-12-31",
                        "code": _symbol(code),
                    },
                    timeout=30,
                )
                rows = response.json().get("data") or []
                if rows and rows[0].get("TOTAL_ASSETS"):
                    total_assets = float(rows[0]["TOTAL_ASSETS"])
            except Exception:  # noqa: BLE001
                total_assets = None
        profile = fetch_profile(code, item["name"], total_assets)
        return converted, profile

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(work, item): item for item in banks}
        for future in as_completed(futures):
            item = futures[future]
            try:
                rows, profile = future.result()
                indicator_rows.extend(rows)
                profiles.append(profile)
                print(f"{item['code']} {item['name']} rows={len(rows)} shares={profile['total_shares']}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"{item['code']} {item['name']} FAILED {exc}", flush=True)
    indicator_rows.sort(key=lambda row: (row["code"], row["report_date"]))
    profiles.sort(key=lambda row: row["code"])
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    write_table(
        TABLE_DIR / "bank-indicators.csv.gz",
        indicator_rows,
        BANK_INDICATOR_COLUMNS,
        metadata={
            "kind": "bank_indicators",
            "source_id": "eastmoney.f10.bank_indicators",
            "publisher": "东方财富",
            "interface": "RPT_F10_FINANCE_MAINFINADATA 银行主要指标",
            "url": "https://datacenter-web.eastmoney.com/api/data/v1/get",
            "definition_note": "2016 年起季度与年报：ROEJQ 加权 ROE、NONPERLOAN 不良率、BLDKBBL 拨备覆盖率、HXYJBCZL 核心一级资本充足率、NET_INTEREST_MARGIN 净息差（只展示）。",
        },
    )
    write_table(
        TABLE_DIR / "bank-profile.csv.gz",
        profiles,
        BANK_PROFILE_COLUMNS,
        metadata={
            "kind": "bank_profile",
            "source_id": "eastmoney.f10.capital_structure",
            "publisher": "东方财富",
            "interface": "CapitalStockStructure TOTAL_SHARES + 2025 资产负债表 TOTAL_ASSETS",
            "url": "https://emweb.securities.eastmoney.com/PC_HSF10/CapitalStockStructure/PageAjax",
            "definition_note": "总股本用于预演估值换算每股；总资产只用于预演中风险加权资产的研究假设。",
        },
    )
    print(f"银行 {len(profiles)} 家，指标 {len(indicator_rows)} 行", flush=True)
    return indicator_rows


def _company_ratio_map() -> dict[str, float]:
    return {
        str(row["code"]).zfill(6): float(row["pledge_ratio"])
        for row in read_table(POOL_4F / "pledge.csv.gz")
        if row.get("pledge_ratio")
    }


def fetch_governance(workers: int = 6) -> None:
    banks = bank_rows()
    ratios = _company_ratio_map()
    pending = []
    for item in banks:
        path = GOVERNANCE_CACHE / f"{item['code']}.json"
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if cached.get("complete"):
                    continue
            except Exception:  # noqa: BLE001
                pass
        pending.append(item)
    print(f"银行 {len(banks)} 家，待补治理 {len(pending)} 家", flush=True)

    def work(item: dict) -> dict:
        result = fetch_company(item["code"], item["name"], "bank", ratios.get(item["code"]))
        path = GOVERNANCE_CACHE / f"{item['code']}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(work, item): item for item in pending}
        for future in as_completed(futures):
            item = futures[future]
            done += 1
            try:
                result = future.result()
                audit = (result.get("audit") or {}).get("available")
                pledge = (result.get("pledge") or {}).get("available")
                penalties = len((result.get("penalties") or {}).get("events") or [])
                print(f"[{done}/{len(pending)}] {item['code']} {item['name']} audit={audit} pledge={pledge} penalties={penalties}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[{done}/{len(pending)}] {item['code']} FAILED {exc}", flush=True)
    print("银行治理补查完成", flush=True)


def fetch_ownership() -> list[dict]:
    banks = bank_rows()
    overrides = _manual_ownership_review()
    rows = []
    for item in banks:
        code = item["code"]
        try:
            info = _orginfo(code)
        except Exception:  # noqa: BLE001
            info = {}
        holder = str(info.get("ACTUAL_HOLDER") or "").strip()
        role = "实际控制人"
        source_url = "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_BASIC_ORGINFO"
        if not holder or holder in ("无", "—", "-"):
            fallback = _fallback_holder(code)
            if fallback:
                holder = fallback
                role = "第一大股东（无实际控制人）"
                source_url = "东方财富 F10 股东表/治理质押主体"
        if code in overrides:
            node = overrides[code]
            ownership = str(node["ownership"])
            basis = str(node.get("basis") or "人工复核")
        else:
            ownership, basis = classify_ownership(holder)
        rows.append(
            {
                "code": code,
                "name": item["name"] or str(info.get("SECURITY_NAME_ABBR") or ""),
                "ownership": ownership,
                "holder": holder or None,
                "holder_role": role,
                "basis": basis,
                "source_id": "eastmoney.f10.ownership",
                "source_url": source_url,
                "fetched_at": _now(),
            }
        )
        time.sleep(0.1)
    OWNERSHIP_TABLE.parent.mkdir(parents=True, exist_ok=True)
    write_table(
        OWNERSHIP_TABLE,
        rows,
        OWNERSHIP_COLUMNS,
        metadata={
            "kind": "ownership",
            "source_id": "eastmoney.f10.ownership",
            "publisher": "东方财富 F10",
            "interface": "RPT_F10_BASIC_ORGINFO ACTUAL_HOLDER；无实际控制人时用第一大股东",
            "url": "https://datacenter-web.eastmoney.com/api/data/v1/get",
            "definition_note": "央企/地方国企/民企/外资/其他；只标记，不参与判定。",
        },
    )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["ownership"]] = counts.get(row["ownership"], 0) + 1
    print(f"银行所有制表 {len(rows)} 行", counts)
    return rows


def build_tables() -> None:
    for meta in build_governance_tables():
        print(meta["file"], meta["rows"], meta["size_bytes"])
    for name in ("bank-indicators.csv.gz", "bank-profile.csv.gz", "ownership.csv.gz"):
        if not (TABLE_DIR / name).exists():
            raise RuntimeError(f"缺少 {name}")


def build_snapshot() -> None:
    BANK_DIR.mkdir(parents=True, exist_ok=True)
    manifest_4f = json.loads((POOL_4F / "manifest.json").read_text(encoding="utf-8"))
    financial = [
        path
        for path in POOL_4F.glob("*.csv.gz")
        if not path.name.startswith("governance-")
    ]
    dividends = sorted(DIVIDEND_DIR.glob("dividends-*.csv.gz"))
    table_paths = (
        financial
        + dividends
        + [
            GOVERNANCE_TABLE_DIR / "governance-audit.csv.gz",
            GOVERNANCE_TABLE_DIR / "governance-penalty.csv.gz",
            GOVERNANCE_TABLE_DIR / "governance-pledge.csv.gz",
            GOVERNANCE_TABLE_DIR / "governance-coverage.csv.gz",
            TABLE_DIR / "ownership.csv.gz",
            TABLE_DIR / "bank-indicators.csv.gz",
            TABLE_DIR / "bank-profile.csv.gz",
        ]
    )
    sources = list(manifest_4f.get("sources") or []) + [
        {
            "id": "eastmoney.f10.bank_indicators",
            "publisher": "东方财富",
            "interface": "RPT_F10_FINANCE_MAINFINADATA 银行主要指标",
            "url": "https://datacenter-web.eastmoney.com/api/data/v1/get",
            "note": "2016 年起季度与年报；ROE、不良率、拨备覆盖率、核心一级资本充足率、净息差。",
        },
        {
            "id": "eastmoney.f10.capital_structure",
            "publisher": "东方财富",
            "interface": "CapitalStockStructure TOTAL_SHARES",
            "url": "https://emweb.securities.eastmoney.com/PC_HSF10/CapitalStockStructure/PageAjax",
            "note": "总股本用于预演估值换算每股。",
        },
        {
            "id": "eastmoney.f10.ownership",
            "publisher": "东方财富 F10",
            "interface": "RPT_F10_BASIC_ORGINFO ACTUAL_HOLDER；无实际控制人时用第一大股东",
            "url": "https://datacenter-web.eastmoney.com/api/data/v1/get",
            "note": "所有制只标记，不参与判定。",
        },
    ]
    target, manifest = build_snapshot_dir(
        table_paths,
        parent=BANK_DIR,
        as_of=manifest_4f["as_of"],
        window=manifest_4f["window"],
        scope=manifest_4f["scope"],
        sources=sources,
        untested_conditions=[],
        build_stats={"kind": "bank_pool", "banks": len(bank_rows()), "dividend_periods": len(dividends)},
    )
    (BANK_DIR / "manifest.json").write_text(
        json.dumps(
            {
                "current": target.name,
                "snapshot_id": manifest["snapshot_id"],
                "content_hash": manifest["content_hash"],
                "as_of": manifest["as_of"],
                "tables": len(manifest["tables"]),
                "total_bytes": manifest["total_bytes"],
                "written_at": _now(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"银行快照 {target.name}，{len(manifest['tables'])} 张表，{manifest['total_bytes']/1024/1024:.2f} MB")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-bank", action="store_true")
    parser.add_argument("--fetch-governance", action="store_true")
    parser.add_argument("--fetch-ownership", action="store_true")
    parser.add_argument("--build-tables", action="store_true")
    parser.add_argument("--build-snapshot", action="store_true")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    if args.fetch_bank:
        fetch_bank(args.workers)
    if args.fetch_governance:
        fetch_governance(args.workers)
    if args.fetch_ownership:
        fetch_ownership()
    if args.build_tables:
        build_tables()
    if args.build_snapshot:
        build_snapshot()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
