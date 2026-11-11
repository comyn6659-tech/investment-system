"""估值分组的事后诊断：历史的估值分位之后，价格怎么走。

定位（第三阶段第二批 §3）：
- **事后描述**，不是策略、不是回测、不是买卖建议；
- 收益是**价格指数收益，不含分红**，不得称为全收益或策略收益；
- 分位一律复用画像的 `build_window`（as_of = t），不另写第二套算法；
- 分组只用总设计已冻结的阈值，本模块不提供任何参数入口。
"""

from __future__ import annotations

import calendar
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from engine.series.models import IndexSeries
from engine.series.window import WINDOW_MONTHS, build_window

# 总设计 §7.2 冻结阈值：本文件不提供修改入口
FROZEN_THRESHOLDS: dict[str, float] = {
    "pe_low_max": 20.0,
    "pe_high_min": 60.0,
    "pb_low_max": 40.0,
    "pb_high_min": 70.0,
}

HORIZONS = (3, 6, 12)

GROUP_LABELS = {
    "pe_low": "PE 分位 ≤20",
    "pe_mid": "PE 分位 20–60",
    "pe_high": "PE 分位 ≥60",
    "pb_low": "PB 分位 ≤40",
    "pb_mid": "PB 分位 40–70",
    "pb_high": "PB 分位 ≥70",
    "b_entry": "B 入场条件（PE≤20 且 PB≤40）",
    "b_exit": "B 退出条件（PE≥60 或 PB≥70）",
    "baseline": "无条件基准（同期所有可评估月份）",
}
GROUPS = tuple(GROUP_LABELS)

GUARDRAILS = (
        "以下全部是事后描述，不是策略，也不代表当时可以交易。",
        "收益为价格指数收益，不含分红；不是全收益，也不是可投资组合收益。",
        "历史估值数据的公布时刻与修订版本未知，信号时点只能事后近似。",
        "按月采样的重叠样本：观测数不等于独立交易次数，分段数只用于提示重叠程度。",
        "不构成策略表现的证明，也不构成任何买卖结论。",
    )


# --- 价格序列 ---------------------------------------------------------------
@dataclass(frozen=True)
class PricePoint:
    date: str
    close: float

    def to_dict(self) -> dict[str, Any]:
        return {"date": self.date, "close": self.close}


def load_price_points(series: IndexSeries) -> tuple[PricePoint, ...]:
    points = tuple(PricePoint(date=d, close=float(v)) for d, v in series.observations)
    if not points:
        raise ValueError(f"{series.instrument_id} 价格序列为空")
    return points


def last_price_on_or_before(points: Sequence[PricePoint], target: str) -> PricePoint | None:
    """不晚于 target 的最后一个交易日收盘（事后同日对齐）。"""
    lo, hi = 0, len(points) - 1
    found: PricePoint | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if points[mid].date <= target:
            found = points[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return found


def calendar_month_end(day: str, months: int) -> str:
    """day 之后 months 个日历月的那一个月最后一天（月末对齐）。"""
    base = date.fromisoformat(str(day)[:10])
    total = base.year * 12 + (base.month - 1) + months
    year, month = total // 12, total % 12 + 1
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


@dataclass(frozen=True)
class ForwardReturn:
    start_date: str | None
    start_price: float | None
    end_date: str | None
    end_price: float | None
    return_pct: float | None
    matured: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_date": self.start_date,
            "start_price": self.start_price,
            "end_date": self.end_date,
            "end_price": self.end_price,
            "return_pct": self.return_pct,
            "matured": self.matured,
            "reason": self.reason,
        }


