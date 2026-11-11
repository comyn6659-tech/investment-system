#!/usr/bin/env python
"""构建指数价格日线序列（版本化、可重放）。

口径：**价格指数收盘价，不含分红**。只用于事后价格收益诊断，不是全收益指数，
也不用于任何策略结论。与其他序列一样按内容哈希入库。

用法：
    .venv/bin/python tools/build_price_series.py
    .venv/bin/python tools/build_price_series.py --only 000300
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
    METRIC_PRICE_INDEX_CLOSE,
    IndexSeries,
    SeriesSource,
    SeriesStore,
)

TZ = ZoneInfo("Asia/Singapore")
SERIES_DIR = ROOT / "data" / "series"
CACHE_DIR = ROOT / "data" / "cache"
INDEX_CODES = {"000300": "沪深300", "000905": "中证500", "000852": "中证1000"}


def _from_cache(code: str) -> tuple[pd.DataFrame, str]:
    """行情接口被限流时，退回本项目当天抓取的原始缓存（同样来自该接口）。"""
    path = CACHE_DIR / f"index_daily_{code}.csv"
    if not path.exists():
        raise RuntimeError(f"没有可用的行情缓存: {path.name}")
    frame = pd.read_csv(path)
    stamp = datetime.fromtimestamp(path.stat().st_mtime, TZ).isoformat(timespec="seconds")
    return frame, stamp


def build_price_series(
    code: str, *, now: datetime | None = None, source_mode: str = "auto"
) -> IndexSeries:
    now = now or datetime.now(TZ)
    stamp = now.isoformat(timespec="seconds")
    frame = None
    used_cache = False
    error = None
    if source_mode in ("auto", "network"):
        try:
            frame = ak.stock_zh_index_daily_em(symbol=f"sh{code}")
            frame.to_csv(CACHE_DIR / f"series_price_{code}.csv", index=False)
        except Exception as exc:  # 限流/网络问题：退回当天缓存
            error = f"{type(exc).__name__}: {exc}"
            print(f"[warn] {code} 实时接口失败：{error}", file=sys.stderr)
            frame = None
    if frame is None:
        frame, stamp = _from_cache(code)
        used_cache = True
        if source_mode == "network":
            raise RuntimeError(f"{code} 实时接口不可用，且禁止使用缓存")
    observations: list[tuple[str, float]] = []
    for _, row in frame.iterrows():
        day = str(row.get("date"))[:10]
        raw = row.get("close")
        if day is None or pd.isna(raw):
            continue
        value = float(raw)
        if value != value or value <= 0:
            continue
        observations.append((day, value))
    return IndexSeries(
        instrument_id=f"INDEX:{code}.SH",
        metric=METRIC_PRICE_INDEX_CLOSE,
        field="close",
        frequency=FREQ_DAILY,
        source=SeriesSource(
            source_id="em.index_daily_em",
            publisher="东方财富",
            interface="AKShare stock_zh_index_daily_em",
            url="https://quote.eastmoney.com/",
            retrieved_at=stamp,
            definition_note=(
                "指数日线不复权收盘价。**价格指数，不含分红**；"
                "不是全收益指数，也不代表可投资组合收益。"
                + ("（本序列由本项目同日抓取的原始缓存重建）" if used_cache else "")
            ),
        ),
        observations=tuple(observations),
        unit="index_point",
        transform="日线『close』原值，未做复权或拼接",
        aggregation_method="not_applicable",
        denominator_basis="not_applicable",
        loss_sample_handling="not_applicable",
        revision_availability="unknown",
        notes=(
            "点位不带币种；仅用于事后价格收益诊断。"
            "接口对指数发布日之前的日期也会给出回溯值（例如中证1000 于 2014-10 发布），"
            "本批判定与诊断只使用估值序列覆盖的日期（000905 自 2007-01、000852 自 2014-10），"
            "未使用发布前的回填段。"
            + (f"构建时实时接口不可用（{error}），改用同日缓存。" if used_cache and error else "")
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建指数价格日线序列")
    parser.add_argument("--only", default=None, help="只构建指定代码，逗号分隔")
    parser.add_argument(
        "--source",
        choices=["auto", "network", "cache"],
        default="auto",
        help="auto=先联网，失败退回同日缓存；network=只联网；cache=只用缓存",
    )
    args = parser.parse_args(argv)

    codes = tuple(x.strip() for x in args.only.split(",")) if args.only else tuple(INDEX_CODES)
    now = datetime.now(TZ)
    SERIES_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    store = SeriesStore(SERIES_DIR)
    built = []
    for code in codes:
        series = build_price_series(code, now=now, source_mode=args.source)
        store.save(series)
        built.append(
            {
                "series_id": series.series_id,
                "instrument_id": series.instrument_id,
                "observations": len(series.observations),
                "start": series.start_date,
                "end": series.end_date,
            }
        )
    entries = store.rebuild_manifest()
    print(
        json.dumps(
            {"built_at": now.isoformat(timespec="seconds"), "price_series": built, "manifest": len(entries)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
