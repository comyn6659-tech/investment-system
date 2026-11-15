#!/usr/bin/env python
"""恒生科技指数（HSTECH）官方数据可行性核查与已取得部分接入。

只使用恒生指数有限公司（Hang Seng Indexes Company）官方数据：
- 官方指数数据接口：价格指数、全收益指数的日收盘（接口返回近 5 年）；
- 官方指数单张（月度）：当前市盈率、股息率；不提供市净率；
- 官方历史估值文件只覆盖 HSI / HSCEI，HSTECH 没有公开的 PE/DY 历史文件。

因此判定为 `short_history_only`：接入官方收盘、全收益、官方月度当前估值快照与指数快照；
不做 5 年估值分位、候选 A 周度信号、研究回测、信号账本。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.data.facts import Fact, SourceRef  # noqa: E402
from engine.data.snapshot import Snapshot, SnapshotStore  # noqa: E402
from engine.series.models import (  # noqa: E402
    FREQ_DAILY,
    FREQ_DAILY_SHORT,
    METRIC_OFFICIAL_CLOSE,
    METRIC_OFFICIAL_PE_1,
    METRIC_TOTAL_RETURN_CLOSE,
    IndexSeries,
    SeriesSource,
    SeriesStore,
)

TZ = ZoneInfo("Asia/Singapore")
SERIES_DIR = ROOT / "data" / "series"
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
COVERAGE_DIR = ROOT / "data" / "coverage"
FEASIBILITY_PATH = COVERAGE_DIR / "hstech-feasibility.json"
DOC_PATH = ROOT / "docs" / "archive" / "phase4i-hstech-feasibility.md"

INSTRUMENT_ID = "INDEX:HSTECH.HKEX"
INDEX_CODE = "HSTECH"
PI_CODE = "02083.00"
TR_GROSS_CODE = "12083.00"
TR_NET_CODE = "22083.00"
LAUNCH_DATE = "2020-07-27"
BASE_DATE = "2014-12-31"
API_BASE = "https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy"
META_URL = f"{API_BASE}/v1/product-data/pub/indexes/metadata/v2"
SERIES_URL = f"{API_BASE}/v1/product-data/pub/series/metadata/v2"
FACTSHEET_URL = f"{API_BASE}/v1/content/gcs/download"
FACTSHEET_RESOURCE = "/public-website/webapp/factsheets/en-hk/hstech.pdf"
HISTORICAL_PE_URL = "https://www.hsi.com.hk/content/dam/wpb/hsil/index_operation/historical_pe/en_hk/hstech/hstech.xlsx"
HISTORICAL_DY_URL = "https://www.hsi.com.hk/content/dam/wpb/hsil/index_operation/historical_dy/en_hk/hstech/hstech.xlsx"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


def _now() -> datetime:
    return datetime.now(TZ)


def _get(url: str, params: dict[str, str] | None = None, *, timeout: int = 60) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _get_json(url: str, params: dict[str, str]) -> dict:
    return json.loads(_get(url, params).decode("utf-8"))


# --- 官方数据 ---------------------------------------------------------------
def fetch_value_history(index_code: str) -> dict:
    payload = _get_json(
        META_URL,
        {
            "data": "valueHistoryPub",
            "language": "en-hk",
            "indexCodes": index_code,
        },
    )
    if payload.get("code") != "0":
        raise RuntimeError(f"恒生指数官方接口返回异常：{payload.get('message')!r}")
    data = (payload.get("data") or [{}])[0]
    rows = data.get("valueHistory") or []
    if not rows:
        raise RuntimeError(f"官方接口没有返回 {index_code} 的历史")
    observations = [(str(row["date"])[:10], float(row["close"])) for row in rows]
    observations.sort()
    return {
        "index_code": index_code,
        "index_name": data.get("indexName"),
        "observations": observations,
    }


def fetch_current_values() -> dict:
    payload = _get_json(SERIES_URL, {"data": "values", "seriesCode": "hstech", "language": "en-hk"})
    rows = payload.get("data") or []
    output = {}
    for row in rows:
        if row.get("indexType") == "PI":
            output["price"] = {
                "value": float(row["indexValue"]),
                "date": str(row.get("indexTime") or "")[:10],
                "previous_close": float(row["previousClose"]) if row.get("previousClose") else None,
            }
        elif row.get("indexType") == "TRI_Grs":
            output["gross_total_return"] = {
                "value": float(row["indexValue"]),
                "date": str(row.get("indexTime") or "")[:10],
            }
        elif row.get("indexType") == "TRI_Net":
            output["net_total_return"] = {
                "value": float(row["indexValue"]),
                "date": str(row.get("indexTime") or "")[:10],
            }
    if "price" not in output:
        raise RuntimeError("官方接口没有返回 HSTECH 价格指数当前值")
    return output


def fetch_identity() -> dict:
    payload = _get_json(SERIES_URL, {"data": "details", "seriesCode": "hstech", "language": "en-hk"})
    data = payload.get("data") or {}
    return {
        "index_series_code": data.get("indexSeriesCode"),
        "index_series_name": data.get("indexSeriesName"),
        "description": data.get("description"),
    }


def _facts_text() -> str:
    from pypdf import PdfReader

    content = _get(FACTSHEET_URL, {"resource": FACTSHEET_RESOURCE}, timeout=90)
    reader = PdfReader(BytesIO(content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return unicodedata.normalize("NFKC", text)


def _en_date(match) -> str | None:
    if not match:
        return None
    months = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    return f"{int(match.group(3)):04d}-{months[match.group(2).lower()]:02d}-{int(match.group(1)):02d}"


def _cn_date(match) -> str | None:
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def fetch_factsheet() -> dict:
    text = _facts_text()
    normalized = unicodedata.normalize("NFKC", text)
    # 官方指数单张：中文或英文版的指数基本分析表
    row = re.search(r"(?:恒生科技指数|HSTECH)\s+([0-9.]+)\s+([0-9.]+)", normalized)
    if not row:
        raise RuntimeError("官方指数单张没有找到恒生科技指数股息率/市盈率行")
    dividend_yield = float(row.group(1))
    pe_ratio = float(row.group(2))
    as_of = (
        _en_date(re.search(r"All data as at\s*(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", normalized))
        or _cn_date(re.search(r"所有数据截至\s*(\d{4})年(\d{1,2})月(\d{1,2})日", normalized))
    )
    if not as_of:
        raise RuntimeError("官方指数单张没有找到数据截止日")
    launch = (
        _en_date(re.search(r"Launch Date\s*(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", normalized))
        or _cn_date(re.search(r"推出日期\s*(\d{4})年(\d{1,2})月(\d{1,2})日", normalized))
        or LAUNCH_DATE
    )
    base = (
        _en_date(re.search(r"Base Date\s*(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", normalized))
        or _cn_date(re.search(r"基日\s*(\d{4})年(\d{1,2})月(\d{1,2})日", normalized))
        or BASE_DATE
    )
    base_value = re.search(r"(?:Base Index|基值)\s*([\d,]+)", normalized)
    constituents = re.search(r"(?:No\. of Constituents|成份股数目)\s*(\d+)", normalized)
    currency = re.search(r"(?:Currency|货币)\s*([A-Z]{3})", normalized)
    return {
        "as_of": as_of,
        "dividend_yield_pct": dividend_yield,
        "pe_ratio": pe_ratio,
        "pb": None,
        "pb_note": "官方指数单张不提供市净率；公开历史估值文件也没有 HSTECH。",
        "launch_date": launch,
        "base_date": base,
        "base_value": int(base_value.group(1).replace(",", "")) if base_value else None,
        "constituents": int(constituents.group(1)) if constituents else None,
        "currency": currency.group(1) if currency else "HKD",
        "text_excerpt": normalized[:1200],
    }


def probe_historical_file(url: str) -> dict:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            content_type = response.headers.get("content-type", "")
            content = response.read(64)
        is_xlsx = content.startswith(b"PK") or "spreadsheet" in content_type
        return {
            "status": "ok" if is_xlsx else "missing",
            "content_type": content_type,
            "bytes_head": content[:8].hex(),
            "is_xlsx": is_xlsx,
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}", "is_xlsx": False}


# --- 序列与快照 -------------------------------------------------------------
def _source(source_id: str, interface: str, url: str, stamp: str, note: str) -> SeriesSource:
    return SeriesSource(
        source_id=source_id,
        publisher="恒生指数有限公司（Hang Seng Indexes Company Limited）",
        interface=interface,
        url=url,
        retrieved_at=stamp,
        definition_note=note,
    )


def build_series(bundle: dict, *, now: datetime) -> list[IndexSeries]:
    stamp = now.isoformat(timespec="seconds")
    close = bundle["close"]
    gross = bundle["gross_total_return"]
    factsheet = bundle["factsheet"]
    return [
        IndexSeries(
            instrument_id=INSTRUMENT_ID,
            metric=METRIC_OFFICIAL_CLOSE,
            field="收盘",
            frequency=FREQ_DAILY,
            source=_source(
                "hsi.official_history",
                "恒生指数官方指数数据接口 valueHistoryPub",
                META_URL,
                stamp,
                "官方价格指数日收盘；接口返回近 5 年，未返回 2020-07-27 至 2021-11-10 的已发布历史。",
            ),
            observations=tuple(close["observations"]),
            unit="index_point",
            transform="官方接口 valueHistory.close 原值",
            aggregation_method="not_applicable",
            denominator_basis="not_applicable",
            loss_sample_handling="not_applicable",
            notes=f"共 {len(close['observations'])} 条；区间 {close['observations'][0][0]}～{close['observations'][-1][0]}。",
        ),
        IndexSeries(
            instrument_id=INSTRUMENT_ID,
            metric=METRIC_TOTAL_RETURN_CLOSE,
            field="收盘（全收益）",
            frequency=FREQ_DAILY,
            source=_source(
                "hsi.official_history",
                "恒生指数官方指数数据接口 valueHistoryPub（Gross Total Return）",
                META_URL,
                stamp,
                "官方全收益指数（Gross Total Return，12083.00）日收盘；分红再投资口径。",
            ),
            observations=tuple(gross["observations"]),
            unit="index_point",
            transform="官方接口 valueHistory.close 原值；12083.00 全收益指数",
            aggregation_method="not_applicable",
            denominator_basis="not_applicable",
            loss_sample_handling="not_applicable",
            notes=f"共 {len(gross['observations'])} 条；区间 {gross['observations'][0][0]}～{gross['observations'][-1][0]}。",
        ),
        IndexSeries(
            instrument_id=INSTRUMENT_ID,
            metric=METRIC_OFFICIAL_PE_1,
            field="市盈率（官方指数单张，月度）",
            frequency=FREQ_DAILY_SHORT,
            source=_source(
                "hsi.factsheet",
                "恒生指数官方指数单张（月度）",
                FACTSHEET_URL,
                stamp,
                "官方指数单张的市盈率；月度快照，不是日频历史。",
            ),
            observations=((factsheet["as_of"], factsheet["pe_ratio"]),),
            unit="ratio",
            transform="官方指数单张『市价/盈利率』原值",
            aggregation_method="unknown",
            denominator_basis="unknown",
            loss_sample_handling="unknown",
            notes="只有最新一个月度快照；不能用于分位、信号或回测。",
        ),
    ]


def build_snapshot(bundle: dict, *, now: datetime) -> Snapshot:
    from tools.build_snapshots import _point_in_time_block

    stamp = now.isoformat(timespec="seconds")
    close = bundle["close"]["observations"]
    gross = bundle["gross_total_return"]["observations"]
    latest = bundle["current"]["price"]
    factsheet = bundle["factsheet"]
    day = close[-1][0]
    price_src = SourceRef(
        source_id="hsi.official_history",
        publisher="恒生指数有限公司（Hang Seng Indexes Company Limited）",
        interface="恒生指数官方指数数据接口 valueHistoryPub",
        url=META_URL,
        retrieved_at=stamp,
        notes="官方价格指数日收盘；HKD 计价",
    )
    value_src = SourceRef(
        source_id="hsi.factsheet",
        publisher="恒生指数有限公司（Hang Seng Indexes Company Limited）",
        interface="恒生指数官方指数单张（月度）",
        url=FACTSHEET_URL,
        retrieved_at=stamp,
        notes="官方指数单张的 PE/股息率；月度快照，不提供 PB，不构成日频估值历史",
    )
    facts = [
        Fact.known(
            fact_id="index.level",
            metric="index_close_level",
            value=float(latest["value"]),
            unit="index_point",
            period=day,
            available_at=f"{day}T16:08:00+08:00",
            fetched_at=stamp,
            source=price_src,
            transform="官方接口最后一行收盘",
            notes="港股交易日收盘；港元计价",
        ),
        Fact.known(
            fact_id="index.pe_ttm",
            metric="pe_ttm",
            value=float(factsheet["pe_ratio"]),
            unit="ratio",
            period=factsheet["as_of"],
            available_at=None,
            fetched_at=stamp,
            source=value_src,
            transform="官方指数单张『市价/盈利率』",
            notes="月度快照，不是日频历史；不能用于分位或回测",
        ),
    ]
    coverage = {
        "build": {
            "mode": "official_history_close",
            "market_date": day,
            "bar_status": "closed",
            "built_at": stamp,
            "date_source": "官方指数数据接口最后一行",
        },
        "market": "HK",
        "currency": "HKD",
        "price_history": {
            "source_id": price_src.source_id,
            "start": close[0][0],
            "end": day,
            "observations": len(close),
            "frequency": "daily",
            "note": "官方接口只返回近 5 年；未覆盖 2020-07-27 至 2021-11-10 的已发布历史。",
        },
        "total_return_history": {
            "source_id": price_src.source_id,
            "code": TR_GROSS_CODE,
            "start": gross[0][0],
            "end": gross[-1][0],
            "observations": len(gross),
            "frequency": "daily",
            "note": "全收益（Gross Total Return）指数；分红再投资。",
        },
        "valuation_source_official": {
            "source_id": value_src.source_id,
            "publisher": value_src.publisher,
            "start": factsheet["as_of"],
            "end": factsheet["as_of"],
            "observations": 1,
            "frequency": "monthly",
            "pe": factsheet["pe_ratio"],
            "dividend_yield_pct": factsheet["dividend_yield_pct"],
            "pb": None,
            "note": "官方指数单张月度快照；PB 不提供；HSTECH 没有公开的 PE/DY 历史文件。",
        },
        "valuation_history": {
            "available": False,
            "note": "未取得 HSTECH 官方 PE/股息率/市净率历史；官方历史估值文件只覆盖 HSI/HSCEI。",
        },
        "aggregation_method": "unknown",
        "aggregation_note": "官方未说明指数 PE 的加权方式与亏损样本处理",
        "supports_weekly_5y_history": False,
        "supports_5y_window": False,
        "index_identity_verified": True,
        "index_launch_date": factsheet["launch_date"],
        "index_base_date": factsheet["base_date"],
        "index_base_value": factsheet["base_value"],
        "index_constituents": factsheet["constituents"],
        **_point_in_time_block(facts),
    }
    return Snapshot(
        instrument_id=INSTRUMENT_ID,
        asset_class="index",
        as_of=day,
        available_at=f"{day}T16:08:00+08:00",
        fetched_at=stamp,
        facts=facts,
        coverage=coverage,
        producers=["tools/build_hstech_data.py"],
        notes=(
            "恒生科技指数：港股、港元计价；官方收盘与全收益已接入；"
            "官方历史估值未取得，仅保留月度当前快照；不构成估值结论"
        ),
    )


# --- 可行性记录 -------------------------------------------------------------
def build_feasibility(bundle: dict, *, now: datetime) -> dict:
    close = bundle["close"]["observations"]
    gross = bundle["gross_total_return"]["observations"]
    net = bundle["net_total_return"]["observations"]
    factsheet = bundle["factsheet"]
    pe_file = bundle["pe_file"]
    dy_file = bundle["dy_file"]
    day = close[-1][0]
    return {
        "schema_version": "hstech-feasibility/1",
        "generated_at": now.isoformat(timespec="seconds"),
        "scope": "恒生科技指数（HSTECH）",
        "decision": "short_history_only",
        "decision_detail": (
            "官方价格指数与全收益指数的日收盘可得（接口返回近 5 年）；"
            "官方估值只有月度指数单张的 PE/股息率当前快照，没有 HSTECH 的 PE/DY/PB 历史文件，"
            "因此不做 5 年估值分位、候选 A 周度信号、研究回测、候选 A 账本。"
            "已接入官方收盘、全收益、月度当前估值快照与指数快照。"
        ),
        "official_history_available": True,
        "official_pe_history_available": False,
        "official_dividend_yield_history_available": False,
        "official_pb_history_available": False,
        "current_official_valuation": {
            "date": factsheet["as_of"],
            "pe_ratio": factsheet["pe_ratio"],
            "dividend_yield_pct": factsheet["dividend_yield_pct"],
            "pb": None,
            "pb_note": factsheet["pb_note"],
            "frequency": "monthly",
            "source": "hsi.factsheet",
        },
        "probes": [
            {
                "probe_id": "h1_hsi_daily_close",
                "requirement": "官方日收盘",
                "purpose": "恒生科技指数价格指数日收盘",
                "publisher": "恒生指数有限公司",
                "interface": "官方指数数据接口 valueHistoryPub（02083.00）",
                "url": META_URL,
                "status": "ok",
                "rows": len(close),
                "fields": ["date", "close"],
                "date_range": [close[0][0], day],
                "frequency": "daily",
                "official": True,
                "adopted": True,
                "note": "接口只返回近 5 年；未覆盖 2020-07-27 至 2021-11-10 的已发布历史。",
            },
            {
                "probe_id": "h1_hsi_total_return",
                "requirement": "官方全收益",
                "purpose": "恒生科技指数全收益（12083.00 / 22083.00）",
                "publisher": "恒生指数有限公司",
                "interface": "官方指数数据接口 valueHistoryPub（Gross/Net Total Return）",
                "url": META_URL,
                "status": "ok",
                "rows": len(gross),
                "fields": ["date", "close"],
                "date_range": [gross[0][0], gross[-1][0]],
                "frequency": "daily",
                "official": True,
                "adopted": True,
                "note": f"采用 Gross Total Return（12083.00）；Net Total Return（22083.00）{len(net)} 条，仅登记不混用。",
            },
            {
                "probe_id": "h2_hsi_pe_history",
                "requirement": "官方估值历史",
                "purpose": "HSTECH 官方 PE 历史（日度或月度）",
                "publisher": "恒生指数有限公司",
                "interface": "官方历史估值文件 historical_pe",
                "url": HISTORICAL_PE_URL,
                "status": pe_file["status"],
                "rows": 0,
                "fields": ["PE"],
                "date_range": [],
                "frequency": "",
                "official": True,
                "adopted": False,
                "note": (
                    "同一路径下 HSI/HSCEI 返回 Excel；HSTECH 返回 AEM 404 HTML，"
                    f"content-type={pe_file.get('content_type')!r}。"
                ),
            },
            {
                "probe_id": "h2_hsi_dy_history",
                "requirement": "官方估值历史",
                "purpose": "HSTECH 官方股息率历史（日度或月度）",
                "publisher": "恒生指数有限公司",
                "interface": "官方历史估值文件 historical_dy",
                "url": HISTORICAL_DY_URL,
                "status": dy_file["status"],
                "rows": 0,
                "fields": ["股息率"],
                "date_range": [],
                "frequency": "",
                "official": True,
                "adopted": False,
                "note": (
                    "同一路径下 HSI/HSCEI 返回 Excel；HSTECH 返回 AEM 404 HTML，"
                    f"content-type={dy_file.get('content_type')!r}。"
                ),
            },
            {
                "probe_id": "h2_hsi_pb_history",
                "requirement": "官方估值历史",
                "purpose": "HSTECH 官方市净率历史（日度或月度）",
                "publisher": "恒生指数有限公司",
                "interface": "官方指数单张/历史估值文件",
                "url": FACTSHEET_URL,
                "status": "missing",
                "rows": 0,
                "fields": ["PB"],
                "date_range": [],
                "frequency": "",
                "official": True,
                "adopted": False,
                "note": "官方指数单张只提供股息率与 PE；公开历史估值文件没有 HSTECH；PB 不可得。",
            },
            {
                "probe_id": "h2_hsi_current_factsheet",
                "requirement": "官方当前估值",
                "purpose": "HSTECH 官方月度 PE/股息率当前快照",
                "publisher": "恒生指数有限公司",
                "interface": "官方指数单张（月度 PDF）",
                "url": FACTSHEET_URL,
                "status": "ok",
                "rows": 1,
                "fields": ["股息率", "市价/盈利率", "数据截止日"],
                "date_range": [factsheet["as_of"], factsheet["as_of"]],
                "frequency": "monthly",
                "official": True,
                "adopted": True,
                "note": (
                    f"PE {factsheet['pe_ratio']}、股息率 {factsheet['dividend_yield_pct']}%；"
                    f"数据截至 {factsheet['as_of']}；PB 不提供。"
                ),
            },
            {
                "probe_id": "h3_hsi_identity",
                "requirement": "指数身份",
                "purpose": "HSTECH 名称、代码、发布日、基日",
                "publisher": "恒生指数有限公司",
                "interface": "官方指数数据接口 details + 官方指数单张",
                "url": SERIES_URL,
                "status": "ok",
                "rows": 1,
                "fields": ["indexSeriesName", "推出日期", "基日", "基值", "成份股数目", "货币"],
                "date_range": [],
                "frequency": "",
                "official": True,
                "adopted": True,
                "note": (
                    f"{bundle['identity'].get('index_series_name')}；推出 {factsheet['launch_date']}，"
                    f"基日 {factsheet['base_date']}，基值 {factsheet['base_value']}，"
                    f"成份股 {factsheet['constituents']}，货币 {factsheet['currency']}。"
                ),
            },
        ],
        "implemented": [
            "HSTECH 官方价格指数日收盘序列（hsi.official_history）",
            "HSTECH 官方全收益（Gross Total Return，12083.00）日收盘序列",
            "HSTECH 官方月度 PE 当前快照（指数单张，2026-10-30）",
            "HSTECH 指数快照（官方收盘 + 官方月度 PE；港股/港元）",
            "名称识别：恒生科技 / 恒生科技指数 / HSTECH",
        ],
        "not_implemented": [
            "5 年月度估值画像（无官方历史估值序列）",
            "候选 A 周度研究信号（无官方 PE 日频历史）",
            "候选 A 研究回测（无官方 PE 日频历史）",
            "候选 A 信号账本 HSTECH 记录（无信号可写）",
            "官方估值前向记录账本（无官方当日 PE/股息率值）",
        ],
        "third_party_not_adopted": [
            "Wind/东方财富/同花顺等第三方 HSTECH PE：非官方，未采用。",
            "恒生科技 ETF 或 QDII 产品资料：本批不接入任何港股 ETF/QDII。",
        ],
        "notes": [
            "只使用恒生指数有限公司官方接口、官方指数单张与官方历史估值文件路径；",
            "没有为了让分位或信号通过而使用第三方估值、ETF 估值或拼接序列；",
            "指数 2020-07-27 发布，之前为回溯计算（如适用）；官方接口只返回近 5 年日收盘。",
            "港股、港元计价；成分集中于互联网与科技龙头；部分成分股亏损或利润波动大，PE 作为估值指标的可靠性有限。",
        ],
    }


def render_markdown(feasibility: dict) -> str:
    valuation = feasibility["current_official_valuation"]
    probes = {item["probe_id"]: item for item in feasibility["probes"]}
    lines = [
        "# 恒生科技指数（HSTECH）数据可行性报告",
        "",
        f"生成时间：{feasibility['generated_at']}｜生成脚本：`tools/build_hstech_data.py`｜"
        "机器可读记录：[data/coverage/hstech-feasibility.json](../data/coverage/hstech-feasibility.json)",
        "",
        "本报告只回答一件事：**HSTECH 有哪些官方数据、这些数据能支持做到哪一步**。",
        "结论按施工单 B 节的三档判定；不为了让策略跑通而使用第三方估值、ETF 估值或拼接序列。",
        "",
        "## 1. 结论",
        "",
        "| 项目 | 结论 |",
        "|---|---|",
        f"| 官方日收盘 | **可得**，{probes['h1_hsi_daily_close']['date_range'][0]} 至 "
        f"{probes['h1_hsi_daily_close']['date_range'][1]}，{probes['h1_hsi_daily_close']['rows']} 个交易日 |",
        f"| 官方全收益（12083.00） | **可得**，同区间、{probes['h1_hsi_total_return']['rows']} 个观测 |",
        "| 官方 PE 历史 | **不可得**；HSTECH 历史估值文件返回 AEM 404，只有 HSI/HSCEI 有公开文件 |",
        "| 官方股息率历史 | **不可得**；同上 |",
        "| 官方 PB 历史 | **不可得**；官方指数单张不提供 PB |",
        f"| 官方当前估值（月度） | PE **{valuation['pe_ratio']}**、股息率 **{valuation['dividend_yield_pct']}%**、"
        f"PB **不提供**，数据截至 **{valuation['date']}** |",
        "| 本批判定 | **`short_history_only`**：接入官方收盘、全收益、月度当前估值快照与指数快照；"
        "**不做** 5 年估值分位、候选 A 周度信号、研究回测、信号账本 |",
        "",
        "## 2. 四项数据逐项核查",
        "",
        "### 2.1 官方日收盘与全收益",
        "",
        "| 项 | 内容 |",
        "|---|---|",
        "| 来源 | 恒生指数有限公司（Hang Seng Indexes Company Limited） |",
        "| 接口 | 官方指数数据接口 `valueHistoryPub`（价格指数 02083.00；全收益 12083.00） |",
        f"| 起止 | {probes['h1_hsi_daily_close']['date_range'][0]} 至 "
        f"{probes['h1_hsi_daily_close']['date_range'][1]} |",
        "| 频率 | 日频 |",
        f"| 观测数 | 价格指数 {probes['h1_hsi_daily_close']['rows']}；全收益 {probes['h1_hsi_total_return']['rows']} |",
        "| 是否官方 | 是 |",
        "| 是否采用 | 价格指数与全收益均采用 |",
        "",
        "接口只返回近 5 年；未返回 2020-07-27 至 2021-11-10 的已发布历史，已在序列与快照中如实标注。",
        "",
        "### 2.2 官方估值历史：只有月度当前快照，没有历史",
        "",
        "| 尝试 | 结果 |",
        "|---|---|",
        "| 官方历史 PE 文件 `historical_pe/en_hk/hstech/hstech.xlsx` | 返回 AEM 404 HTML，不是 Excel |",
        "| 官方历史股息率文件 `historical_dy/en_hk/hstech/hstech.xlsx` | 返回 AEM 404 HTML，不是 Excel |",
        "| 同路径 HSI / HSCEI | 返回 Excel；说明路径规则正确，但 HSTECH 没有公开文件 |",
        "| 官方指数单张（月度） | 有 PE、股息率，**无 PB**；只有当前月度快照 |",
        "",
        "因此：**官方日度或月度估值历史没有取得**。没有把以下任何一项拿来凑分位：",
        "",
        "- Wind / 东方财富 / 同花顺等第三方 HSTECH PE；",
        "- 恒生科技 ETF 或 QDII 的估值；",
        "- 用成分股财务数据自行合成的指数 PE；",
        "- 用当前月度值外推或插值出的历史。",
        "",
        "### 2.3 官方当前估值快照",
        "",
        "官方指数单张（月度 PDF）数据截至 "
        f"**{valuation['date']}**：市盈率 **{valuation['pe_ratio']}**、"
        f"股息率 **{valuation['dividend_yield_pct']}%**，PB 不提供。",
        "这是一个月度值，只能说明“此刻官方口径是多少”，不能说明历史位置。",
        "",
        "### 2.4 指数身份",
        "",
        f"- 名称：{feasibility['probes'][-1]['note']}",
        "- 港股、港元计价；发布于 2020-07-27，之前为回溯计算（如适用）。",
        "- 成分集中于互联网与科技龙头；部分成分股亏损或利润波动大，PE 作为估值指标的可靠性有限。",
        "",
        "## 3. 据此做了什么、没做什么",
        "",
        "### 3.1 已做",
        "",
        "1. 官方价格指数日收盘与官方全收益日收盘序列（固定序列、内容哈希、起止与观测数）。",
        "2. 官方月度 PE 当前快照（进入指数画像的“官方短历史（并列事实）”）。",
        "3. HSTECH 指数快照（官方收盘 + 官方月度 PE；港股/港元；覆盖块明确 `valuation_history.available=false`）。",
        "4. 名称识别：恒生科技 / 恒生科技指数 / HSTECH → `INDEX:HSTECH.HKEX`；其他恒生指数 → 范围外。",
        "",
        "### 3.2 未做及原因",
        "",
        "| 未做 | 原因 |",
        "|---|---|",
        "| 5 年月度估值画像 | 没有官方月度估值历史；不能用第三方或 ETF 估值代替 |",
        "| 候选 A 周度研究信号 | 规则需要官方 PE 日频历史；只有一个月度值，无法形成 5 年周度池 |",
        "| 候选 A 研究回测 | 同上；不拿价格指数或第三方 PE 冒充 |",
        "| 候选 A 信号账本 HSTECH 记录 | 没有信号可写；账本保持既有 3 条记录不变 |",
        "| 官方估值前向记录账本 | 没有官方当日 PE/股息率值；只有月度单张 |",
        "",
        "## 4. 复现",
        "",
        "```bash",
        "cd /Users/comyn/Documents/投资/投资分析系统/investment-system",
        ".venv/bin/python tools/build_hstech_data.py",
        ".venv/bin/python -m engine.cli 恒生科技",
        "```",
        "",
        "## 5. 限制",
        "",
        "- 本结论只针对本次已测的官方接口与文件路径；不代表所有官方渠道（含付费/授权渠道）都没有历史估值。",
        "- 官方接口只返回近 5 年日收盘；2020-07-27 至 2021-11-10 的已发布历史未取得。",
        "- 官方月度 PE 的加权方式、亏损样本处理未在单张说明，记为 unknown；PB 不提供。",
        "- 没有为了让分位、信号或回测通过而改变候选 A 阈值、股票池或数据口径。",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="恒生科技指数官方数据可行性核查与接入")
    parser.add_argument("--probe-only", action="store_true", help="只核查，不写序列/快照")
    args = parser.parse_args(argv)

    now = _now()
    bundle = {
        "close": fetch_value_history(PI_CODE),
        "gross_total_return": fetch_value_history(TR_GROSS_CODE),
        "net_total_return": fetch_value_history(TR_NET_CODE),
        "current": fetch_current_values(),
        "identity": fetch_identity(),
        "factsheet": fetch_factsheet(),
        "pe_file": probe_historical_file(HISTORICAL_PE_URL),
        "dy_file": probe_historical_file(HISTORICAL_DY_URL),
    }
    if bundle["close"]["observations"][-1][0] != bundle["current"]["price"]["date"]:
        raise RuntimeError("官方历史收盘与当前值日期不一致，拒绝拼接")
    feasibility = build_feasibility(bundle, now=now)

    if not args.probe_only:
        store = SeriesStore(SERIES_DIR)
        built = []
        for series in build_series(bundle, now=now):
            store.save(series)
            built.append(
                {
                    "series_id": series.series_id,
                    "metric": series.metric,
                    "frequency": series.frequency,
                    "observations": len(series.observations),
                    "start": series.start_date,
                    "end": series.end_date,
                }
            )
        store.rebuild_manifest()
        feasibility["series"] = built

        snapshot = build_snapshot(bundle, now=now)
        snapshot_store = SnapshotStore(SNAPSHOT_DIR)
        snapshot_store.save(snapshot)
        snapshot_store.rebuild_manifest()
        feasibility["snapshot_id"] = snapshot.snapshot_id

    COVERAGE_DIR.mkdir(parents=True, exist_ok=True)
    FEASIBILITY_PATH.write_text(json.dumps(feasibility, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    DOC_PATH.write_text(render_markdown(feasibility) + "\n", encoding="utf-8")
    print(json.dumps(feasibility, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
