#!/usr/bin/env python
"""生成银行估值预演的输入表（不改银行池快照/报告/账本）。

输入：银行池快照（银行指标、分红、总股本）、东方财富行情（现价与日期）。
输出：`data/bank-pool/valuation-inputs.csv.gz` + 元数据；预演工具只读该表与政策。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.quality.policy import load_policy  # noqa: E402
from engine.quality.snapshot import load_snapshot_dir  # noqa: E402
from engine.quality.tables import read_table, write_table  # noqa: E402
from engine.bank.evaluator import evaluate_pool  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
BANK_DIR = ROOT / "data" / "bank-pool"
BANK_POLICY = ROOT / "data" / "policies" / "bank-pool-v1-draft.json"
RAW_DIR = ROOT / "data" / "cache" / "bank-pool" / "raw"
OUTPUT = BANK_DIR / "valuation-inputs.csv.gz"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

COLUMNS = [
    "code", "name", "in_scope", "base_year", "base_net_profit", "base_revenue",
    "revenue_2020", "revenue_2022", "latest_revenue_yoy", "roe_latest", "roe_5y",
    "net_margin_base", "net_margin_min_5y", "payout_3y", "shares", "price", "price_date",
    "cet1_ratio", "cet1_date", "npl_ratio", "npl_date", "provision_coverage", "provision_date",
    "loss_years_7", "data_complete", "notes",
]


def _num(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _annual(rows: list[dict]) -> dict[int, dict]:
    output = {}
    for row in rows:
        if row.get("REPORT_TYPE") != "年报":
            continue
        day = str(row.get("REPORT_DATE") or "")[:10]
        if day[4:10] == "-12-31":
            output[int(day[:4])] = row
    return output


def _latest_with(rows: list[dict], key: str) -> dict | None:
    usable = [row for row in rows if _num(row.get(key)) is not None]
    usable.sort(key=lambda row: str(row.get("REPORT_DATE") or ""))
    return usable[-1] if usable else None


def _price(code: str) -> tuple[float | None, str | None]:
    secid = ("1." if code.startswith("6") else "0.") + code
    try:
        response = requests.get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={"secid": secid, "fields": "f43,f58,f86"},
            headers=UA,
            timeout=20,
        )
        data = (response.json() or {}).get("data") or {}
        raw = data.get("f43")
        stamp = data.get("f86")
        if raw is None or not stamp:
            return None, None
        price = float(raw) / 100.0
        day = datetime.fromtimestamp(int(stamp), TZ).date().isoformat()
        return price, day
    except Exception:  # noqa: BLE001
        return None, None


def _dividends_by_year(snapshot_dir: Path) -> dict[str, dict[int, float]]:
    output: dict[str, dict[int, float]] = {}
    for path in sorted(snapshot_dir.glob("dividends-*.csv.gz")):
        for row in read_table(path):
            code = str(row.get("SECURITY_CODE") or "").zfill(6)
            year = str(row.get("REPORT_DATE") or "")[:4]
            if not code or not year.isdigit():
                continue
            progress = str(row.get("ASSIGN_PROGRESS") or "")
            profile = str(row.get("IMPL_PLAN_PROFILE") or "")
            if "取消" in progress or "终止" in progress:
                continue
            if "特别" in profile or "特殊" in profile:
                continue
            per_10 = _num(row.get("PRETAX_BONUS_RMB"))
            shares = _num(row.get("TOTAL_SHARES"))
            if per_10 is None or shares is None:
                continue
            output.setdefault(code, {}).setdefault(int(year), 0.0)
            output[code][int(year)] += per_10 / 10.0 * shares
    return output


def build_inputs() -> list[dict]:
    manifest = json.loads((BANK_DIR / "manifest.json").read_text(encoding="utf-8"))
    snapshot_dir = BANK_DIR / manifest["current"]
    snapshot, companies, _stats = load_snapshot_dir(snapshot_dir)
    bank_results = evaluate_pool(
        companies,
        load_policy(BANK_POLICY),
        as_of=snapshot["as_of"],
        snapshot_id=snapshot["snapshot_id"],
        include_sensitivity=False,
    )
    in_scope = {item.code for item in bank_results.results if item.status != "out_of_scope"}
    profiles = {
        str(row["code"]).zfill(6): row
        for row in read_table(snapshot_dir / "bank-profile.csv.gz")
    }
    dividends = _dividends_by_year(snapshot_dir)
    rows = []
    for code, profile in sorted(profiles.items()):
        raw_path = RAW_DIR / f"{code}.json"
        if not raw_path.exists():
            raise RuntimeError(f"缺原始银行指标缓存：{raw_path}")
        raw_rows = json.loads(raw_path.read_text(encoding="utf-8"))["rows"]
        annual = _annual(raw_rows)
        base = annual.get(2025)
        if base is None:
            raise RuntimeError(f"{code} 缺 2025 年报银行指标")
        base_profit = _num(base.get("PARENTNETPROFIT"))
        base_revenue = _num(base.get("TOTALOPERATEREVE"))
        revenue_2020 = _num((annual.get(2020) or {}).get("TOTALOPERATEREVE"))
        revenue_2022 = _num((annual.get(2022) or {}).get("TOTALOPERATEREVE"))
        latest = max(raw_rows, key=lambda row: str(row.get("REPORT_DATE") or ""))
        latest_yoy = _num(latest.get("TOTALOPERATEREVETZ"))
        if latest_yoy is None:
            latest_yoy = None  # 由预演工具回退到 5 年复合增速
        else:
            latest_yoy = latest_yoy / 100.0
        roe_latest = _num(base.get("ROEJQ"))
        roe_latest = roe_latest / 100.0 if roe_latest is not None else None
        roe_values = [
            _num(annual.get(year, {}).get("ROEJQ"))
            for year in range(2021, 2026)
        ]
        roe_values = [value for value in roe_values if value is not None]
        roe_5y = sum(roe_values) / len(roe_values) / 100.0 if len(roe_values) == 5 else None
        margins = []
        for year in range(2021, 2026):
            row = annual.get(year)
            if row is None:
                continue
            profit = _num(row.get("PARENTNETPROFIT"))
            revenue = _num(row.get("TOTALOPERATEREVE"))
            if profit is None or revenue in (None, 0):
                continue
            margins.append(profit / revenue)
        net_margin_base = (
            base_profit / base_revenue if base_profit is not None and base_revenue else None
        )
        net_margin_min_5y = min(margins) if margins else None
        payout_values = []
        by_year = dividends.get(code, {})
        for year in (2023, 2024, 2025):
            annual_row = annual.get(year)
            profit = _num(annual_row.get("PARENTNETPROFIT")) if annual_row else None
            amount = by_year.get(year, 0.0)
            if profit is None or profit <= 0:
                continue
            payout_values.append(amount / profit)
        payout_3y = sum(payout_values) / len(payout_values) if len(payout_values) == 3 else None
        shares = _num(profile.get("total_shares"))
        price, price_date = _price(code)
        cet1 = _latest_with(raw_rows, "HXYJBCZL")
        npl = _latest_with(raw_rows, "NONPERLOAN")
        provision = _latest_with(raw_rows, "BLDKBBL")
        loss_years_7 = 0
        loss_available = True
        for year in range(2019, 2026):
            row = annual.get(year)
            profit = _num(row.get("PARENTNETPROFIT")) if row else None
            if profit is None:
                loss_available = False
                break
            if profit < 0:
                loss_years_7 += 1
        missing = []
        for label, value in (
            ("2025 归母净利润", base_profit),
            ("2025 营业收入", base_revenue),
            ("2020 营业收入", revenue_2020),
            ("2022 营业收入", revenue_2022),
            ("2025 ROE", roe_latest),
            ("近 5 年 ROE", roe_5y),
            ("2025 归母净利率", net_margin_base),
            ("近 5 年最低归母净利率", net_margin_min_5y),
            ("近 3 年平均派息率", payout_3y),
            ("总股本", shares),
            ("现价", price),
            ("核心一级资本充足率", _num(cet1.get("HXYJBCZL")) if cet1 else None),
            ("不良贷款率", _num(npl.get("NONPERLOAN")) if npl else None),
            ("拨备覆盖率", _num(provision.get("BLDKBBL")) if provision else None),
            ("近 7 年亏损年数", None if not loss_available else 0),
        ):
            if value is None:
                missing.append(label)
        rows.append(
            {
                "code": code,
                "name": profile.get("name") or "",
                "in_scope": code in in_scope,
                "base_year": 2025,
                "base_net_profit": base_profit,
                "base_revenue": base_revenue,
                "revenue_2020": revenue_2020,
                "revenue_2022": revenue_2022,
                "latest_revenue_yoy": latest_yoy,
                "roe_latest": roe_latest,
                "roe_5y": roe_5y,
                "net_margin_base": net_margin_base,
                "net_margin_min_5y": net_margin_min_5y,
                "payout_3y": payout_3y,
                "shares": shares,
                "price": price,
                "price_date": price_date,
                "cet1_ratio": _num(cet1.get("HXYJBCZL")) if cet1 else None,
                "cet1_date": str(cet1.get("REPORT_DATE"))[:10] if cet1 else None,
                "npl_ratio": _num(npl.get("NONPERLOAN")) if npl else None,
                "npl_date": str(npl.get("REPORT_DATE"))[:10] if npl else None,
                "provision_coverage": _num(provision.get("BLDKBBL")) if provision else None,
                "provision_date": str(provision.get("REPORT_DATE"))[:10] if provision else None,
                "loss_years_7": loss_years_7 if loss_available else None,
                "data_complete": not missing,
                "notes": "；".join(missing),
            }
        )
    write_table(
        OUTPUT,
        rows,
        COLUMNS,
        metadata={
            "kind": "bank_valuation_inputs",
            "source_id": "mixed",
            "publisher": "东方财富 / 本项目银行快照",
            "interface": "RPT_F10_FINANCE_MAINFINADATA 银行指标 + 分红表 + 总股本 + 行情",
            "url": "https://push2.eastmoney.com/api/qt/stock/get",
            "definition_note": "银行估值预演输入：营收/净利润/ROE/净利率/派息率来自银行指标与分红表；现价来自东方财富行情；只读，不进入银行池快照。",
        },
    )
    print(f"估值输入 {len(rows)} 行 → {OUTPUT}")
    print(f"评价范围内 {sum(1 for row in rows if row['in_scope'])} 家；数据完整 {sum(1 for row in rows if row['data_complete'])} 家")
    return rows


if __name__ == "__main__":
    build_inputs()
