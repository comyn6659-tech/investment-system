#!/usr/bin/env python
"""构建指数估值序列（可重放、有版本、同源同列）。

本轮画像用第三方月度长序列 + 官方短序列并列：
- 长序列：乐咕乐股「滚动市盈率」与「市净率」两列，各自独立成一条序列，不拼接；
- 短序列：中证指数官方指标文件「市盈率1」，只有最近约 20 个交易日，单独存放。

不插值、不前向填充、不把月度观测扩成周度。

用法：
    .venv/bin/python tools/build_index_series.py
    .venv/bin/python tools/build_index_series.py --only 000300
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.series.models import (  # noqa: E402
    FREQ_DAILY_SHORT,
    FREQ_MONTHLY_REFERENCE,
    METRIC_OFFICIAL_PE_1,
    METRIC_PB,
    METRIC_PE_ROLLING,
    IndexSeries,
    SeriesSource,
    SeriesStore,
)

TZ = ZoneInfo("Asia/Singapore")
SERIES_DIR = ROOT / "data" / "series"
CACHE_DIR = ROOT / "data" / "cache"

INDEX_CODES = {"000300": "沪深300", "000905": "中证500", "000852": "中证1000"}


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _observations(frame: pd.DataFrame, date_col: str, value_col: str) -> tuple[tuple[str, float], ...]:
    out: list[tuple[str, float]] = []
    for _, row in frame.iterrows():
        raw_date = row.get(date_col)
        raw_value = row.get(value_col)
        if raw_date is None or pd.isna(raw_date):
            continue
        try:
            if pd.isna(raw_value):
                continue
        except (TypeError, ValueError):
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if value != value:
            continue
        day = str(raw_date)[:10]
        if len(day) != 10:
            continue
        out.append((day, value))
    return tuple(out)


def _source(kind: str, code: str, stamp: str) -> SeriesSource:
    if kind == "legulegu":
        return SeriesSource(
            source_id="legulegu.index_pe_pb",
            publisher="乐咕乐股",
            interface="AKShare stock_index_pe_lg / stock_index_pb_lg",
            url="https://legulegu.com/stockdata/index-basic-pe",
            retrieved_at=stamp,
            definition_note=(
                "第三方聚合数据；列名为供应商原始命名。整体法/加权方式、分母口径与亏损股处理"
                "未在来源中说明，记为 unknown；不与其他来源的同名指标拼接。"
            ),
        )
    return SeriesSource(
        source_id="csindex.indicator_file",
        publisher="中证指数有限公司",
        interface="官方指数估值指标文件（.xls）",
        url=(
            "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/"
            f"autofile/indicator/{code}indicator.xls"
        ),
        retrieved_at=stamp,
        definition_note="官方文件只提供最近约 20 个交易日；市盈率/股息率口径未在文件内说明。",
    )


def build_index_series(code: str, *, now: datetime | None = None) -> list[IndexSeries]:
    now = now or datetime.now(TZ)
    stamp = now.isoformat(timespec="seconds")
    name = INDEX_CODES[code]
    instrument_id = f"INDEX:{code}.SH"
    series: list[IndexSeries] = []

    pe = ak.stock_index_pe_lg(symbol=name)
    pb = ak.stock_index_pb_lg(symbol=name)
    pe.to_csv(CACHE_DIR / f"series_pe_{code}.csv", index=False)
    pb.to_csv(CACHE_DIR / f"series_pb_{code}.csv", index=False)

    series.append(
        IndexSeries(
            instrument_id=instrument_id,
            metric=METRIC_PE_ROLLING,
            field="滚动市盈率",
            frequency=FREQ_MONTHLY_REFERENCE,
            source=_source("legulegu", code, stamp),
            observations=_observations(pe, "日期", "滚动市盈率"),
            transform="供应商原列『滚动市盈率』，未做任何换算或改写",
            aggregation_method="unknown",
            denominator_basis="unknown",
            loss_sample_handling="unknown",
            revision_availability="unknown",
            notes="与本序列同名的其他 PE 列（静态/等权/中位数）属于不同口径，未混入。",
        )
    )
    series.append(
        IndexSeries(
            instrument_id=instrument_id,
            metric=METRIC_PB,
            field="市净率",
            frequency=FREQ_MONTHLY_REFERENCE,
            source=_source("legulegu", code, stamp),
            observations=_observations(pb, "日期", "市净率"),
            transform="供应商原列『市净率』，未做任何换算或改写",
            aggregation_method="unknown",
            denominator_basis="unknown",
            loss_sample_handling="unknown",
            revision_availability="unknown",
            notes="等权市净率与中位数市净率属于不同口径，未混入。",
        )
    )

    csi = ak.stock_zh_index_value_csindex(symbol=code)
    csi.to_csv(CACHE_DIR / f"series_csindex_{code}.csv", index=False)
    series.append(
        IndexSeries(
            instrument_id=instrument_id,
            metric=METRIC_OFFICIAL_PE_1,
            field="市盈率1",
            frequency=FREQ_DAILY_SHORT,
            source=_source("csindex", code, stamp),
            observations=_observations(csi, "日期", "市盈率1"),
            transform="官方指标文件列『市盈率1』，原样保留",
            aggregation_method="unknown",
            denominator_basis="unknown",
            loss_sample_handling="unknown",
            revision_availability="unknown",
            notes="只覆盖最近 20 个交易日，仅作并列事实，不参与长历史排名。",
        )
    )
    return series


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建指数估值序列")
    parser.add_argument("--only", default=None, help="只构建指定代码，逗号分隔")
    args = parser.parse_args(argv)

    codes = tuple(x.strip() for x in args.only.split(",")) if args.only else tuple(INDEX_CODES)
    prevision = datetime.now(TZ)
    SERIES_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    store = SeriesStore(SERIES_DIR)

    built: list[dict] = []
    for code in codes:
        for item in build_index_series(code, now=prevision):
            store.save(item)
            built.append(
                {
                    "series_id": item.series_id,
                    "instrument_id": item.instrument_id,
                    "metric": item.metric,
                    "frequency": item.frequency,
                    "observations": len(item.observations),
                    "start": item.start_date,
                    "end": item.end_date,
                }
            )
    entries = store.rebuild_manifest()
    print(
        json.dumps(
            {"built_at": prevision.isoformat(timespec="seconds"), "series": built, "manifest": len(entries)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
