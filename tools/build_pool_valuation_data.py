#!/usr/bin/env python
"""构建全市场批量估值输入快照（不修改好公司池快照、政策或估值代码）。

按报告期批量取东方财富三张合并报表的估值所需字段；行情用一次性全市场快照
（腾讯行情分批请求，记录价格日期），总股本用 F10 股本变动表的最新记录。
输出 `data/pool-valuation/snapshot-qps-*/`（压缩表 + 元数据 + 内容哈希）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.quality.snapshot import build_snapshot_dir  # noqa: E402
from engine.quality.columns import DIVIDEND_COLUMNS  # noqa: E402
from engine.quality.tables import read_table, write_table  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
POOL_DIR = ROOT / "data" / "pool-valuation"
CACHE_DIR = ROOT / "data" / "cache" / "pool-valuation"
TABLE_DIR = CACHE_DIR / "tables"
QUALITY_SNAPSHOT = ROOT / "data" / "quality-pool" / "snapshot-qps-3ebbd24be612bf7e"
EASTMONEY_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
ANALYSIS_DATE = "2026-11-13"

CODE_RANGES = (
    ("SZ", '(SECURITY_CODE<"400000")'),
    ("SH", '(SECURITY_CODE>="600000")(SECURITY_CODE<"700000")'),
)

BALANCE_COLUMNS = [
    "SECUCODE", "SECURITY_CODE", "SECURITY_NAME_ABBR", "NOTICE_DATE", "REPORT_DATE",
    "TOTAL_PARENT_EQUITY", "TOTAL_EQUITY", "MONETARYFUNDS", "SHORT_LOAN",
    "NONCURRENT_LIAB_1YEAR", "LONG_LOAN", "BOND_PAYABLE", "LEASE_LIAB", "TOTAL_ASSETS",
    "TOTAL_LIABILITIES", "MINORITY_EQUITY", "INVENTORY", "NOTE_ACCOUNTS_RECE",
    "ACCOUNTS_RECE", "NOTE_ACCOUNTS_PAYABLE", "ACCOUNTS_PAYABLE", "SHARE_CAPITAL",
    "LOAN_ADVANCE", "ACCEPT_DEPOSIT_INTERBANK",
]
INCOME_COLUMNS = [
    "SECUCODE", "SECURITY_CODE", "SECURITY_NAME_ABBR", "NOTICE_DATE", "REPORT_DATE",
    "TOTAL_OPERATE_INCOME", "OPERATE_INCOME", "OPERATE_PROFIT", "TOTAL_PROFIT",
    "INCOME_TAX", "NETPROFIT", "PARENT_NETPROFIT", "FE_INTEREST_EXPENSE",
    "FINANCE_EXPENSE", "BASIC_EPS",
]
CASHFLOW_COLUMNS = [
    "SECUCODE", "SECURITY_CODE", "SECURITY_NAME_ABBR", "NOTICE_DATE", "REPORT_DATE",
    "NETCASH_OPERATE", "CONSTRUCT_LONG_ASSET", "FA_IR_DEPR", "OILGAS_BIOLOGY_DEPR",
    "IA_AMORTIZE", "LPE_AMORTIZE", "USERIGHT_ASSET_AMORTIZE", "END_CASH_EQUIVALENTS",
    "END_CCE", "INVENTORY_REDUCE", "OPERATE_RECE_REDUCE", "OPERATE_PAYABLE_ADD",
    "DEFER_TAX",
]
SHARE_COLUMNS = ["SECUCODE", "SECURITY_CODE", "END_DATE", "TOTAL_SHARES"]
PRICE_COLUMNS = ["code", "name", "price", "price_date", "previous_close", "source", "fetched_at"]

PERIODS = {
    "balance": (
        "RPT_F10_FINANCE_GBALANCE", BALANCE_COLUMNS,
        [f"{year}-12-31" for year in range(2015, 2026)],
    ),
    "income": (
        "RPT_F10_FINANCE_GINCOME", INCOME_COLUMNS,
        [f"{year}-12-31" for year in range(2016, 2026)],
    ),
    "cashflow": (
        "RPT_F10_FINANCE_GCASHFLOW", CASHFLOW_COLUMNS,
        [f"{year}-12-31" for year in range(2016, 2026)],
    ),
}


def _now() -> datetime:
    return datetime.now(TZ)


def _stamp() -> str:
    return _now().isoformat(timespec="seconds")


def _get_json(url: str, params: dict[str, str], *, tries: int = 4, timeout: int = 45) -> dict:
    query = urllib.parse.urlencode(params)
    urls = [url]
    if "datacenter-web.eastmoney.com/api" in url:
        urls.append(url.replace("datacenter-web.eastmoney.com/api", "datacenter.eastmoney.com/securities/api"))
    last: Exception | None = None
    for attempt in range(tries):
        for candidate in urls:
            try:
                request = urllib.request.Request(
                    f"{candidate}?{query}", headers={"User-Agent": USER_AGENT}
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not payload.get("success"):
                    raise RuntimeError(str(payload.get("message") or "接口失败"))
                return payload
            except Exception as exc:  # noqa: BLE001
                last = exc
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"请求失败（重试 {tries} 次）：{last}")


def _fetch_table(kind: str, report_name: str, columns: list[str], period: str) -> list[dict]:
    path = TABLE_DIR / f"{kind}-{period}.csv.gz"
    if path.exists():
        return read_table(path)
    rows: list[dict] = []
    stamp = _stamp()
    for _venue, code_filter in CODE_RANGES:
        page = 1
        while page <= 30:
            payload = _get_json(
                EASTMONEY_URL,
                {
                    "reportName": report_name,
                    "columns": ",".join(columns),
                    "pageSize": "500",
                    "pageNumber": str(page),
                    "source": "WEB",
                    "client": "WEB",
                    "filter": f"(REPORT_DATE='{period}'){code_filter}",
                    "sortColumns": "SECURITY_CODE",
                    "sortTypes": "1",
                },
            )
            data = (payload.get("result") or {}).get("data") or []
            if not data:
                break
            rows.extend(data)
            if len(data) < 500:
                break
            page += 1
            time.sleep(0.05)
    # 只保留沪深 A 股，按代码去重
    by_code = {}
    for row in rows:
        secucode = str(row.get("SECUCODE") or "")
        code = str(row.get("SECURITY_CODE") or "").zfill(6)
        if not (secucode.endswith(".SH") or secucode.endswith(".SZ")):
            continue
        row["SECURITY_CODE"] = code
        by_code[code] = {column: row.get(column) for column in columns}
    data = [by_code[code] for code in sorted(by_code)]
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    write_table(
        path,
        data,
        columns,
        metadata={
            "kind": f"{kind}_full",
            "report_date": period,
            "source_id": "eastmoney.f10.batch_financials",
            "publisher": "东方财富",
            "interface": report_name,
            "url": EASTMONEY_URL,
            "retrieved_at": stamp,
            "definition_note": f"{period} 全市场沪深 A 股合并报表估值所需字段；按报告期批量。",
        },
    )
    return data


def _fetch_shares() -> list[dict]:
    path = TABLE_DIR / "shares.csv.gz"
    if path.exists():
        return read_table(path)
    rows: list[dict] = []
    stamp = _stamp()
    for _venue, code_filter in CODE_RANGES:
        page = 1
        pages = 1
        while page <= pages:
            payload = _get_json(
                EASTMONEY_URL,
                {
                    "reportName": "RPT_F10_EH_EQUITY",
                    "columns": ",".join(SHARE_COLUMNS),
                    "pageSize": "500",
                    "pageNumber": str(page),
                    "source": "WEB",
                    "client": "WEB",
                    "filter": f"(END_DATE>='2026-01-01'){code_filter}",
                    "sortColumns": "SECURITY_CODE,END_DATE",
                    "sortTypes": "1,-1",
                },
            )
            result = payload.get("result") or {}
            pages = int(result.get("pages") or 1)
            rows.extend(result.get("data") or [])
            page += 1
            time.sleep(0.05)
    latest: dict[str, dict] = {}
    for row in rows:
        code = str(row.get("SECURITY_CODE") or "").zfill(6)
        day = str(row.get("END_DATE") or "")[:10]
        if not day or day > ANALYSIS_DATE:
            continue
        if code not in latest or day > str(latest[code].get("END_DATE"))[:10]:
            latest[code] = {
                "code": code,
                "name": row.get("SECURITY_NAME_ABBR"),
                "end_date": day,
                "total_shares": row.get("TOTAL_SHARES"),
            }
    data = [latest[code] for code in sorted(latest)]
    write_table(
        path,
        data,
        ["code", "name", "end_date", "total_shares"],
        metadata={
            "kind": "shares",
            "source_id": "eastmoney.f10.equity",
            "publisher": "东方财富",
            "interface": "RPT_F10_EH_EQUITY",
            "url": EASTMONEY_URL,
            "retrieved_at": stamp,
            "definition_note": "最近一期股本变动（END_DATE ≤ 分析日）的总股本；用于每股价值换算。",
        },
    )
    return data


def _prev_trading_day(day: date) -> date:
    while day.weekday() >= 5:
        day = day - timedelta(days=1)
    return day


def _fetch_prices(codes: list[str]) -> list[dict]:
    path = TABLE_DIR / "price.csv.gz"
    if path.exists():
        return read_table(path)
    stamp = _stamp()
    now = _now()
    use_previous = now.hour < 15  # A 股收盘前，取上一交易日收盘
    rows: list[dict] = []
    for start in range(0, len(codes), 200):
        chunk = codes[start : start + 200]
        symbols = ",".join(("sh" if code.startswith("6") else "sz") + code for code in chunk)
        request = urllib.request.Request(
            f"https://qt.gtimg.cn/q={symbols}", headers={"User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("gbk", errors="ignore")
        for line in text.splitlines():
            match = re.match(r'v_(?:sh|sz)(\d{6})="(.+)"', line.strip())
            if not match:
                continue
            code, payload = match.groups()
            fields = payload.split("~")
            if len(fields) < 40:
                continue
            try:
                current = float(fields[3])
                previous = float(fields[4]) if fields[4] else None
            except (TypeError, ValueError):
                continue
            timestamp = next((item for item in fields if re.fullmatch(r"\d{14}", item)), None)
            quote_day = (
                date(int(timestamp[:4]), int(timestamp[4:6]), int(timestamp[6:8]))
                if timestamp
                else now.date()
            )
            if use_previous and previous:
                price = previous
                price_day = _prev_trading_day(quote_day - timedelta(days=1))
            else:
                price = current
                price_day = quote_day
            rows.append(
                {
                    "code": code,
                    "name": fields[1],
                    "price": price,
                    "price_date": price_day.isoformat(),
                    "previous_close": previous,
                    "source": "tencent.quote_snapshot",
                    "fetched_at": stamp,
                }
            )
        time.sleep(0.05)
    write_table(
        path,
        rows,
        PRICE_COLUMNS,
        metadata={
            "kind": "price",
            "source_id": "tencent.quote_snapshot",
            "publisher": "腾讯财经",
            "interface": "qt.gtimg.cn 全市场行情快照（分批）",
            "url": "https://qt.gtimg.cn/",
            "retrieved_at": stamp,
            "definition_note": (
                "全市场一次性行情快照；收盘前取上一交易日收盘价，收盘后取当日收盘价；"
                "价格日期逐行记录。"
            ),
        },
    )
    return rows


def build_snapshot(financial_tables: list[Path], price_path: Path, shares_path: Path) -> tuple[Path, dict]:
    dividend_tables = sorted(QUALITY_SNAPSHOT.glob("dividends-*.csv.gz"))
    base_tables = [
        QUALITY_SNAPSHOT / "universe.csv.gz",
        QUALITY_SNAPSHOT / "industry.csv.gz",
    ]
    table_paths = financial_tables + dividend_tables + base_tables + [price_path, shares_path]
    sources = [
        {
            "id": "eastmoney.f10.batch_financials",
            "publisher": "东方财富",
            "interface": "RPT_F10_FINANCE_GBALANCE / GINCOME / GCASHFLOW",
            "url": EASTMONEY_URL,
            "note": "2015–2025 年报与 2026 三季报；按报告期批量，只保留估值所需字段。",
        },
        {
            "id": "tencent.quote_snapshot",
            "publisher": "腾讯财经",
            "interface": "qt.gtimg.cn 全市场行情快照",
            "url": "https://qt.gtimg.cn/",
            "note": "全市场一次性快照；记录逐行价格日期。",
        },
        {
            "id": "eastmoney.f10.equity",
            "publisher": "东方财富",
            "interface": "RPT_F10_EH_EQUITY",
            "url": EASTMONEY_URL,
            "note": "最近一期股本变动；总股本用于每股价值换算。",
        },
    ]
    target, manifest = build_snapshot_dir(
        table_paths,
        parent=POOL_DIR,
        as_of=ANALYSIS_DATE,
        window={"start_fy": 2015, "end_fy": 2025, "interim": "2026-09-30"},
        scope={"markets": ["SH", "SZ"], "asset_class": "equity", "note": "池成员估值批量输入；不含银行估值"},
        sources=sources,
        untested_conditions=[],
        build_stats={"kind": "pool_valuation", "tables": len(table_paths)},
    )
    (POOL_DIR / "manifest.json").write_text(
        json.dumps(
            {
                "current": target.name,
                "snapshot_id": manifest["snapshot_id"],
                "content_hash": manifest["content_hash"],
                "as_of": manifest["as_of"],
                "tables": len(manifest["tables"]),
                "total_bytes": manifest["total_bytes"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return target, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args(argv)

    financial: list[Path] = []
    if not args.build_only:
        jobs = []
        for kind, (report_name, columns, periods) in PERIODS.items():
            for period in periods:
                jobs.append((kind, report_name, columns, period))
        jobs.append(("interim_income", "RPT_F10_FINANCE_GINCOME", INCOME_COLUMNS, "2026-09-30"))
        jobs.append(("interim_income", "RPT_F10_FINANCE_GINCOME", INCOME_COLUMNS, "2025-09-30"))
        for period in ("2023-09-30", "2024-09-30", "2025-09-30"):
            jobs.append(("dividends", "RPT_SHAREBONUS_DET", DIVIDEND_COLUMNS, period))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {
                pool.submit(_fetch_table, kind, report_name, columns, period): (kind, period)
                for kind, report_name, columns, period in jobs
            }
            for future in as_completed(futures):
                kind, period = futures[future]
                rows = future.result()
                financial.append(TABLE_DIR / f"{kind}-{period}.csv.gz")
                print(f"{kind} {period}: {len(rows)} 行", flush=True)
        shares = _fetch_shares()
        universe = read_table(QUALITY_SNAPSHOT / "universe.csv.gz")
        codes = sorted(str(row["code"]).zfill(6) for row in universe)
        prices = _fetch_prices(codes)
        print(f"shares {len(shares)}；price {len(prices)}", flush=True)
    else:
        financial = sorted(TABLE_DIR.glob("*-2025-12-31.csv.gz")) + sorted(
            TABLE_DIR.glob("*-2026-09-30.csv.gz")
        )
        # 重新收集全部报告期表
        financial = sorted(
            path
            for path in TABLE_DIR.glob("*.csv.gz")
            if path.name.startswith(("balance-", "income-", "cashflow-", "interim_income-", "dividends-"))
        )
    price_path = TABLE_DIR / "price.csv.gz"
    shares_path = TABLE_DIR / "shares.csv.gz"
    target, manifest = build_snapshot(financial, price_path, shares_path)
    print(f"快照 {target.name}：{len(manifest['tables'])} 张表，{manifest['total_bytes']/1024/1024:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