def forward_price_return(
    points: Sequence[PricePoint],
    *,
    signal_date: str,
    horizon_months: int,
    start_on: str | None = None,
) -> ForwardReturn:
    """从 signal_date（或显式 start_on）起，到「信号月 + h 个日历月」月末的价格收益。

    - 起点：不晚于起算日的最后一个交易日收盘；
    - 终点：不晚于「起算日 + h 个日历月」月末的最后一个交易日收盘；
    - 价格数据没覆盖到终点 → `matured=False`，不计入，不截断。
    """
    start_target = start_on or signal_date
    start = last_price_on_or_before(points, start_target)
    if start is None:
        return ForwardReturn(None, None, None, None, None, False, "no_price_at_start")
    end_target = calendar_month_end(signal_date, horizon_months)
    if points[-1].date < end_target:
        return ForwardReturn(
            start.date, start.close, None, None, None, False, "not_matured"
        )
    end = last_price_on_or_before(points, end_target)
    if end is None:
        return ForwardReturn(start.date, start.close, None, None, None, False, "no_price_at_end")
    if end.date <= start.date:
        return ForwardReturn(
            start.date, start.close, end.date, end.close, None, False, "empty_window"
        )
    return ForwardReturn(
        start.date,
        start.close,
        end.date,
        end.close,
        (end.close / start.close - 1.0) * 100.0,
        True,
        None,
    )


# --- 分位复用 ---------------------------------------------------------------
def percentile_at(
    series: IndexSeries, as_of: str, *, require_positive: bool
) -> tuple[float | None, dict[str, Any]]:
    """用画像同一个函数算历史某日的分位（只用不晚于 as_of 的数据）。"""
    window = build_window(
        series.observations,
        metric=series.metric,
        as_of=as_of,
        require_positive=require_positive,
    )
    return window.percentile, window.to_dict()


# --- 分组 -------------------------------------------------------------------
def bucket_flags(pe_pct: float | None, pb_pct: float | None) -> dict[str, bool]:
    """按冻结阈值给出分组；边界归属：20 归低、60 归高、40 归低、70 归高。"""
    t = FROZEN_THRESHOLDS
    flags: dict[str, bool] = {}
    if pe_pct is not None:
        flags["pe_low"] = pe_pct <= t["pe_low_max"]
        flags["pe_mid"] = t["pe_low_max"] < pe_pct < t["pe_high_min"]
        flags["pe_high"] = pe_pct >= t["pe_high_min"]
    if pb_pct is not None:
        flags["pb_low"] = pb_pct <= t["pb_low_max"]
        flags["pb_mid"] = t["pb_low_max"] < pb_pct < t["pb_high_min"]
        flags["pb_high"] = pb_pct >= t["pb_high_min"]
    if pe_pct is not None and pb_pct is not None:
        flags["b_entry"] = pe_pct <= t["pe_low_max"] and pb_pct <= t["pb_low_max"]
        flags["b_exit"] = pe_pct >= t["pe_high_min"] or pb_pct >= t["pb_high_min"]
        flags["baseline"] = True
    return flags


def count_segments(flags: Sequence[bool]) -> int:
    """连续处于同组计为一段。"""
    segments = 0
    previous = False
    for flag in flags:
        if flag and not previous:
            segments += 1
        previous = flag
    return segments


# --- 统计 -------------------------------------------------------------------
def quantile(values: Sequence[float], q: float) -> float | None:
    """线性插值分位（与常见统计软件默认一致），便于手算核对。"""
    data = sorted(float(v) for v in values)
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    position = (len(data) - 1) * q
    low = int(position)
    high = min(low + 1, len(data) - 1)
    frac = position - low
    return data[low] * (1.0 - frac) + data[high] * frac


def summarize_returns(samples: Sequence[ForwardReturn]) -> dict[str, Any]:
    matured = [s for s in samples if s.matured and s.return_pct is not None]
    values = [s.return_pct for s in matured]
    positive = sum(1 for v in values if v > 0)
    return {
        "matured": len(matured),
        "not_matured": len(samples) - len(matured),
        "median": statistics.median(values) if values else None,
        "q1": quantile(values, 0.25),
        "q3": quantile(values, 0.75),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "positive_ratio": (positive / len(values)) if values else None,
        "positive_count": positive,
        "start_date_min": min((s.start_date for s in matured), default=None),
        "start_date_max": max((s.start_date for s in matured), default=None),
    }


# --- 主流程 -----------------------------------------------------------------
def _observation_dates(pe: IndexSeries, pb: IndexSeries) -> list[str]:
    return sorted({d for d, _ in pe.observations} | {d for d, _ in pb.observations})


