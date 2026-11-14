#!/usr/bin/env python
"""构建银行专用估值 v2 的输入表与历史估值位置（只生成已提交数据，不改池/账本）。

数据源（同源同口径）：
- 最新资产负债表：东方财富 F10 `zcfzbAjaxNew`（归母净资产、其他权益工具）；
- 每日估值：东方财富估值分析 `RPT_VALUEANALYSIS_DET`（收盘价、市净率、总股本）；
- 分红：东方财富 F10 分红送配（除权除息日、每 10 股派息）；
- 银行年度 ROE/NPL/拨备：银行池已提交快照 `bank-indicators.csv.gz`。

输出：
- `data/bank-pool/valuation-v2/inputs.csv.gz`（36 家范围内银行 + 42 家历史位置字段）
- `data/bank-pool/valuation-v2/history.csv.gz`（42 家月末 PB / 股息率序列）
- `data/bank-pool/valuation-v2/manifest.json`

用法：
    .venv/bin/python tools/build_bank_valuation_v2_data.py
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.quality.tables import read_table, write_table  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
BANK_DIR = ROOT / "data" / "bank-pool"
SNAPSHOT = BANK_DIR / "snapshot-qps-dc9c12160476db8b"
INPUTS = BANK_DIR / "valuation-inputs.csv.gz"
INDICATORS = SNAPSHOT / "bank-indicators.csv.gz"
OUT_DIR = BANK_DIR / "valuation-v2"
CACHE_DIR = ROOT / "data" / "cache" / "bank-pool" / "v2"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
BALANCE_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/zcfzbAjaxNew"

INPUT_COLUMNS = [
    "code", "name", "in_scope", "report_date", "parent_equity", "other_equity_tool",
    "bvps", "shares", "price", "price_date", "roe_2021", "roe_2022", "roe_2023",
    "roe_2024", "roe_2025", "roe_3y_avg", "roe_5y_avg", "roe_5y_min", "npl_latest",
    "npl_5y_ago", "npl_change_5y", "provision_coverage", "loss_years_7",
    "data_complete", "pb_current", "pb_percentile", "dy_current", "dy_percentile",
    "history_months", "history_start", "history_end", "history_status",
    "price_source", "balance_source",
]
HISTORY_COLUMNS = [
    "code", "month", "date", "close", "pb", "trailing_dps", "dividend_yield",
]


def _now() -> datetime:
    return datetime.now(TZ)


def _num(value):
    if value in (None, "", "nan", "None"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_json(url: str, params: dict, *, timeout: int = 30, tries: int = 4) -> dict:
    last = None
    for attempt in range(tries):
        try:
            response = requests.get(
                url, params=params, headers={"User-Agent": USER_AGENT}, timeout=timeout
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"请求失败：{url}：{last}")


def _balance(code: str, report_date: str) -> dict:
    cache = CACHE_DIR / f"{code}-balance.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    market = "SH" if code.startswith("6") else "SZ"
    payload = _get_json(
        BALANCE_URL,
        {
            "companyType": "3",
            "reportDateType": "0",
            "reportType": "1",
            "dates": report_date,
            "code": f"{market}{code}",
        },
    )
    rows = payload.get("data") or []
    if not rows:
        raise RuntimeError(f"{code} 没有 {report_date} 资产负债表")
    row = rows[0]
    output = {
        "report_date": str(row.get("REPORT_DATE") or "")[:10],
        "parent_equity": _num(row.get("TOTAL_PARENT_EQUITY")),
        "other_equity_tool": _num(row.get("OTHER_EQUITY_TOOL")) or 0.0,
        "source_id": "eastmoney.f10.balance",
        "source_url": BALANCE_URL,
        "fetched_at": _now().isoformat(timespec="seconds"),
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def _valuation(code: str) -> list[dict]:
    import akshare as ak

    cache = CACHE_DIR / f"{code}-valuation.csv"
    if cache.exists():
        import csv

        with cache.open("r", encoding="utf-8") as handle:
            return [
                {"date": row["date"], "close": float(row["close"]), "pb": float(row["pb"])}
                for row in csv.DictReader(handle)
            ]
    frame = ak.stock_value_em(symbol=code)
    rows = []
    for _, item in frame.iterrows():
        day = str(item["数据日期"])[:10]
        close = _num(item.get("当日收盘价"))
        pb = _num(item.get("市净率"))
        if not day or close is None or pb is None:
            continue
        rows.append({"date": day, "close": close, "pb": pb})
    rows.sort(key=lambda row: row["date"])
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    import csv

    with cache.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "close", "pb"])
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _dividends(code: str) -> list[dict]:
    import akshare as ak

    cache = CACHE_DIR / f"{code}-dividends.csv"
    if cache.exists():
        import csv

        with cache.open("r", encoding="utf-8") as handle:
            return [
                {"ex_date": row["ex_date"], "cash_per_share": float(row["cash_per_share"])}
                for row in csv.DictReader(handle)
            ]
    frame = ak.stock_history_dividend_detail(symbol=code, indicator="分红")
    events: dict[str, float] = {}
    for _, item in frame.iterrows():
        ex_date = item.get("除权除息日")
        if ex_date is None or str(ex_date) in ("NaT", "nan", ""):
            continue
        if str(item.get("进度") or "") != "实施":
            continue
        cash = _num(item.get("派息"))
        if cash is None:
            continue
        day = str(ex_date)[:10]
        events[day] = events.get(day, 0.0) + cash / 10.0
    rows = [{"ex_date": day, "cash_per_share": cash} for day, cash in sorted(events.items())]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    import csv

    with cache.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ex_date", "cash_per_share"])
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _month_end_rows(rows: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for row in rows:
        month = row["date"][:7]
        grouped[month] = row
    return [grouped[month] for month in sorted(grouped)]


def _trailing_dps(events: list[dict], day: str) -> float:
    end = date.fromisoformat(day)
    start = end - timedelta(days=365)
    total = 0.0
    for event in events:
        ex_date = date.fromisoformat(event["ex_date"])
        if start < ex_date <= end:
            total += event["cash_per_share"]
    return total


def _percentile(values: list[float], current: float | None) -> float | None:
    if current is None or not values:
        return None
    return round(100.0 * sum(1 for value in values if value <= current) / len(values), 1)


def _history(code: str) -> dict:
    rows = _valuation(code)
    if not rows:
        return {"status": "insufficient", "months": 0, "rows": []}
    today = date.fromisoformat(rows[-1]["date"])
    cutoff = today.replace(year=today.year - 10)
    monthly = _month_end_rows([row for row in rows if date.fromisoformat(row["date"]) >= cutoff])
    dividends = _dividends(code)
    history_rows = []
    for row in monthly:
        day = row["date"]
        dps = _trailing_dps(dividends, day)
        history_rows.append(
            {
                "code": code,
                "month": day[:7],
                "date": day,
                "close": row["close"],
                "pb": row["pb"],
                "trailing_dps": round(dps, 6),
                "dividend_yield": round(dps / row["close"], 8) if row["close"] else None,
            }
        )
    # Percentile: current value vs completed month-ends before the current month.
    current_month = rows[-1]["date"][:7]
    base = [item for item in history_rows if item["month"] < current_month]
    pb_values = [item["pb"] for item in base if item["pb"] is not None]
    dy_values = [item["dividend_yield"] for item in base if item["dividend_yield"] is not None]
    months = len(base)
    if months < 60:
        return {
            "status": "insufficient",
            "months": months,
            "rows": history_rows,
            "pb_percentile": None,
            "dy_percentile": None,
            "pb_current": rows[-1]["pb"],
            "dy_current": (
                _trailing_dps(dividends, rows[-1]["date"]) / rows[-1]["close"]
                if rows[-1]["close"]
                else None
            ),
            "start": base[0]["month"] if base else None,
            "end": base[-1]["month"] if base else None,
        }
    current_dps = _trailing_dps(dividends, rows[-1]["date"])
    current_dy = current_dps / rows[-1]["close"] if rows[-1]["close"] else None
    return {
        "status": "ok",
        "months": months,
        "rows": history_rows,
        "pb_percentile": _percentile(pb_values, rows[-1]["pb"]),
        "dy_percentile": _percentile(dy_values, current_dy),
        "pb_current": rows[-1]["pb"],
        "dy_current": current_dy,
        "start": base[0]["month"],
        "end": base[-1]["month"],
    }


def _roe_series(indicators: list[dict]) -> dict[int, float]:
    output = {}
    for row in indicators:
        if str(row.get("report_type")) != "年报":
            continue
        year_text = str(row.get("report_date") or "")[:4]
        value = _num(row.get("roe_weighted"))
        if year_text.isdigit() and value is not None:
            output[int(year_text)] = value / 100.0
    return output


def _indicator_latest(indicators: list[dict]) -> dict:
    rows = sorted(indicators, key=lambda row: str(row.get("report_date") or ""))
    latest = dict(rows[-1]) if rows else {}
    for field in ("npl_ratio", "provision_coverage", "core_tier1_ratio"):
        if _num(latest.get(field)) is not None:
            continue
        for row in reversed(rows[:-1]):
            if _num(row.get(field)) is not None:
                latest[field] = row.get(field)
                break
    return latest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-fetch", action="store_true", help="只用缓存/已提交数据重建输出")
    args = parser.parse_args(argv)

    inputs = read_table(INPUTS)
    indicators_rows = read_table(INDICATORS)
    by_code: dict[str, list[dict]] = defaultdict(list)
    for row in indicators_rows:
        by_code[str(row["code"]).zfill(6)].append(row)

    history_table = []
    input_rows = []
    generated_at = _now().isoformat(timespec="seconds")
    for index, row in enumerate(sorted(inputs, key=lambda item: str(item["code"])), start=1):
        code = str(row["code"]).zfill(6)
        name = row["name"]
        in_scope = str(row.get("in_scope")) == "True"
        indicators = by_code.get(code) or []
        latest_indicator = _indicator_latest(indicators)
        roe = _roe_series(indicators)
        report_date = str(latest_indicator.get("report_date") or "")[:10]
        balance = {}
        if in_scope:
            try:
                balance = _balance(code, report_date)
            except Exception as exc:  # noqa: BLE001
                print(f"[{index}/{len(inputs)}] {code} balance failed: {exc}", file=sys.stderr)
        history = {"status": "missing", "months": 0, "rows": [], "pb_percentile": None,
                   "dy_percentile": None, "pb_current": None, "dy_current": None,
                   "start": None, "end": None}
        try:
            history = _history(code)
        except Exception as exc:  # noqa: BLE001
            print(f"[{index}/{len(inputs)}] {code} history failed: {exc}", file=sys.stderr)
        history_rows = history.get("rows") or []
        history_table.extend(history_rows)

        shares = _num(row.get("shares"))
        parent_equity = _num(balance.get("parent_equity"))
        other_equity = _num(balance.get("other_equity_tool")) or 0.0
        bvps = (
            (parent_equity - other_equity) / shares
            if parent_equity is not None and shares
            else None
        )
        years = [roe.get(year) for year in (2021, 2022, 2023, 2024, 2025)]
        have = [value for value in years if value is not None]
        roe_5y_avg = statistics.fmean(have) if len(have) == 5 else None
        roe_5y_min = min(have) if len(have) == 5 else None
        roe_3y = [roe.get(year) for year in (2023, 2024, 2025)]
        roe_3y_avg = statistics.fmean(roe_3y) if all(value is not None for value in roe_3y) else None
        npl_latest = _num(latest_indicator.get("npl_ratio"))
        npl_5y_ago = _num((by_code.get(code) or [{}])[0].get("npl_ratio"))
        for item in by_code.get(code) or []:
            if str(item.get("report_date") or "")[:10] == "2021-12-31":
                npl_5y_ago = _num(item.get("npl_ratio"))
                break
        npl_change = (
            npl_latest - npl_5y_ago
            if npl_latest is not None and npl_5y_ago is not None
            else None
        )
        provision = _num(latest_indicator.get("provision_coverage"))
        price = history.get("pb_current")
        price_row = history_rows[-1] if history_rows else {}
        input_row = {
            "code": code,
            "name": name,
            "in_scope": in_scope,
            "report_date": balance.get("report_date") or report_date,
            "parent_equity": parent_equity,
            "other_equity_tool": other_equity,
            "bvps": round(bvps, 6) if bvps is not None else None,
            "shares": shares,
            "price": price_row.get("close") or _num(row.get("price")),
            "price_date": price_row.get("date") or row.get("price_date"),
            "roe_2021": roe.get(2021), "roe_2022": roe.get(2022), "roe_2023": roe.get(2023),
            "roe_2024": roe.get(2024), "roe_2025": roe.get(2025),
            "roe_3y_avg": roe_3y_avg, "roe_5y_avg": roe_5y_avg, "roe_5y_min": roe_5y_min,
            "npl_latest": npl_latest, "npl_5y_ago": npl_5y_ago,
            "npl_change_5y": round(npl_change, 4) if npl_change is not None else None,
            "provision_coverage": provision,
            "loss_years_7": _num(row.get("loss_years_7")),
            "data_complete": all(
                value is not None
                for value in (bvps, roe_3y_avg, roe_5y_avg, roe_5y_min, npl_latest, provision, shares, price_row.get("close"))
            ),
            "pb_current": history.get("pb_current"),
            "pb_percentile": history.get("pb_percentile"),
            "dy_current": history.get("dy_current"),
            "dy_percentile": history.get("dy_percentile"),
            "history_months": history.get("months"),
            "history_start": history.get("start"),
            "history_end": history.get("end"),
            "history_status": history.get("status"),
            "price_source": "eastmoney.value_analysis",
            "balance_source": "eastmoney.f10.balance",
        }
        input_rows.append(input_row)
        print(f"[{index}/{len(inputs)}] {code} {name} history={history.get('status')}", flush=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    input_meta = write_table(
        OUT_DIR / "inputs.csv.gz",
        input_rows,
        INPUT_COLUMNS,
        metadata={
            "kind": "bank-valuation-v2-inputs",
            "generated_at": generated_at,
            "definition_note": "36 家范围内银行估值输入 + 42 家历史位置；市净率/股息率为东方财富同源估值与分红数据。",
        },
    )
    history_meta = write_table(
        OUT_DIR / "history.csv.gz",
        history_table,
        HISTORY_COLUMNS,
        metadata={
            "kind": "bank-valuation-history",
            "generated_at": generated_at,
            "definition_note": "月末 PB 与滚动 12 个月股息率；同源东方财富收盘价、市净率与分红（除权除息日）。",
        },
    )
    manifest = {
        "schema_version": "bank-valuation-v2-data/1",
        "generated_at": generated_at,
        "inputs": "inputs.csv.gz",
        "history": "history.csv.gz",
        "inputs_rows": len(input_rows),
        "in_scope": sum(1 for row in input_rows if row["in_scope"]),
        "history_rows": len(history_table),
        "history_ok": sum(1 for row in input_rows if row["history_status"] == "ok"),
        "tables": [input_meta, history_meta],
        "sources": [
            "eastmoney.f10.balance (zcfzbAjaxNew)",
            "eastmoney.value_analysis (RPT_VALUEANALYSIS_DET)",
            "eastmoney.f10.dividend (stock_history_dividend_detail)",
            "bank-pool snapshot bank-indicators.csv.gz",
        ],
        "warning": "本数据只用于银行估值 v2 试运行与历史位置描述，不写正式账本，不改变 CLI 银行结论。",
    }
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
