#!/usr/bin/env python
"""构建候选 A 回测所需的中证官方日频序列。

每组指数取三条序列（共 9 条）：
- `official_close`：指数日频收盘（价格指数，不含分红）；
- `official_pe_rolling`：同一份官方文件的『滚动市盈率』列，单位：倍；
- `total_return_close`：对应的全收益指数日频收盘（主收益口径）。

口径与边界：
- 来源为中证指数官方历史行情文件（经 AKShare `stock_zh_index_hist_csindex`）；
- 文件在指数正式发布日之前也会给出回溯值，本工具把这些行**丢弃**，并记录丢弃条数；
- 不与第三方月度序列拼接；本文件只做采集与版本化，不含任何策略计算。

用法：
    .venv/bin/python tools/build_official_series.py
    .venv/bin/python tools/build_official_series.py --only 000300
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
    FREQ_DAILY,
    METRIC_OFFICIAL_CLOSE,
    METRIC_OFFICIAL_PE_ROLLING,
    METRIC_TOTAL_RETURN_CLOSE,
    IndexSeries,
    SeriesSource,
    SeriesStore,
)

TZ = ZoneInfo("Asia/Singapore")
SERIES_DIR = ROOT / "data" / "series"
CACHE_DIR = ROOT / "data" / "cache"

# 指数正式发布日（公开资料）。发布之前的官方文件行是回溯值，不能进入分位样本或收益计算。
# 中证1000 的发布日期由第三批施工单明确给出（2014-10-17）。
INDICES = {
    "000300": {"name": "沪深300", "tr": "H00300", "launch": "2005-04-08", "start": "20040101"},
    "000905": {"name": "中证500", "tr": "H00905", "launch": "2007-01-15", "start": "20040101"},
    "000852": {"name": "中证1000", "tr": "H00852", "launch": "2014-10-17", "start": "20100101"},
}


def _source(kind: str, code: str, stamp: str) -> SeriesSource:
    return SeriesSource(
        source_id="csindex.indicators",
        publisher="中证指数有限公司",
        interface="AKShare stock_zh_index_hist_csindex（官方历史行情文件）",
        url="https://www.csindex.com.cn/",
        retrieved_at=stamp,
        definition_note=(
            "官方历史行情文件。文件不带公布时刻，也不提供修订版本；"
            "滚动市盈率的加权方式与亏损样本处理未在文件内说明。"
            + ("全收益指数：把样本股分红再投资后的指数，主收益口径。" if kind == "tr" else "")
        ),
    )


def _observations(frame: pd.DataFrame, value_column: str, launch: str) -> tuple[tuple[str, float], ...]:
    out: list[tuple[str, float]] = []
    for _, row in frame.iterrows():
        day = str(row.get("日期"))[:10]
        if not day or day < launch:
            continue
        raw = row.get(value_column)
        try:
            if raw is None or pd.isna(raw):
                continue
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value != value:
            continue
        out.append((day, value))
    return tuple(out)


def build_series(code: str, *, now: datetime | None = None) -> list[IndexSeries]:
    now = now or datetime.now(TZ)
    stamp = now.isoformat(timespec="seconds")
    meta = INDICES[code]
    launch = meta["launch"]

    frame = ak.stock_zh_index_hist_csindex(
        symbol=code, start_date=meta["start"], end_date="20261231"
    )
    frame.to_csv(CACHE_DIR / f"official_hist_{code}.csv", index=False)
    tr_frame = ak.stock_zh_index_hist_csindex(
        symbol=meta["tr"], start_date=meta["start"], end_date="20261231"
    )
    tr_frame.to_csv(CACHE_DIR / f"official_hist_tr_{code}.csv", index=False)

    dropped = int((frame["日期"].astype(str).str[:10] < launch).sum())
    out: list[IndexSeries] = []

    close_obs = _observations(frame, "收盘", launch)
    pe_obs = _observations(frame, "滚动市盈率", launch)
    tr_obs = _observations(tr_frame, "收盘", launch)

    out.append(
        IndexSeries(
            instrument_id=f"INDEX:{code}.SH",
            metric=METRIC_OFFICIAL_CLOSE,
            field="收盘",
            frequency=FREQ_DAILY,
            source=_source("price", code, stamp),
            observations=close_obs,
            unit="index_point",
            transform="官方历史行情文件『收盘』原值；价格指数，不含分红",
            aggregation_method="not_applicable",
            denominator_basis="not_applicable",
            loss_sample_handling="not_applicable",
            revision_availability="unknown",
            notes=f"只保留发布日 {launch} 及之后的记录；丢弃发布前回溯行 {dropped} 条。价格指数，不含分红。",
        )
    )
    out.append(
        IndexSeries(
            instrument_id=f"INDEX:{code}.SH",
            metric=METRIC_OFFICIAL_PE_ROLLING,
            field="滚动市盈率",
            frequency=FREQ_DAILY,
            source=_source("pe", code, stamp),
            observations=pe_obs,
            unit="ratio",
            transform="官方历史行情文件『滚动市盈率』原列，未做换算",
            aggregation_method="unknown",
            denominator_basis="unknown",
            loss_sample_handling="unknown",
            revision_availability="unknown",
            notes=(
                f"只保留发布日 {launch} 及之后的记录；文件在该日之前不给 PE。"
                "非正值不参与分位；文件不带公布时刻，回测按 T+1 收盘执行并作 T+2 敏感性。"
            ),
        )
    )
    out.append(
        IndexSeries(
            instrument_id=f"INDEX:{code}.SH",
            metric=METRIC_TOTAL_RETURN_CLOSE,
            field="收盘",
            frequency=FREQ_DAILY,
            source=_source("tr", meta["tr"], stamp),
            observations=tr_obs,
            unit="index_point",
            transform=f"全收益指数 {meta['tr']} 官方历史行情文件『收盘』原值",
            aggregation_method="not_applicable",
            denominator_basis="not_applicable",
            loss_sample_handling="not_applicable",
            revision_availability="unknown",
            notes=(
                f"全收益口径（分红再投资），发布日 {launch} 之后的记录；"
                "用于主收益口径，不与管理费或其他费用重复扣除。"
            ),
        )
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建中证官方日频序列")
    parser.add_argument("--only", default=None, help="只构建指定代码，逗号分隔")
    args = parser.parse_args(argv)
    codes = tuple(x.strip() for x in args.only.split(",")) if args.only else tuple(INDICES)
    now = datetime.now(TZ)
    SERIES_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    store = SeriesStore(SERIES_DIR)
    built = []
    for code in codes:
        for series in build_series(code, now=now):
            store.save(series)
            built.append(
                {
                    "series_id": series.series_id,
                    "instrument_id": series.instrument_id,
                    "metric": series.metric,
                    "observations": len(series.observations),
                    "start": series.start_date,
                    "end": series.end_date,
                }
            )
    entries = store.rebuild_manifest()
    print(
        json.dumps(
            {"built_at": now.isoformat(timespec="seconds"), "series": built, "manifest": len(entries)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
