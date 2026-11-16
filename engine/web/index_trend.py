"""指数「近 5 年估值分位走势」：与卡片完全相同的 60 个月窗口规则，逐月重算。

数据只来自已提交的月度参考序列（`data/series`，同源同列）；没有序列就不画线，
不拼第三方数据，也不给买卖判断。每个点都是「该月最后一个观测在该来源历史池中的分位」，
只作描述。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.series.models import (
    FREQ_MONTHLY_REFERENCE,
    METRIC_PB,
    METRIC_PE_ROLLING,
    SeriesStore,
)
from engine.series.window import MIN_VALID_MONTHS, WINDOW_MONTHS, build_window

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_LIMIT = 60

NOTE = (
    "走势与卡片同一口径：分析时点所在月份之前的 60 个完整自然月为参考池，"
    "每月取最后一个有效观测，分位 = 100 ×（小于 + 0.5×等于）/ 有效观测数；"
    "只描述历史位置，不是上涨概率，也不构成买卖判断。"
)


def percentile_trend(
    instrument_id: str,
    *,
    metric: str = METRIC_PE_ROLLING,
    limit: int = DEFAULT_LIMIT,
    store: SeriesStore | None = None,
) -> dict[str, Any]:
    """返回最近 limit 个月的分位走势点（按月份升序）。"""
    series_store = store or SeriesStore(ROOT / "data" / "series")
    series = series_store.find(instrument_id, metric, frequency=FREQ_MONTHLY_REFERENCE)
    if series is None:
        return {
            "status": "no_series",
            "metric": metric,
            "points": [],
            "note": "没有该指数的同源月度估值序列，因此不画分位走势；卡片同样不计算分位。",
        }
    observations = list(series.observations)
    months = sorted({str(day)[:7] for day, _value in observations})
    points: list[dict[str, Any]] = []
    for month in months:
        same_month = [str(day) for day, _value in observations if str(day)[:7] == month]
        as_of = max(same_month)
        window = build_window(
            observations,
            metric=metric,
            as_of=as_of,
            require_positive=(metric == METRIC_PE_ROLLING),
        )
        if window.percentile is None:
            continue
        points.append(
            {
                "month": month,
                "date": as_of,
                "value": round(float(window.comparison.value), 4) if window.comparison else None,
                "percentile": round(float(window.percentile), 1),
                "pool_size": window.pool_size,
            }
        )
    tail = points[-limit:]
    return {
        "status": "ok" if len(tail) >= 2 else "insufficient",
        "metric": metric,
        "label": "PE（滚动）" if metric == METRIC_PE_ROLLING else "PB",
        "points": tail,
        "window_months": WINDOW_MONTHS,
        "min_valid_months": MIN_VALID_MONTHS,
        "series_id": series.series_id,
        "source": series.source.to_dict(),
        "note": NOTE,
    }


def pe_trend(instrument_id: str, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    return percentile_trend(instrument_id, metric=METRIC_PE_ROLLING, limit=limit)


def pb_trend(instrument_id: str, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    return percentile_trend(instrument_id, metric=METRIC_PB, limit=limit)


__all__ = ["DEFAULT_LIMIT", "NOTE", "pb_trend", "pe_trend", "percentile_trend"]
