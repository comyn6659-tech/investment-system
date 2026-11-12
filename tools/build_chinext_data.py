#!/usr/bin/env python
"""创业板指（399006）数据可行性核查与官方序列接入。

本工具只做两件事：
1. 逐项核查创业板指的官方数据（收盘、全收益、估值、指数身份）与跟踪 ETF 资料；
2. 把**已取得的官方数据**接入现有序列库与快照库，不把第三方估值拼进来。

可行性结论按施工单 §1 的三档判定：
- 官方日度 PE 且自 2016 年前起 → 全量；
- 官方月度或更短历史 → 只做能做的部分；
- 官方估值不可得 → 只接收盘、全收益与 ETF 资料。

当前实测：国证指数官网只提供**最新一个交易日**的 PE/PB 快照，
没有可下载的日度或月度历史；深交所公开接口只有指数行情与行业加权 PE，
没有创业板指估值历史。因此结论为 `short_history_only`：接入收盘、
全收益、官方当前估值快照与 ETF 资料；不计算分位、不生成信号、不回测。

用法：
    .venv/bin/python tools/build_chinext_data.py --probe-only
    .venv/bin/python tools/build_chinext_data.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime
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
    METRIC_OFFICIAL_PB_1,
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
FEASIBILITY_PATH = COVERAGE_DIR / "chinext-feasibility.json"

INDEX_CODE = "399006"
TR_CODE = "399606"
INSTRUMENT_ID = "INDEX:399006.SZ"
LAUNCH_DATE = "2010-06-01"  # 发布日；基日 2010-05-31 的基点行不进入序列
CNI_HIST_URL = (
    "http://hq.cnindex.com.cn/market/market/getIndexDailyDataWithDataFormat"
)
CNI_LIST_URL = "https://www.cnindex.com.cn/index/indexList"
CNI_DETAIL_URL = "https://www.cnindex.com.cn/index/selectIndexByCode"
SZSE_LIST_URL = "http://www.szse.cn/api/report/ShowReport/data"
SZSE_LIST_PARAMS = {
    "SHOWTYPE": "JSON",
    "CATALOGID": "1812_zs",
    "TABKEY": "tab1",
    "txtQueryDate": INDEX_CODE,
}
CEIC_URL = (
    "https://www.ceicdata.com.cn/zh-hans/china/"
    "shenzhen-stock-exchange-chinext-pe-and-pb-ratio-daily"
)
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"


def _now() -> datetime:
    return datetime.now(TZ)


def _get(url: str, params: dict[str, str] | None = None, *, timeout: int = 30) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _get_json(url: str, params: dict[str, str] | None = None) -> dict:
    return json.loads(_get(url, params).decode("utf-8"))


# --- 官方行情 ---------------------------------------------------------------
def fetch_cni_history(code: str) -> dict:
    """国证指数官方历史行情（日频，收盘点位）。"""
    payload = _get_json(
        CNI_HIST_URL,
        {
            "indexCode": code,
            "startDate": "2010-01-01",
            "endDate": _now().date().isoformat(),
            "frequency": "day",
        },
    )
    if payload.get("code") != 200:
        raise RuntimeError(f"国证指数历史行情返回异常: {payload.get('message')!r}")
    data = payload["data"]
    columns = list(data["item"])
    close_index = columns.index("close")
    rows = []
    for raw in data["data"]:
        day = str(raw[columns.index("timestamp")])[:10]
        value = raw[close_index]
        if value is None:
            continue
        rows.append((day, float(value)))
    rows.sort()
    # 基日 2010-05-31 的 1000 点是基点，不是发布后的交易收盘；按发布日截断。
    rows = [(d, v) for d, v in rows if d >= LAUNCH_DATE]
    return {"code": code, "columns": columns, "rows": rows, "raw_rows": len(data["data"])}


def fetch_cni_detail() -> dict:
    payload = _get_json(CNI_DETAIL_URL, {"codeValue": INDEX_CODE})
    data = payload.get("data") or {}
    return {
        "indexcode": data.get("indexcode"),
        "indexname": data.get("indexname"),
        "indexfullcname": data.get("indexfullcname"),
        "base_date": data.get("jr"),
    }


def fetch_cni_current_valuation() -> dict:
    payload = _get_json(
        CNI_LIST_URL, {"channelCode": "-1", "rows": "2000", "pageNum": "1"}
    )
    rows = ((payload.get("data") or {}).get("rows")) or []
    for row in rows:
        if str(row.get("indexcode")) == INDEX_CODE:
            return {
                "indexcode": row.get("indexcode"),
                "indexname": row.get("indexname"),
                "close": float(row.get("closeingPoint")),
                "pe_static": float(row.get("peStatic")),
                "pe_dynamic": float(row.get("peDynamic")),
                "pb": float(row.get("pb")),
                "fields": sorted(row.keys()),
            }
    raise RuntimeError("国证指数官网指数列表中没有 399006")


def fetch_szse_index_list() -> dict:
    payload = json.loads(_get(SZSE_LIST_URL, SZSE_LIST_PARAMS).decode("utf-8"))
    rows = (payload[0].get("data") or []) if payload else []
    if not rows:
        raise RuntimeError("深交所指数列表没有返回 399006")
    row = rows[0]

    def text(value: str) -> str:
        return re.sub(r"<[^>]+>", "", str(value)).strip()

    return {
        "code": text(row.get("zsdm", "")),
        "name": text(row.get("zsmc", "")),
        "base_date": text(row.get("jrnew", "")),
        "base_value": text(row.get("jrzs", "")),
        "start_date": text(row.get("qsrnew", "")),
    }


def probe_szse_index_quote() -> dict:
    payload = json.loads(
        _get(
            SZSE_LIST_URL,
            {
                "SHOWTYPE": "JSON",
                "CATALOGID": "1826_snapshot",
                "TABKEY": "tab1",
                "txtDMorJC": INDEX_CODE,
                "txtBeginDate": "2026-11-07",
                "txtEndDate": "2026-11-11",
            },
        ).decode("utf-8")
    )
    meta = payload[0].get("metadata") if payload else {}
    cols = meta.get("cols") or {}
    return {
        "columns": list(cols.values()),
        "rows": len(payload[0].get("data") or []) if payload else 0,
        "has_pe": any("市盈" in str(v) for v in cols.values()),
    }


def probe_szse_industry_pe() -> dict:
    payload = json.loads(
        _get(
            SZSE_LIST_URL,
            {
                "SHOWTYPE": "JSON",
                "CATALOGID": "1804_gptj_merge_after",
                "TABKEY": "tab4",
                "txtQueryDate": "2026-11-11",
            },
        ).decode("utf-8")
    )
    meta = payload[0].get("metadata") if payload else {}
    cols = meta.get("cols") or {}
    return {
        "columns": list(cols.values()),
        "has_weighted_pe": any("市盈率" in str(v) for v in cols.values()),
        "rows": len(payload[0].get("data") or []) if payload else 0,
    }


def probe_ceic_board_pe() -> dict:
    """第三方转载的创业板**板块** PE：只登记「存在但未采用」。"""
    try:
        html = _get(CEIC_URL).decode("utf-8", errors="ignore")
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        description = re.search(
            r'<meta name="description" content="([^"]*)"', html
        )
        return {
            "status": "ok",
            "title": re.sub(r"\s+", " ", title.group(1)).strip() if title else None,
            "description": (
                re.sub(r"\s+", " ", description.group(1)).strip()
                if description
                else None
            ),
        }
    except Exception as exc:  # 第三方页面可达性不影响本批结论
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


# --- 序列与快照 -------------------------------------------------------------
def _source(kind: str, stamp: str) -> SeriesSource:
    if kind == "pe_short":
        return SeriesSource(
            source_id="cnindex.index_list",
            publisher="深圳证券信息有限公司（国证指数）",
            interface="国证指数官网指数列表接口（当前交易日快照）",
            url=CNI_LIST_URL,
            retrieved_at=stamp,
            definition_note=(
                "官网指数列表只提供最新交易日的 PE（静态/滚动）与 PB；"
                "不提供历史下载，未说明加权方式与亏损样本处理。"
            ),
        )
    if kind == "total_return":
        return SeriesSource(
            source_id="cnindex.official_history",
            publisher="深圳证券信息有限公司（国证指数）",
            interface="国证指数官方历史行情接口",
            url=CNI_HIST_URL,
            retrieved_at=stamp,
            definition_note=(
                "官方指数历史行情；全收益指数 399606（分红再投资）的收盘点位。"
                "接口不提供公布时刻与修订版本。"
            ),
        )
    return SeriesSource(
        source_id="cnindex.official_history",
        publisher="深圳证券信息有限公司（国证指数）",
        interface="国证指数官方历史行情接口",
        url=CNI_HIST_URL,
        retrieved_at=stamp,
        definition_note=(
            "官方指数历史行情；价格指数收盘点位，不含分红。"
            "接口不提供公布时刻与修订版本。"
        ),
    )


def _series(
    *,
    kind: str,
    code: str,
    metric: str,
    field: str,
    frequency: str,
    observations: tuple[tuple[str, float], ...],
    stamp: str,
    transform: str,
    notes: str,
    unit: str,
) -> IndexSeries:
    tr = kind == "total_return"
    return IndexSeries(
        instrument_id=INSTRUMENT_ID,
        metric=metric,
        field=field,
        frequency=frequency,
        source=_source(kind, stamp),
        observations=observations,
        unit=unit,
        transform=transform,
        aggregation_method="not_applicable" if unit == "index_point" else "unknown",
        denominator_basis="not_applicable" if unit == "index_point" else "unknown",
        loss_sample_handling="not_applicable" if unit == "index_point" else "unknown",
        revision_availability="unknown",
        notes=notes
        + (
            (
                f"；全收益指数代码 {code}，只保留发布日 {LAUNCH_DATE} 及之后的记录"
                if tr
                else f"；只保留发布日 {LAUNCH_DATE} 及之后的记录，"
                f"基日 {LAUNCH_DATE} 之前的基点行已丢弃"
            )
            if unit == "index_point"
            else ""
        ),
    )


def build_series(bundle: dict, *, now: datetime) -> list[IndexSeries]:
    stamp = now.isoformat(timespec="seconds")
    close_rows = bundle["close"]["rows"]
    tr_rows = bundle["total_return"]["rows"]
    valuation = bundle["valuation"]
    val_date = close_rows[-1][0]
    if valuation["close"] != close_rows[-1][1]:
        raise RuntimeError(
            f"官网当前快照收盘 {valuation['close']} 与历史行情最后收盘 "
            f"{close_rows[-1][1]} 不一致，拒绝拼接"
        )

    count_note = f"共 {len(close_rows)} 条；区间 {close_rows[0][0]}～{close_rows[-1][0]}"
    return [
        _series(
            kind="close",
            code=INDEX_CODE,
            metric=METRIC_OFFICIAL_CLOSE,
            field="收盘",
            frequency=FREQ_DAILY,
            observations=tuple(close_rows),
            stamp=stamp,
            transform="官方历史行情接口『收盘』原值；价格指数，不含分红",
            notes=f"{count_note}。",
            unit="index_point",
        ),
        _series(
            kind="total_return",
            code=TR_CODE,
            metric=METRIC_TOTAL_RETURN_CLOSE,
            field="收盘",
            frequency=FREQ_DAILY,
            observations=tuple(tr_rows),
            stamp=stamp,
            transform=f"全收益指数 {TR_CODE} 官方历史行情『收盘』原值",
            notes=(
                f"全收益口径（分红再投资），共 {len(tr_rows)} 条；"
                f"区间 {tr_rows[0][0]}～{tr_rows[-1][0]}。"
            ),
            unit="index_point",
        ),
        _series(
            kind="pe_short",
            code=INDEX_CODE,
            metric=METRIC_OFFICIAL_PE_1,
            field="滚动市盈率（官方当前快照）",
            frequency=FREQ_DAILY_SHORT,
            observations=((val_date, valuation["pe_dynamic"]),),
            stamp=stamp,
            transform="官网指数列表『PE滚动』原值，未做换算",
            notes=(
                "只有最新一个交易日，作为并列事实；样本不足，不参与分位、"
                "不生成信号、不回测。"
            ),
            unit="ratio",
        ),
        _series(
            kind="pe_short",
            code=INDEX_CODE,
            metric=METRIC_OFFICIAL_PB_1,
            field="市净率（官方当前快照）",
            frequency=FREQ_DAILY_SHORT,
            observations=((val_date, valuation["pb"]),),
            stamp=stamp,
            transform="官网指数列表『PB』原值，未做换算",
            notes="只有最新一个交易日，作为并列事实；不参与分位。",
            unit="ratio",
        ),
    ]


def build_snapshot(bundle: dict, *, now: datetime) -> Snapshot:
    from tools.build_snapshots import _point_in_time_block

    valuation = bundle["valuation"]
    close_rows = bundle["close"]["rows"]
    tr_rows = bundle["total_return"]["rows"]
    day = close_rows[-1][0]
    stamp = now.isoformat(timespec="seconds")
    price_src = SourceRef(
        source_id="cnindex.official_history",
        publisher="深圳证券信息有限公司（国证指数）",
        interface="国证指数官方历史行情接口",
        url=CNI_HIST_URL,
        retrieved_at=stamp,
        notes="官方指数历史行情；收盘点位",
    )
    value_src = SourceRef(
        source_id="cnindex.index_list",
        publisher="深圳证券信息有限公司（国证指数）",
        interface="国证指数官网指数列表接口（当前交易日快照）",
        url=CNI_LIST_URL,
        retrieved_at=stamp,
        notes="官网只提供最新交易日的 PE/PB，无历史",
    )
    facts = [
        Fact.known(
            fact_id="index.level",
            metric="index_close_level",
            value=float(close_rows[-1][1]),
            unit="index_point",
            period=day,
            available_at=f"{day}T15:00:00+08:00",
            fetched_at=stamp,
            source=price_src,
            transform="官方历史行情最后一行收盘",
            notes="已收盘交易日的收盘点位；价格指数，不含分红",
        ),
        Fact.known(
            fact_id="index.pe_ttm",
            metric="pe_ttm",
            value=float(valuation["pe_dynamic"]),
            unit="ratio",
            period=day,
            available_at=None,
            fetched_at=stamp,
            source=value_src,
            transform="官网指数列表『PE滚动』",
            notes="官方当前快照，只有 1 个交易日；不能用于分位或回测",
        ),
        Fact.known(
            fact_id="index.pb",
            metric="pb",
            value=float(valuation["pb"]),
            unit="ratio",
            period=day,
            available_at=None,
            fetched_at=stamp,
            source=value_src,
            transform="官网指数列表『PB』",
            notes="官方当前快照，只有 1 个交易日；不能用于分位",
        ),
    ]
    coverage = {
        "build": {
            "mode": "official_history_close",
            "market_date": day,
            "bar_status": "closed",
            "built_at": stamp,
            "date_source": "官方历史行情最后一行",
        },
        "price_history": {
            "source_id": price_src.source_id,
            "start": close_rows[0][0],
            "end": day,
            "observations": len(close_rows),
            "frequency": "daily",
        },
        "total_return_history": {
            "source_id": price_src.source_id,
            "code": TR_CODE,
            "start": tr_rows[0][0],
            "end": tr_rows[-1][0],
            "observations": len(tr_rows),
            "frequency": "daily",
        },
        "valuation_source_official": {
            "source_id": value_src.source_id,
            "publisher": value_src.publisher,
            "start": day,
            "end": day,
            "observations": 1,
            "frequency": "snapshot",
            "note": "官网只提供最新交易日 PE/PB，没有日度或月度历史，不能计算 5 年分位",
        },
        "valuation_history": {
            "available": False,
            "note": "未取得创业板指的官方历史估值序列；第三方板块 PE 与指数 PE 不是同一对象，未采用",
        },
        "aggregation_method": "unknown",
        "aggregation_note": "官网未说明指数 PE/PB 的加权方式与亏损样本处理",
        "supports_weekly_5y_history": False,
        "supports_5y_window": False,
        "index_identity_verified": True,
        **_point_in_time_block(facts),
    }
    return Snapshot(
        instrument_id=INSTRUMENT_ID,
        asset_class="index",
        as_of=day,
        available_at=f"{day}T15:00:00+08:00",
        fetched_at=stamp,
        facts=facts,
        coverage=coverage,
        producers=["tools/build_chinext_data.py"],
        notes=(
            "创业板指：官方收盘与全收益已接入；官方历史估值不可得，"
            "仅保留当前快照，不构成估值结论"
        ),
    )


# --- 可行性记录 -------------------------------------------------------------
def _probe_record(**kwargs) -> dict:
    base = {
        "probe_id": "",
        "requirement": "",
        "purpose": "",
        "publisher": "",
        "interface": "",
        "url": "",
        "status": "",
        "rows": None,
        "fields": [],
        "date_range": [],
        "frequency": "",
        "official": False,
        "adopted": False,
        "note": "",
    }
    base.update(kwargs)
    return base


def build_feasibility(bundle: dict, *, now: datetime) -> dict:
    close = bundle["close"]
    tr = bundle["total_return"]
    val = bundle["valuation"]
    szse = bundle["szse_list"]
    quote = bundle["szse_quote"]
    industry = bundle["szse_industry"]
    detail = bundle["detail"]
    ceic = bundle["ceic"]
    etf = bundle["etf"]
    day = close["rows"][-1][0]

    probes = [
        _probe_record(
            probe_id="c1_cni_daily_close_399006",
            requirement="官方日收盘",
            purpose="创业板指官方日频收盘",
            publisher="深圳证券信息有限公司（国证指数）",
            interface="国证指数官方历史行情接口",
            url=CNI_HIST_URL,
            status="ok",
            rows=len(close["rows"]),
            fields=["日期", "收盘"],
            date_range=[close["rows"][0][0], day],
            frequency="daily",
            official=True,
            adopted=True,
            note=f"原始返回 {close['raw_rows']} 行，含基日 2010-05-31 基点行；按发布日截断后 {len(close['rows'])} 行。",
        ),
        _probe_record(
            probe_id="c1_cni_total_return_399606",
            requirement="官方全收益",
            purpose="创业板指全收益（399606，创业板R）日频收盘",
            publisher="深圳证券信息有限公司（国证指数）",
            interface="国证指数官方历史行情接口",
            url=CNI_HIST_URL,
            status="ok",
            rows=len(tr["rows"]),
            fields=["日期", "收盘"],
            date_range=[tr["rows"][0][0], tr["rows"][-1][0]],
            frequency="daily",
            official=True,
            adopted=True,
            note="分红再投资口径；与价格指数分属不同序列，不混用。",
        ),
        _probe_record(
            probe_id="c2_cni_current_valuation",
            requirement="官方估值",
            purpose="创业板指官方 PE/PB 当前快照",
            publisher="深圳证券信息有限公司（国证指数）",
            interface="国证指数官网指数列表接口",
            url=CNI_LIST_URL,
            status="ok",
            rows=1,
            fields=["peStatic", "peDynamic", "pb", "closeingPoint"],
            date_range=[day, day],
            frequency="snapshot",
            official=True,
            adopted=True,
            note=(
                f"PE 滚动 {val['pe_dynamic']}、PE 静态 {val['pe_static']}、PB {val['pb']}；"
                "官网只给最新交易日，无历史。"
            ),
        ),
        _probe_record(
            probe_id="c2_cni_pe_history",
            requirement="官方估值",
            purpose="创业板指官方滚动市盈率日度/月度历史",
            publisher="深圳证券信息有限公司（国证指数）",
            interface="官网指数详情/指数列表/历史行情",
            url=CNI_HIST_URL,
            status="missing",
            rows=0,
            fields=["peDynamic", "pb"],
            date_range=[],
            frequency="",
            official=True,
            adopted=False,
            note=(
                "历史行情接口只有 OHLC/成交，无 PE；指数列表只有最新值，"
                "date/tradeDate/searchDate 等日期参数实测均被忽略；"
                "官网无指数估值历史下载。未找到可复核的官方日度或月度历史。"
            ),
        ),
        _probe_record(
            probe_id="c2_szse_index_quote",
            requirement="官方估值",
            purpose="深交所指数行情是否含 PE",
            publisher="深圳证券交易所",
            interface="深交所指数行情报表（1826_snapshot）",
            url=SZSE_LIST_URL,
            status="ok" if quote["rows"] else "empty",
            rows=quote["rows"],
            fields=quote["columns"],
            date_range=["2026-11-07", "2026-11-11"],
            frequency="daily",
            official=True,
            adopted=False,
            note="深交所指数行情报表字段只有 OHLC/成交，没有市盈率。",
        ),
        _probe_record(
            probe_id="c2_szse_industry_pe",
            requirement="官方估值",
            purpose="深交所行业加权平均市盈率是否可用于创业板指",
            publisher="深圳证券交易所",
            interface="深交所股票行业统计（1804_gptj_merge_after）",
            url=SZSE_LIST_URL,
            status="ok" if industry["rows"] else "empty",
            rows=industry["rows"],
            fields=industry["columns"],
            date_range=[],
            frequency="daily",
            official=True,
            adopted=False,
            note=(
                "深交所『创业板』板块的行业加权平均市盈率（19 个行业），"
                "对象是板块/行业，不是创业板指 100 只样本；指数 PE 的样本与加权方式不同，不能替代。"
            ),
        ),
        _probe_record(
            probe_id="c2_ceic_chinext_board_pe",
            requirement="官方估值",
            purpose="第三方转载的创业板板块 PE（存在但未采用）",
            publisher="CEIC（数据标注来源：深圳证券交易所）",
            interface="CEIC 数据库页面",
            url=CEIC_URL,
            status=ceic.get("status", "unknown"),
            rows=None,
            fields=["市盈率", "市净率"],
            date_range=["2009-10-30", "2026-11-02"],
            frequency="daily",
            official=False,
            adopted=False,
            note=(
                "页面标注为『深交所 : 创业板 : 市盈率和市净率 : 日度』，"
                "最新约 35.39 倍；这是创业板**板块**口径，与创业板指 100 只样本的 "
                f"{val['pe_dynamic']} 倍不是同一对象；且为第三方转载，未采用。"
            ),
        ),
        _probe_record(
            probe_id="c3_cni_index_identity",
            requirement="指数身份",
            purpose="创业板指名称、代码、基日",
            publisher="深圳证券信息有限公司（国证指数）",
            interface="国证指数官网指数详情接口",
            url=CNI_DETAIL_URL,
            status="ok" if detail.get("indexcode") else "empty",
            rows=1,
            fields=["indexcode", "indexname", "indexfullcname", "jr"],
            date_range=[],
            frequency="",
            official=True,
            adopted=True,
            note=(
                f"{detail.get('indexname')}（{detail.get('indexfullcname')}），"
                f"代码 {detail.get('indexcode')}，基日 {detail.get('base_date')}。"
            ),
        ),
        _probe_record(
            probe_id="c3_szse_index_list",
            requirement="指数身份",
            purpose="深交所指数列表交叉核对",
            publisher="深圳证券交易所",
            interface="深交所指数列表（1812_zs）",
            url=SZSE_LIST_URL,
            status="ok",
            rows=1,
            fields=["指数代码", "指数名称", "基日", "基日指数", "起始计算日"],
            date_range=[],
            frequency="",
            official=True,
            adopted=True,
            note=(
                f"{szse['code']} {szse['name']}，基日 {szse['base_date']}，"
                f"基日指数 {szse['base_value']}，起始计算日 {szse['start_date']}。"
            ),
        ),
        _probe_record(
            probe_id="c4_etf_159915",
            requirement="跟踪 ETF",
            purpose="159915 产品资料与日线/净值可用性",
            publisher="天天基金/东方财富",
            interface="基金概况页 + fund_etf_hist_em + fund_etf_fund_info_em",
            url="http://fundf10.eastmoney.com/jbgk_159915.html",
            status=(
                "ok"
                if etf.get("tracking_target")
                and etf.get("hist_rows")
                and etf.get("nav_rows")
                else "error"
            ),
            rows=etf.get("nav_rows"),
            fields=["跟踪标的", "管理费率", "托管费率", "日线收盘", "单位净值"],
            date_range=[etf.get("hist_start"), etf.get("hist_end")],
            frequency="daily",
            official=False,
            adopted=True,
            note=(
                f"跟踪 {etf.get('tracking_target')}；管理费 {etf.get('management_fee')}%/年、"
                f"托管费 {etf.get('custody_fee')}%/年；与既有 ETF 产品资料检查同口径。"
                + (
                    f" 价格源：{etf.get('hist_source')}。"
                    if etf.get("hist_source")
                    else ""
                )
                + (f" 说明：{etf.get('hist_error')}" if etf.get("hist_error") else "")
                + (f" 本次行情/净值抓取失败：{etf.get('error')}" if etf.get('error') else "")
            ),
        ),
    ]

    return {
        "schema_version": "chinext-feasibility/1",
        "generated_at": now.isoformat(timespec="seconds"),
        "scope": "创业板指（399006）与跟踪 ETF 159915",
        "decision": "short_history_only",
        "decision_detail": (
            "官方日收盘与全收益可得且自发布日起完整；官方估值只有最新一个交易日的 "
            "PE/PB 快照，没有日度或月度历史，因此不做 5 年分位、不做周度候选 A 信号、"
            "不做研究回测、不写候选 A 账本。已接入收盘、全收益、当前估值快照、"
            "指数快照与 ETF 产品资料。"
        ),
        "official_history_available": True,
        "official_pe_history_available": False,
        "current_official_valuation": {
            "date": day,
            "pe_dynamic": val["pe_dynamic"],
            "pe_static": val["pe_static"],
            "pb": val["pb"],
            "source": "cnindex.index_list",
        },
        "implemented": [
            "399006 官方日收盘序列（含内容哈希与序列库 manifest）",
            "399606 官方全收益日收盘序列",
            "399006 官方当前 PE/PB 快照（单日，进入指数画像的『官方短历史』并列事实）",
            "399006 指数快照（官方收盘 + 官方当前 PE/PB）",
            "159915 产品资料快照（价格、净值、费率、跟踪标的）",
            "名称识别：创业板指/创业板指数/399006 可识别；单独『创业板』澄清；创业板50 等范围外",
        ],
        "not_implemented": [
            "5 年月度估值画像（无官方历史估值序列）",
            "候选 A 周度研究信号（无官方 PE 日频历史）",
            "候选 A 研究回测（无官方 PE 日频历史）",
            "候选 A 信号账本 399006 记录（无信号可写）",
        ],
        "third_party_not_adopted": [
            "CEIC 转载的创业板板块日度 PE/PB：对象是板块，不是 399006；第三方，未采用。",
            "乐咕乐股等第三方指数 PE 历史：不支持创业板指或口径未说明，未采用。",
        ],
        "probes": probes,
        "notes": [
            "本结论只针对已测官方接口与参数，不代表所有官方渠道都没有历史估值；",
            "没有为了让分位通过而用第三方估值、板块 PE 或拼接序列；",
            "官方当前 PE/PB 只作单日并列事实，不计算分位，也不构成买点。",
        ],
    }


# --- 主流程 -----------------------------------------------------------------
def _sina_hist_frame():
    """东财 push2 限流时的等价日线源（只用于价格/成交）。"""
    import akshare as ak

    hist = ak.fund_etf_hist_sina(symbol="sz159915")
    return hist.rename(
        columns={"date": "日期", "close": "收盘", "volume": "成交量", "amount": "成交额"}
    )


def _sina_price_source(stamp: str):
    return SourceRef(
        source_id="sina.etf_hist",
        publisher="新浪财经",
        interface="AKShare fund_etf_hist_sina",
        url="https://finance.sina.com.cn/",
        retrieved_at=stamp,
        notes="东方财富 push2 限流时的等价 ETF 日线源；只替代价格/成交，净值仍来自东方财富",
    )


def probe_etf() -> dict:
    from tools.build_snapshots import fetch_fund_profile

    now = _now()
    stamp = now.isoformat(timespec="seconds")
    profile = fetch_fund_profile("159915", now=now)
    try:
        import akshare as ak

        hist = ak.fund_etf_hist_em(
            symbol="159915",
            period="daily",
            start_date="20240101",
            end_date="20301231",
            adjust="",
        )
        nav = ak.fund_etf_fund_info_em(
            fund="159915", start_date="20260101", end_date="20261231"
        )
        hist_source = "em.etf_hist"
        hist_error = None
    except Exception as exc:  # 东财限流：改用新浪日线，净值仍走东财
        try:
            import akshare as ak

            hist = _sina_hist_frame()
            nav = ak.fund_etf_fund_info_em(
                fund="159915", start_date="20260101", end_date="20261231"
            )
            hist_source = "sina.etf_hist"
            hist_error = f"{type(exc).__name__}: 东方财富限流，已改用新浪日线"
        except Exception as exc2:
            return {
                **profile,
                "hist_rows": None,
                "hist_start": None,
                "hist_end": None,
                "nav_rows": None,
                "nav_end": None,
                "hist_source": None,
                "error": f"{type(exc).__name__}: {exc}; fallback {type(exc2).__name__}: {exc2}",
            }
    return {
        **profile,
        "hist_rows": int(len(hist)),
        "hist_start": str(hist["日期"].iloc[0])[:10] if len(hist) else None,
        "hist_end": str(hist["日期"].iloc[-1])[:10] if len(hist) else None,
        "nav_rows": int(len(nav)),
        "nav_end": str(nav["净值日期"].iloc[-1])[:10] if len(nav) else None,
        "hist_source": hist_source,
        "hist_error": hist_error,
        "price_source": "sina.etf_hist" if hist_source == "sina.etf_hist" else "em.etf_hist",
        "retrieved_at": stamp,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="创业板指数据可行性核查与接入")
    parser.add_argument("--probe-only", action="store_true", help="只核查，不写序列/快照")
    parser.add_argument("--skip-etf", action="store_true", help="跳过 159915 ETF 快照构建")
    args = parser.parse_args(argv)

    now = _now()
    bundle = {
        "close": fetch_cni_history(INDEX_CODE),
        "total_return": fetch_cni_history(TR_CODE),
        "valuation": fetch_cni_current_valuation(),
        "detail": fetch_cni_detail(),
        "szse_list": fetch_szse_index_list(),
        "szse_quote": probe_szse_index_quote(),
        "szse_industry": probe_szse_industry_pe(),
        "ceic": probe_ceic_board_pe(),
        "etf": probe_etf() if not args.skip_etf else {
            "tracking_target": None,
            "management_fee": None,
            "custody_fee": None,
            "hist_rows": None,
            "nav_rows": None,
            "error": "本次以 --skip-etf 跳过 ETF 资料抓取",
        },
    }
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

        if not args.skip_etf:
            from tools.build_snapshots import build_etf_snapshot

            try:
                etf_snapshot = build_etf_snapshot(
                    "159915", expected_as_of=snapshot.as_of, now=now
                )
            except Exception:
                # 东财 push2 限流：用新浪日线 + 东财净值构建，并在快照中如实标注来源。
                etf_snapshot = build_etf_snapshot(
                    "159915",
                    expected_as_of=snapshot.as_of,
                    now=now,
                    hist_frame=_sina_hist_frame(),
                    price_source=_sina_price_source(now.isoformat(timespec="seconds")),
                )
                feasibility["etf_price_source_fallback"] = "sina.etf_hist"
            snapshot_store.save(etf_snapshot)
            snapshot_store.rebuild_manifest()
            feasibility["etf_snapshot_id"] = etf_snapshot.snapshot_id

    COVERAGE_DIR.mkdir(parents=True, exist_ok=True)
    FEASIBILITY_PATH.write_text(
        json.dumps(feasibility, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(feasibility, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
