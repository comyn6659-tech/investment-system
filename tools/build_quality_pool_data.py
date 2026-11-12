#!/usr/bin/env python
"""好公司池（quality-pool-v1-draft）的数据采集与固定快照构建。

施工单：第四阶段第四批（`batch4d-quality-pool-instructions.md`）。
本工具只负责“把公开数据按报告期批量取回来 + 落成可重放的固定快照”，
不判断好公司、不写结论、不碰个股政策。

数据口径：
- 财务：东方财富 F10 合并报表（年报），资产负债表 2015–2025、利润表/现金流量表
  2016–2025；2015 年资产负债表只用于计算 2016 年 ROE 的平均归母净资产。
- 分红：东方财富 `RPT_SHAREBONUS_DET`，按报告期取 2021–2025 年中期与年度方案。
- 质押：东方财富 `RPT_CSDC_LIST` 最新交易日的**公司整体**质押比例；
  该口径不是“控股股东质押比例”，快照里单列且不参与规则。
- 审计意见、处罚/立案、控股股东质押：没有全市场批量接口，可行性报告记为未检验。
- 行业：东方财富业绩报表 `PUBLISHNAME`（最新年报），用于剔除金融业。
- 上市日期、板块、ST：上交所/深交所股票列表。

用法：
    .venv/bin/python tools/build_quality_pool_data.py --fetch-only
    .venv/bin/python tools/build_quality_pool_data.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.quality.build import build_companies  # noqa: E402
from engine.quality.columns import (  # noqa: E402
    BALANCE_COLUMNS,
    CASHFLOW_COLUMNS,
    DIVIDEND_COLUMNS,
    INCOME_COLUMNS,
    INDUSTRY_COLUMNS,
    PLEDGE_COLUMNS,
    UNIVERSE_COLUMNS,
)
from engine.quality.tables import read_table, write_table  # noqa: E402
from engine.quality.policy import load_policy  # noqa: E402
from engine.quality.snapshot import build_snapshot_dir  # noqa: E402

CACHE_DIR = ROOT / "data" / "cache" / "quality-pool"
TABLE_DIR = CACHE_DIR / "tables"
POOL_DIR = ROOT / "data" / "quality-pool"
COVERAGE_DIR = ROOT / "data" / "coverage"
FEASIBILITY_PATH = COVERAGE_DIR / "quality-pool-feasibility.json"

TZ = ZoneInfo("Asia/Singapore")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
EASTMONEY_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
EASTMONEY_WEB_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

BALANCE_YEARS = tuple(range(2015, 2026))
FLOW_YEARS = tuple(range(2016, 2026))
DIVIDEND_PERIODS = tuple(
    f"{year}-{month}" for year in range(2021, 2026) for month in ("06-30", "12-31")
)
STATEMENTS = {
    "balance": ("RPT_F10_FINANCE_GBALANCE", BALANCE_YEARS),
    "income": ("RPT_F10_FINANCE_GINCOME", FLOW_YEARS),
    "cashflow": ("RPT_F10_FINANCE_GCASHFLOW", FLOW_YEARS),
}

# F10 接口没有交易所过滤；按代码区间分两次取，客户端再按 .SZ/.SH 过滤。
CODE_RANGES = (
    ("SZ", '(SECURITY_CODE<"400000")'),
    ("SH", '(SECURITY_CODE>="600000")(SECURITY_CODE<"700000")'),
)


def _now() -> datetime:
    return datetime.now(TZ)


def _stamp(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def _get_json(
    url: str, params: dict[str, str], *, tries: int = 4, timeout: int = 120
) -> dict:
    query = urllib.parse.urlencode(params)
    last_error: Exception | None = None
    for attempt in range(tries):
        try:
            request = urllib.request.Request(
                f"{url}?{query}", headers={"User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not payload.get("success"):
                raise RuntimeError(str(payload.get("message") or "接口返回失败"))
            return payload
        except Exception as exc:  # 网络抖动统一重试；最后一次失败才抛出
            last_error = exc
            message = str(exc)
            # 东财在短时请求过多时会用「filter参数有误」这类通用错误挡回；
            # 退避要足够长，否则重试只会继续撞限流。
            wait = 20.0 if "filter" in message.lower() or "限流" in message else 1.2 * (attempt + 1)
            time.sleep(wait)
    raise RuntimeError(f"请求失败（重试 {tries} 次）：{last_error}")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- 股票列表 ---------------------------------------------------------------
def _normalise_name(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _board_for(code: str) -> str:
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    return "主板"


def _fetch_sse_list(stock_type: str) -> list[dict]:
    """上交所股票列表；响应约 2MB，显式给足超时，避免无超时挂起。"""
    import requests

    params = {
        "STOCK_TYPE": stock_type,
        "REG_PROVINCE": "",
        "CSRC_CODE": "",
        "STOCK_CODE": "",
        "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L",
        "COMPANY_STATUS": "2,4,5,7,8",
        "type": "inParams",
        "isPagination": "true",
        "pageHelp.cacheSize": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.pageSize": "10000",
        "pageHelp.pageNo": "1",
        "pageHelp.endPage": "1",
    }
    response = requests.get(
        "https://query.sse.com.cn/sseQuery/commonQuery.do",
        params=params,
        headers={
            "Referer": "https://www.sse.com.cn/assortment/stock/list/share/",
            "User-Agent": USER_AGENT,
        },
        timeout=(10, 240),
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("result") or []


def fetch_universe(refresh: bool = False) -> dict:
    path = CACHE_DIR / "raw" / "universe.json"
    if path.exists() and not refresh:
        return _read_json(path)

    import akshare as ak  # 仅本工具使用；engine 仍是 stdlib-only

    rows: list[dict] = []
    for stock_type, board in (("1", "主板"), ("8", "科创板")):
        for item in _fetch_sse_list(stock_type):
            code = str(item["A_STOCK_CODE"]).zfill(6)
            rows.append(
                {
                    "code": code,
                    "name": _normalise_name(item["SEC_NAME_CN"]),
                    "exchange": "SH",
                    "board": board,
                    "listing_date": str(item.get("LIST_DATE") or "")[:10],
                    "source": f"sse.commonQuery:STOCK_TYPE={stock_type}",
                }
            )
    frame = ak.stock_info_sz_name_code(symbol="A股列表")
    for _, item in frame.iterrows():
        code = str(item["A股代码"]).zfill(6)
        rows.append(
            {
                "code": code,
                "name": _normalise_name(item["A股简称"]),
                "exchange": "SZ",
                "board": str(item["板块"]) or _board_for(code),
                "listing_date": str(item["A股上市日期"])[:10],
                "industry_csrc": str(item.get("所属行业") or ""),
                "source": "stock_info_sz_name_code:A股列表",
            }
        )
    rows.sort(key=lambda item: item["code"])
    payload = {"fetched_at": _stamp(_now()), "rows": rows}
    _write_json(path, payload)
    return payload


# --- 三大报表 ---------------------------------------------------------------
def _statement_path(kind: str, year: int) -> Path:
    return CACHE_DIR / "raw" / "financials" / str(year) / f"{kind}.jsonl"


def fetch_statement(kind: str, year: int, *, refresh: bool = False) -> dict:
    """按报告期一次取全市场；分页、去重、断点续跑。"""
    path = _statement_path(kind, year)
    meta_path = path.with_suffix(".meta.json")
    if path.exists() and meta_path.exists() and not refresh:
        meta = _read_json(meta_path)
        if meta.get("complete"):
            return meta

    report_name, _ = STATEMENTS[kind]
    report_date = f"{year}-12-31"
    best: dict[str, dict] = {}
    page_counts: dict[str, int] = {}
    for label, extra in CODE_RANGES:
        page = 1
        pages = 1
        while page <= pages:
            payload = _get_json(
                EASTMONEY_URL,
                {
                    "reportName": report_name,
                    "columns": "ALL",
                    "pageSize": "500",
                    "pageNumber": str(page),
                    "filter": f"(REPORT_DATE='{report_date}'){extra}",
                    "sortColumns": "SECURITY_CODE",
                    "sortTypes": "1",
                },
            )
            result = payload.get("result") or {}
            pages = int(result.get("pages") or 0)
            data = result.get("data") or []
            if not data:
                break
            for row in data:
                code = str(row.get("SECUCODE") or "")
                if not code.endswith((".SZ", ".SH")):
                    continue
                previous = best.get(code)
                if previous is None or str(row.get("NOTICE_DATE") or "") >= str(
                    previous.get("NOTICE_DATE") or ""
                ):
                    best[code] = row
            page += 1
            time.sleep(1.2)
        page_counts[label] = page - 1

    rows = [best[code] for code in sorted(best)]
    _write_jsonl(path, rows)
    meta = {
        "kind": kind,
        "report_name": report_name,
        "report_date": report_date,
        "rows": len(rows),
        "page_counts": page_counts,
        "fetched_at": _stamp(_now()),
        "complete": True,
    }
    _write_json(meta_path, meta)
    return meta


# --- 分红 -------------------------------------------------------------------
def fetch_dividends(period: str, *, refresh: bool = False) -> dict:
    path = CACHE_DIR / "raw" / "dividends" / f"{period}.jsonl"
    meta_path = path.with_suffix(".meta.json")
    if path.exists() and meta_path.exists() and not refresh:
        meta = _read_json(meta_path)
        if meta.get("complete"):
            return meta

    fields = (
        "SECUCODE",
        "SECURITY_CODE",
        "SECURITY_NAME_ABBR",
        "REPORT_DATE",
        "PRETAX_BONUS_RMB",
        "TOTAL_SHARES",
        "ASSIGN_PROGRESS",
        "IMPL_PLAN_PROFILE",
        "NOTICE_DATE",
        "PLAN_NOTICE_DATE",
    )
    rows: list[dict] = []
    page = 1
    pages = 1
    while page <= pages:
        payload = _get_json(
            EASTMONEY_WEB_URL,
            {
                "reportName": "RPT_SHAREBONUS_DET",
                "columns": "ALL",
                "pageSize": "500",
                "pageNumber": str(page),
                "filter": f"(REPORT_DATE='{period}')",
                "sortColumns": "SECURITY_CODE",
                "sortTypes": "1",
            },
        )
        result = payload.get("result") or {}
        pages = int(result.get("pages") or 0)
        data = result.get("data") or []
        if not data:
            break
        for row in data:
            code = str(row.get("SECUCODE") or "")
            if not code.endswith((".SZ", ".SH")):
                continue
            rows.append({key: row.get(key) for key in fields})
        page += 1
        time.sleep(1.0)

    _write_jsonl(path, rows)
    meta = {
        "period": period,
        "rows": len(rows),
        "fetched_at": _stamp(_now()),
        "complete": True,
    }
    _write_json(meta_path, meta)
    return meta


# --- 质押（公司整体，仅作旁证） --------------------------------------------
def _latest_trade_day() -> str:
    """取最近一个工作日；接口没有数据时再向前找。"""
    today = _now().date()
    while today.weekday() >= 5:
        today = today.fromordinal(today.toordinal() - 1)
    return today.isoformat()


def fetch_pledge(refresh: bool = False) -> dict:
    path = CACHE_DIR / "raw" / "pledge.json"
    if path.exists() and not refresh:
        payload = _read_json(path)
        if payload.get("trade_date"):
            return payload

    day = _latest_trade_day()
    rows: list[dict] = []
    for _ in range(8):
        page = 1
        pages = 1
        collected: list[dict] = []
        while page <= pages:
            try:
                payload = _get_json(
                    EASTMONEY_WEB_URL,
                    {
                        "reportName": "RPT_CSDC_LIST",
                    "columns": "ALL",
                    "pageSize": "500",
                    "pageNumber": str(page),
                    "filter": f"(TRADE_DATE='{day}')",
                        "sortColumns": "PLEDGE_RATIO",
                        "sortTypes": "-1",
                    },
                )
            except RuntimeError as exc:
                if "返回数据为空" in str(exc):
                    page = pages + 1
                    break
                raise
            result = payload.get("result") or {}
            pages = int(result.get("pages") or 0)
            data = result.get("data") or []
            if not data:
                break
            collected.extend(data)
            page += 1
            time.sleep(0.4)
        if collected:
            for row in collected:
                code = str(row.get("SECURITY_CODE") or "").zfill(6)
                if not code.startswith(("0", "3", "6")):
                    continue
                rows.append(
                    {
                        "code": code,
                        "name": _normalise_name(row.get("SECURITY_NAME_ABBR")),
                        "trade_date": str(row.get("TRADE_DATE") or "")[:10],
                        "pledge_ratio": row.get("PLEDGE_RATIO"),
                        "pledge_shares": row.get("REPURCHASE_BALANCE"),
                        "pledge_deals": row.get("PLEDGE_DEAL_NUM"),
                        "industry": row.get("INDUSTRY"),
                    }
                )
            break
        # 当日无数据（周末/节假日）：向后退一天
        day = date.fromordinal(date.fromisoformat(day).toordinal() - 1).isoformat()

    payload = {
        "trade_date": rows[0]["trade_date"] if rows else day,
        "rows": rows,
        "fetched_at": _stamp(_now()),
        "note": "公司整体质押比例（质押股数/总股本），不是控股股东质押比例",
    }
    _write_json(path, payload)
    return payload


# --- 行业（最新年报业绩报表） ----------------------------------------------
def fetch_industry(refresh: bool = False) -> dict:
    path = CACHE_DIR / "raw" / "industry.json"
    if path.exists() and not refresh:
        payload = _read_json(path)
        if payload.get("rows"):
            return payload

    best: dict[str, dict] = {}
    for _, extra in CODE_RANGES:
        page = 1
        pages = 1
        while page <= pages:
            payload = _get_json(
                EASTMONEY_WEB_URL,
                {
                    "reportName": "RPT_LICO_FN_CPD",
                    "columns": "ALL",
                    "pageSize": "500",
                    "pageNumber": str(page),
                    "filter": f"(REPORTDATE='2025-12-31'){extra}",
                    "sortColumns": "SECURITY_CODE",
                    "sortTypes": "1",
                },
                timeout=180,
            )
            result = payload.get("result") or {}
            pages = int(result.get("pages") or 0)
            data = result.get("data") or []
            if not data:
                break
            for row in data:
                code = str(row.get("SECUCODE") or "")
                if not code.endswith((".SZ", ".SH")):
                    continue
                best[code[:6]] = {
                    "code": code[:6],
                    "industry": row.get("PUBLISHNAME"),
                    "board_name": row.get("BOARD_NAME"),
                }
            page += 1
            time.sleep(0.6)

    payload = {
        "report_date": "2025-12-31",
        "rows": [best[code] for code in sorted(best)],
        "fetched_at": _stamp(_now()),
    }
    _write_json(path, payload)
    return payload


# --- 主流程 -----------------------------------------------------------------
def _with_retry(label: str, func, *args, **kwargs):
    """单个报告期/项目失败时等待后重试，避免整批因一次限流前功尽弃。"""
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            wait = 60 * (attempt + 1)
            print(f"{label} 失败（{type(exc).__name__}: {exc}），{wait}s 后重试", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"{label} 重试仍失败：{last_error}")


def fetch_all(*, refresh: bool = False, skip_industry: bool = False) -> dict:
    started = _now()
    universe = fetch_universe(refresh=refresh)
    print(f"股票列表：{len(universe['rows'])} 只", flush=True)

    metas = []
    for kind, (_report_name, years) in STATEMENTS.items():
        for year in years:
            meta = _with_retry(f"{kind} {year}", fetch_statement, kind, year, refresh=refresh)
            metas.append(meta)
            print(
                f"{kind} {year}: {meta['rows']} 行（{meta['fetched_at']}）",
                flush=True,
            )

    for period in DIVIDEND_PERIODS:
        meta = _with_retry(f"分红 {period}", fetch_dividends, period, refresh=refresh)
        print(f"分红 {period}: {meta['rows']} 行", flush=True)

    pledge = _with_retry("质押", fetch_pledge, refresh=refresh)
    print(f"质押 {pledge['trade_date']}: {len(pledge['rows'])} 行", flush=True)

    industry = {"rows": []}
    if not skip_industry:
        industry = _with_retry("行业", fetch_industry, refresh=refresh)
        print(f"行业：{len(industry['rows'])} 只", flush=True)

    manifest = {
        "started_at": _stamp(started),
        "finished_at": _stamp(_now()),
        "universe_rows": len(universe["rows"]),
        "statement_metas": metas,
        "dividend_periods": list(DIVIDEND_PERIODS),
        "pledge_trade_date": pledge["trade_date"],
        "industry_rows": len(industry["rows"]),
    }
    _write_json(CACHE_DIR / "raw" / "manifest.json", manifest)
    print("原始数据采集完成", flush=True)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-only", action="store_true", help="只采集原始数据")
    parser.add_argument("--probe-only", action="store_true", help="只算覆盖率，不写快照")
    parser.add_argument("--fetch-tables", action="store_true", help="只补齐压缩表格")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存重新采集")
    parser.add_argument("--skip-industry", action="store_true")
    args = parser.parse_args()

    if args.fetch_tables:
        return fetch_tables_only()
    if args.fetch_only:
        # 兼容旧命令：现在只采集压缩表格，不再落逐只 JSONL。
        return fetch_tables_only()
    return build(probe_only=args.probe_only)




# --- 快照与可行性 -----------------------------------------------------------
def _load_statements() -> dict[str, dict[int, dict[str, dict]]]:
    output: dict[str, dict[int, dict[str, dict]]] = {}
    for kind, (_report_name, years) in STATEMENTS.items():
        by_year: dict[int, dict[str, dict]] = {}
        for year in years:
            path = _compressed_statement_path(kind, year)
            if not path.exists():
                raise RuntimeError(f"缺少压缩表：{path}；先运行 --fetch-tables")
            by_year[year] = {
                str(row["SECUCODE"])[:6]: row for row in read_table(path)
            }
        output[kind] = by_year
    return output


def _load_dividends() -> list[dict]:
    rows: list[dict] = []
    for period in DIVIDEND_PERIODS:
        path = _compressed_dividend_path(period)
        if not path.exists():
            raise RuntimeError(f"缺少分红表：{path}；先运行 --fetch-tables")
        rows.extend(read_table(path))
    return rows


def _load_pledge() -> dict[str, float]:
    path = TABLE_DIR / "pledge.csv.gz"
    if not path.exists():
        return {}
    output: dict[str, float] = {}
    for row in read_table(path):
        ratio = row.get("pledge_ratio")
        if not ratio:
            continue
        output[str(row["code"]).zfill(6)] = float(ratio)
    return output


def _load_industry() -> dict[str, str]:
    path = TABLE_DIR / "industry.csv.gz"
    if not path.exists():
        return {}
    return {
        str(row["code"]).zfill(6): str(row["industry"])
        for row in read_table(path)
        if row.get("industry")
    }


def _feasibility(
    companies: list,
    stats: dict,
    *,
    as_of: str,
    fetched_at: str,
    statement_metas: list[dict],
    dividend_rows: list[dict],
    pledge_rows: int,
    industry_rows: int,
    window: dict,
) -> dict:
    evaluated = stats["evaluated"]
    complete = stats["complete"]
    coverage = stats["coverage"]
    gate = 0.95
    passed = coverage is not None and coverage >= gate
    dividend_codes = {str(row.get("SECUCODE") or "")[:6] for row in dividend_rows}
    dividend_years = sorted({str(row.get("REPORT_DATE") or "")[:4] for row in dividend_rows})
    return {
        "schema_version": "quality-pool-feasibility/1",
        "as_of": as_of,
        "fetched_at": fetched_at,
        "window": dict(window),
        "universe": {
            "total": stats["universe"],
            "by_exchange": stats["by_exchange"],
            "st": stats["st"],
            "financial": stats["financial"],
            "short_history": stats["short_history"],
            "evaluated": evaluated,
        },
        "financial": {
            "interface": "东方财富 datacenter F10 合并报表按报告期批量接口（RPT_F10_FINANCE_GBALANCE / GINCOME / GCASHFLOW）",
            "years": {
                "balance": list(BALANCE_YEARS),
                "income": list(FLOW_YEARS),
                "cashflow": list(FLOW_YEARS),
            },
            "statement_rows": {
                meta["kind"] + "_" + str(meta["report_date"][:4]): meta["rows"]
                for meta in statement_metas
            },
            "core_fields": {
                "balance": ["TOTAL_PARENT_EQUITY", "MONETARYFUNDS"],
                "income": ["PARENT_NETPROFIT"],
                "cashflow": ["NETCASH_OPERATE", "CONSTRUCT_LONG_ASSET"],
            },
            "complete_companies": complete,
            "coverage": coverage,
            "threshold": gate,
            "gate_passed": passed,
            "by_year": stats["by_year"],
            "incomplete_examples": stats["incomplete"],
        },
        "governance": {
            "audit_opinion": {
                "available": False,
                "probes": [
                    "akshare 1.20.35 无审计意见函数",
                    "东方财富 RPT_AUDIT_OPINION / RPT_AUDIT_OPINION_DETAIL / RPT_F10_FINANCE_AUDIT → 报表配置不存在",
                    "巨潮资讯公告接口只有公告标题，不含逐家公司逐年审计意见类型",
                ],
                "reason": "没有覆盖全市场、可批量重放的审计意见接口；不从报表字段推断。",
                "condition_status": "本版未检验",
            },
            "penalties": {
                "available": False,
                "probes": [
                    "东方财富 RPT_VIOLATION / RPT_PUNISHMENT / RPT_INVESTIGATION 等 → 报表配置不存在",
                    "证监会行政处罚 JSON feed 只覆盖近期且不含交易所公开谴责，无法覆盖最近 5 年全市场",
                    "不采用第三方违规处理数据库拼接",
                ],
                "reason": "证监会/交易所处罚、立案、公开谴责没有全市场批量接口；不拼接新闻或第三方数据库。",
                "condition_status": "本版未检验",
            },
            "controlling_shareholder_pledge": {
                "available": False,
                "probes": [
                    "东方财富 RPT_CSDC_LIST → 公司整体质押比例（质押股数/总股本）",
                    "东方财富 RPTA_APP_ACCUMDETAILS → 单笔质押明细，不是控股股东最新余额",
                    "没有全市场「控股股东质押股份 ÷ 其所持股份」的批量字段",
                ],
                "reason": "公开批量接口只有公司整体质押比例，没有控股股东质押比例；口径不同，不替代。",
                "company_level_pledge_rows": pledge_rows,
                "condition_status": "本版未检验",
            },
            "dividends": {
                "available": True,
                "periods": [f"{year}-06-30 / {year}-12-31" for year in range(2021, 2026)],
                "rows": len(dividend_rows),
                "codes": len(dividend_codes),
                "years": dividend_years,
                "note": "按财年归集中期与年度现金分红；没有分红方案的公司记 0，不记缺失。",
            },
            "industry_rows": industry_rows,
        },
        "decision": "continue" if passed else "stop",
        "decision_note": (
            "财务覆盖率 ≥ 95%，按施工单继续构建全市场快照与规则试运行。"
            if passed
            else "财务覆盖率 < 95%，按施工单停在这里，不构建全市场快照、不做规则试运行。"
        ),
    }


def build(*, probe_only: bool = False) -> int:
    from datetime import date

    universe = {"rows": read_table(TABLE_DIR / "universe.csv.gz")}
    statements = _load_statements()
    dividends = _load_dividends()
    pledge = _load_pledge()
    industry = _load_industry()
    metas = []
    for kind, (_report_name, years) in STATEMENTS.items():
        for year in years:
            meta_path = _compressed_statement_path(kind, year).with_name(
                _compressed_statement_path(kind, year).name + ".meta.json"
            )
            if meta_path.exists():
                metas.append(_read_json(meta_path))
    manifest = {
        "finished_at": max((meta.get("fetched_at") or "" for meta in metas), default=_stamp(_now())),
        "statement_metas": metas,
    }
    policy = load_policy(ROOT / "data" / "policies" / "quality-pool-v1-draft.json")

    companies, stats = build_companies(
        universe["rows"],
        statements,
        dividends,
        pledge,
        industry,
        financial_industries=set(policy.raw["scope"]["exclude_financial_industries"]),
        window_start=int(policy.raw["scope"]["window_start_fy"]),
        window_end=int(policy.raw["scope"]["window_end_fy"]),
        min_years=int(policy.value("min_fiscal_years")),
    )
    as_of = _now().date().isoformat()
    window = {
        "start_fy": int(policy.raw["scope"]["window_start_fy"]),
        "end_fy": int(policy.raw["scope"]["window_end_fy"]),
        "min_years": int(policy.value("min_fiscal_years")),
        "window_years": int(policy.value("window_years")),
    }
    feasibility = _feasibility(
        companies,
        stats,
        as_of=as_of,
        fetched_at=manifest.get("finished_at") or _stamp(_now()),
        statement_metas=manifest.get("statement_metas") or [],
        dividend_rows=dividends,
        pledge_rows=len(pledge),
        industry_rows=len(industry),
        window=window,
    )
    _write_json(FEASIBILITY_PATH, feasibility)
    print(
        "可行性：评价 {evaluated} 家，财务完整 {complete} 家，覆盖率 {coverage:.2%}，门槛 95%".format(
            evaluated=stats["evaluated"],
            complete=stats["complete"],
            coverage=stats["coverage"] or 0.0,
        ),
        flush=True,
    )
    if probe_only or not feasibility["financial"]["gate_passed"]:
        print("决策：", feasibility["decision"], flush=True)
        return 0 if probe_only else 2

    table_paths = [TABLE_DIR / "universe.csv.gz"]
    for optional in ("industry", "pledge"):
        candidate = TABLE_DIR / f"{optional}.csv.gz"
        if candidate.exists():
            table_paths.append(candidate)
    for kind, (_report_name, years) in STATEMENTS.items():
        for year in years:
            table_paths.append(_compressed_statement_path(kind, year))
    for period in DIVIDEND_PERIODS:
        table_paths.append(_compressed_dividend_path(period))
    missing = [path.name for path in table_paths if not path.exists()]
    if missing:
        raise RuntimeError(f"缺少压缩表：{missing}；先运行 --fetch-tables")

    target, snapshot_manifest = build_snapshot_dir(
        table_paths,
        parent=POOL_DIR,
        as_of=as_of,
        window=window,
        scope=policy.raw["scope"],
        sources=[
            {
                "id": "eastmoney.f10.financials",
                "publisher": "东方财富",
                "interface": "datacenter F10 合并报表按报告期批量接口",
                "url": EASTMONEY_URL,
                "note": "资产负债表 2015–2025、利润表/现金流量表 2016–2025；合并口径，金额单位元。",
            },
            {
                "id": "eastmoney.dividend",
                "publisher": "东方财富",
                "interface": "RPT_SHAREBONUS_DET 分红送配",
                "url": EASTMONEY_WEB_URL,
                "note": "2021–2025 年中期与年度方案；每 10 股派现 × 总股本 ÷ 10，按财年归集。",
            },
            {
                "id": "eastmoney.pledge.company_level",
                "publisher": "东方财富（转中国结算）",
                "interface": "RPT_CSDC_LIST 上市公司质押比例",
                "url": EASTMONEY_WEB_URL,
                "note": "公司整体质押比例，仅作旁证，不参与规则；不是控股股东质押比例。",
            },
            {
                "id": "eastmoney.industry",
                "publisher": "东方财富",
                "interface": "RPT_LICO_FN_CPD 最新年报行业",
                "url": EASTMONEY_WEB_URL,
                "note": "用于识别银行、保险、证券、多元金融；行业缺失时用深交所列表的证监会门类。",
            },
            {
                "id": "sse.szse.listing",
                "publisher": "上海证券交易所 / 深圳证券交易所",
                "interface": "股票列表（上市日期、板块、简称）",
                "url": "https://query.sse.com.cn/；https://www.szse.cn/",
                "note": "当前沪深 A 股；ST/*ST 按简称识别，上市年份用于确定评价窗口。",
            },
        ],
        untested_conditions=[
            {
                "condition_id": condition["id"],
                "group_id": group["id"],
                "label": condition["label"],
                "reason": condition.get("untested_reason"),
            }
            for group, condition in policy.iter_conditions()
            if condition.get("evaluation") == "untested"
        ],
        build_stats={
            "evaluated": stats["evaluated"],
            "complete": stats["complete"],
            "coverage": stats["coverage"],
            "universe": stats["universe"],
        },
    )
    _write_json(
        POOL_DIR / "manifest.json",
        {
            "current": target.name,
            "snapshot_id": snapshot_manifest["snapshot_id"],
            "content_hash": snapshot_manifest["content_hash"],
            "as_of": snapshot_manifest["as_of"],
            "companies": len(companies),
            "tables": len(snapshot_manifest["tables"]),
            "total_bytes": snapshot_manifest["total_bytes"],
            "written_at": _stamp(_now()),
        },
    )
    print(
        f"快照：{target.name}，{len(companies)} 家，{len(snapshot_manifest['tables'])} 张表，"
        f"{snapshot_manifest['total_bytes'] / 1024 / 1024:.2f} MB",
        flush=True,
    )
    return 0


# --- 压缩表格采集（补充要求：报告期 × 报表类型） ---------------------------
TABLE_COLUMNS = {
    "balance": BALANCE_COLUMNS,
    "income": INCOME_COLUMNS,
    "cashflow": CASHFLOW_COLUMNS,
}
TABLE_SOURCES = {
    "balance": ("eastmoney.f10.balance", "RPT_F10_FINANCE_GBALANCE"),
    "income": ("eastmoney.f10.income", "RPT_F10_FINANCE_GINCOME"),
    "cashflow": ("eastmoney.f10.cashflow", "RPT_F10_FINANCE_GCASHFLOW"),
}


def _compressed_statement_path(kind: str, year: int) -> Path:
    return TABLE_DIR / "financials" / f"{kind}-{year}.csv.gz"


def fetch_statement_compressed(kind: str, year: int, *, refresh: bool = False) -> dict:
    path = _compressed_statement_path(kind, year)
    meta_path = path.with_name(path.name + ".meta.json")
    if path.exists() and meta_path.exists() and not refresh:
        return _read_json(meta_path)

    report_name, columns = STATEMENTS[kind][0], TABLE_COLUMNS[kind]
    report_date = f"{year}-12-31"
    best: dict[str, dict] = {}
    for label, extra in CODE_RANGES:
        page = 1
        pages = 1
        while page <= pages:
            payload = _get_json(
                EASTMONEY_URL,
                {
                    "reportName": report_name,
                    "columns": "ALL",
                    "pageSize": "500",
                    "pageNumber": str(page),
                    "filter": f"(REPORT_DATE='{report_date}'){extra}",
                    "sortColumns": "SECURITY_CODE",
                    "sortTypes": "1",
                },
            )
            result = payload.get("result") or {}
            pages = int(result.get("pages") or 0)
            data = result.get("data") or []
            if not data:
                break
            for row in data:
                code = str(row.get("SECUCODE") or "")
                if not code.endswith((".SZ", ".SH")):
                    continue
                previous = best.get(code)
                if previous is None or str(row.get("NOTICE_DATE") or "") >= str(
                    previous.get("NOTICE_DATE") or ""
                ):
                    best[code] = row
            page += 1
            time.sleep(1.2)
    rows = [best[code] for code in sorted(best)]
    source_id, interface = TABLE_SOURCES[kind]
    meta = write_table(
        path,
        rows,
        columns,
        metadata={
            "kind": kind,
            "report_date": report_date,
            "source_id": source_id,
            "publisher": "东方财富",
            "interface": interface,
            "url": EASTMONEY_URL,
            "fetched_at": _stamp(_now()),
            "definition_note": "合并报表；金额单位元；按报告期一次取全市场。",
        },
    )
    return meta


def _compressed_dividend_path(period: str) -> Path:
    return TABLE_DIR / "dividends" / f"dividends-{period}.csv.gz"


def fetch_dividends_compressed(period: str, *, refresh: bool = False) -> dict:
    path = _compressed_dividend_path(period)
    meta_path = path.with_name(path.name + ".meta.json")
    if path.exists() and meta_path.exists() and not refresh:
        return _read_json(meta_path)

    rows: list[dict] = []
    page = 1
    pages = 1
    while page <= pages:
        payload = _get_json(
            EASTMONEY_WEB_URL,
            {
                "reportName": "RPT_SHAREBONUS_DET",
                "columns": "ALL",
                "pageSize": "500",
                "pageNumber": str(page),
                "filter": f"(REPORT_DATE='{period}')",
                "sortColumns": "SECURITY_CODE",
                "sortTypes": "1",
            },
        )
        result = payload.get("result") or {}
        pages = int(result.get("pages") or 0)
        data = result.get("data") or []
        if not data:
            break
        for row in data:
            code = str(row.get("SECUCODE") or "")
            if not code.endswith((".SZ", ".SH")):
                continue
            rows.append({key: row.get(key) for key in DIVIDEND_COLUMNS})
        page += 1
        time.sleep(1.0)
    return write_table(
        path,
        rows,
        DIVIDEND_COLUMNS,
        metadata={
            "kind": "dividends",
            "report_date": period,
            "source_id": "eastmoney.dividend",
            "publisher": "东方财富",
            "interface": "RPT_SHAREBONUS_DET",
            "url": EASTMONEY_WEB_URL,
            "fetched_at": _stamp(_now()),
            "definition_note": "每 10 股派现（含税）与总股本；按报告期取方案。",
        },
    )


def fetch_pledge_compressed(refresh: bool = False) -> dict:
    path = TABLE_DIR / "pledge.csv.gz"
    meta_path = path.with_name(path.name + ".meta.json")
    if path.exists() and meta_path.exists() and not refresh:
        return _read_json(meta_path)

    payload = fetch_pledge(refresh=refresh)
    rows = []
    for row in payload.get("rows") or []:
        ratio = row.get("pledge_ratio")
        if ratio is None:
            continue
        rows.append(
            {
                "code": str(row["code"]).zfill(6),
                "name": row.get("name"),
                "trade_date": row.get("trade_date"),
                "pledge_ratio": float(ratio) / 100.0,
                "pledge_shares": row.get("pledge_shares"),
                "pledge_deals": row.get("pledge_deals"),
                "industry": row.get("industry"),
            }
        )
    return write_table(
        path,
        rows,
        PLEDGE_COLUMNS,
        metadata={
            "kind": "pledge",
            "trade_date": payload.get("trade_date"),
            "source_id": "eastmoney.pledge.company_level",
            "publisher": "东方财富（转中国结算）",
            "interface": "RPT_CSDC_LIST 上市公司质押比例",
            "url": EASTMONEY_WEB_URL,
            "fetched_at": payload.get("fetched_at"),
            "definition_note": "公司整体质押比例（质押股数/总股本）；不是控股股东质押比例，不参与规则。",
        },
    )


def fetch_industry_compressed(refresh: bool = False) -> dict:
    path = TABLE_DIR / "industry.csv.gz"
    meta_path = path.with_name(path.name + ".meta.json")
    if path.exists() and meta_path.exists() and not refresh:
        return _read_json(meta_path)

    payload = fetch_industry(refresh=refresh)
    rows = [
        {
            "code": str(row["code"]).zfill(6),
            "industry": row.get("industry"),
            "board_name": row.get("board_name"),
        }
        for row in payload.get("rows") or []
    ]
    return write_table(
        path,
        rows,
        INDUSTRY_COLUMNS,
        metadata={
            "kind": "industry",
            "report_date": payload.get("report_date"),
            "source_id": "eastmoney.industry",
            "publisher": "东方财富",
            "interface": "RPT_LICO_FN_CPD 最新年报行业",
            "url": EASTMONEY_WEB_URL,
            "fetched_at": payload.get("fetched_at"),
            "definition_note": "最新年报所处行业；用于识别银行、保险、证券、多元金融。",
        },
    )


def fetch_tables_only() -> int:
    """补齐尚未采集的压缩表格；已存在的表跳过。"""
    for kind, (_report_name, years) in STATEMENTS.items():
        for year in years:
            meta = fetch_statement_compressed(kind, year)
            print(f"{kind} {year}: {meta['rows']} 行，{meta['size_bytes']} 字节", flush=True)
    for period in DIVIDEND_PERIODS:
        meta = fetch_dividends_compressed(period)
        print(f"dividends {period}: {meta['rows']} 行，{meta['size_bytes']} 字节", flush=True)
    print("pledge", fetch_pledge_compressed(), flush=True)
    print("industry", fetch_industry_compressed(), flush=True)
    print("表格采集完成", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