def build_index_diagnostics(
    *,
    instrument_id: str,
    pe_series: IndexSeries,
    pb_series: IndexSeries,
    price_series: IndexSeries,
    horizons: Iterable[int] = HORIZONS,
) -> dict[str, Any]:
    """单个指数的事后诊断：逐月分位 + 前瞻价格收益 + 分组统计。"""
    points = load_price_points(price_series)
    dates = _observation_dates(pe_series, pb_series)
    by_date = {d: i for i, d in enumerate(dates)}

    samples: list[dict[str, Any]] = []
    not_evaluable: list[dict[str, Any]] = []
    for signal_date in dates:
        pe_pct, pe_window = percentile_at(pe_series, signal_date, require_positive=True)
        pb_pct, pb_window = percentile_at(pb_series, signal_date, require_positive=False)
        if pe_pct is None or pb_pct is None:
            not_evaluable.append(
                {
                    "t": signal_date,
                    "pe_percentile": pe_pct,
                    "pb_percentile": pb_pct,
                    "reason": (
                        pe_window.get("insufficient_reason")
                        or pb_window.get("insufficient_reason")
                        or "分位不可用"
                    ),
                }
            )
            continue
        flags = bucket_flags(pe_pct, pb_pct)
        groups = [name for name in GROUPS if flags.get(name)]
        nxt = dates[by_date[signal_date] + 1] if by_date[signal_date] + 1 < len(dates) else None
        returns: dict[str, Any] = {}
        next_returns: dict[str, Any] = {}
        for h in horizons:
            returns[str(h)] = forward_price_return(
                points, signal_date=signal_date, horizon_months=h
            ).to_dict()
            next_returns[str(h)] = (
                None
                if nxt is None
                else forward_price_return(
                    points, signal_date=signal_date, horizon_months=h, start_on=nxt
                ).to_dict()
            )
        samples.append(
            {
                "t": signal_date,
                "pe_percentile": pe_pct,
                "pb_percentile": pb_pct,
                "groups": groups,
                "next_observation": nxt,
                "returns": returns,
                "returns_next_observation_start": next_returns,
            }
        )

    groups_out: dict[str, Any] = {}
    for name in GROUPS:
        members = [s for s in samples if name in s["groups"]]
        flags = [name in s["groups"] for s in samples]
        entry: dict[str, Any] = {
            "label": GROUP_LABELS[name],
            "months": len(members),
            "segments": count_segments(flags),
            "sample_start": members[0]["t"] if members else None,
            "sample_end": members[-1]["t"] if members else None,
            "horizons": {},
            "horizons_next_observation_start": {},
        }
        for h in horizons:
            pooled = [
                ForwardReturn(
                    **{k: s["returns"][str(h)][k] for k in ForwardReturn.__dataclass_fields__}
                )
                for s in members
            ]
            entry["horizons"][str(h)] = summarize_returns(pooled)
            pooled_next = [
                ForwardReturn(
                    **{
                        k: s["returns_next_observation_start"][str(h)][k]
                        for k in ForwardReturn.__dataclass_fields__
                    }
                )
                for s in members
                if s["returns_next_observation_start"][str(h)] is not None
            ]
            entry["horizons_next_observation_start"][str(h)] = summarize_returns(pooled_next)
        groups_out[name] = entry

    return {
        "instrument_id": instrument_id,
        "pe_series_id": pe_series.series_id,
        "pb_series_id": pb_series.series_id,
        "price_series_id": price_series.series_id,
        "price_series_note": "价格指数收盘，不含分红",
        "observation_dates": len(dates),
        "evaluable_months": len(samples),
        "not_evaluable_months": len(not_evaluable),
        "not_evaluable": not_evaluable,
        "first_evaluable": samples[0]["t"] if samples else None,
        "last_evaluable": samples[-1]["t"] if samples else None,
        "groups": groups_out,
        "samples": samples,
    }


__all__ = [
    "FROZEN_THRESHOLDS",
    "GROUP_LABELS",
    "GROUPS",
    "GUARDRAILS",
    "HORIZONS",
    "ForwardReturn",
    "PricePoint",
    "bucket_flags",
    "build_index_diagnostics",
    "calendar_month_end",
    "count_segments",
    "forward_price_return",
    "last_price_on_or_before",
    "load_price_points",
    "percentile_at",
    "quantile",
    "summarize_returns",
]
