"""周度观察点与 5 年滚动分位。

口径（第三批施工单 §3.2）：
- 观察点 = 每个 ISO 周最后一个交易日；
- 样本 = 观察点之前 5 个日历年内、每周最后一个**有效（正值）**滚动市盈率，每周一条；
  当前周不入池；
- 池内取的是「该周最后一个正值」；观察点自身仍取**当日**值，缺失或非正不给分位
  （收尾项 R1：此前误用「该周最后一个交易日的值」，缺值会让整周作废）；
- 覆盖 = 有效周数 ≥ 该区间内有交易日的周数 × 90%，否则该观察点不可评估；
- 分位公式沿用 `engine.series.window.percentile_of`，不另写第二套。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence

from engine.series.window import percentile_of

WINDOW_YEARS = 5
MIN_COVERAGE = 0.90
ENTRY_PERCENTILE = 20.0
EXIT_PERCENTILE = 60.0
MAX_HOLD_MONTHS = 12


def iso_week(day: str) -> str:
    value = date.fromisoformat(day[:10])
    year, week, _ = value.isocalendar()
    return f"{year:04d}-W{week:02d}"


@dataclass(frozen=True)
class WeeklyObservation:
    week: str
    date: str
    close: float | None
    pe: float | None  # 观察日当天的值（作为「当前值」使用）
    week_pe: float | None = None  # 该周最后一个正值（作为「池内样本」使用）

    def to_dict(self) -> dict[str, Any]:
        return {
            "week": self.week,
            "date": self.date,
            "close": self.close,
            "pe": self.pe,
            "week_pe": self.week_pe,
        }


def build_weekly_observations(
    close_observations: Sequence[tuple[str, float]],
    pe_observations: Sequence[tuple[str, float]],
) -> list[WeeklyObservation]:
    """以交易日历（收盘序列）为准，每周取最后一个交易日作为观察点。

    同时记录该周**最后一个正值 PE**（`week_pe`），供参考池使用：
    池内样本按「每周最后一个有效值」取，避免观察日当天缺值就让整周作废。
    """
    pe_by_date = {d: v for d, v in pe_observations}
    last_in_week: dict[str, tuple[str, float]] = {}
    for day, close in sorted(close_observations):
        last_in_week[iso_week(day)] = (day, close)

    # 每周最后一个正值 PE：按日期升序扫描，后出现的覆盖先出现的
    week_pe: dict[str, float] = {}
    for day, value in sorted(pe_by_date.items()):
        if value is None or value <= 0:
            continue
        week_pe[iso_week(day)] = value

    out: list[WeeklyObservation] = []
    for week in sorted(last_in_week):
        day, close = last_in_week[week]
        out.append(
            WeeklyObservation(
                week=week,
                date=day,
                close=close,
                pe=pe_by_date.get(day),
                week_pe=week_pe.get(week),
            )
        )
    return out


def _years_before(day: str, years: int) -> str:
    value = date.fromisoformat(day[:10])
    try:
        shifted = value.replace(year=value.year - years)
    except ValueError:  # 2 月 29 日
        shifted = value.replace(year=value.year - years, day=28)
    return shifted.isoformat()


@dataclass(frozen=True)
class WeeklyPercentile:
    week: str
    date: str
    pe: float | None
    percentile: float | None
    evaluable: bool
    reason: str | None
    window_start: str
    weeks_in_window: int
    valid_weeks: int
    coverage: float | None
    pool_size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "week": self.week,
            "date": self.date,
            "pe": self.pe,
            "percentile": self.percentile,
            "evaluable": self.evaluable,
            "reason": self.reason,
            "window_start": self.window_start,
            "weeks_in_window": self.weeks_in_window,
            "valid_weeks": self.valid_weeks,
            "coverage": self.coverage,
            "pool_size": self.pool_size,
        }


def weekly_percentiles(
    observations: Sequence[WeeklyObservation],
    *,
    window_years: int = WINDOW_YEARS,
    min_coverage: float = MIN_COVERAGE,
) -> list[WeeklyPercentile]:
    """逐周计算 5 年分位；不插补，不可评估就明确记原因。"""
    ordered = sorted(observations, key=lambda o: o.date)
    out: list[WeeklyPercentile] = []
    for index, obs in enumerate(ordered):
        window_start = _years_before(obs.date, window_years)
        # 当前周不入池：窗口取 (t-5y, t) 之间的观测
        pool_window = [
            other
            for other in ordered[:index]
            if window_start < other.date < obs.date
        ]
        # R1：池内取「每周最后一个正值」，不是「观察日的值」
        valid = [o for o in pool_window if o.week_pe is not None and o.week_pe > 0]
        weeks_in_window = len(pool_window)
        coverage = (len(valid) / weeks_in_window) if weeks_in_window else None

        reason = None
        if obs.pe is None:
            reason = "观察点没有滚动市盈率"
        elif obs.pe <= 0:
            reason = "观察点滚动市盈率非正，不给分位"
        elif ordered[0].date > window_start:
            reason = f"5 年窗口未覆盖：序列自 {ordered[0].date} 起，窗口需要从 {window_start} 起"
        elif weeks_in_window == 0:
            reason = "窗口内没有交易日"
        elif len(valid) < weeks_in_window * min_coverage:
            reason = (
                f"覆盖不足：可用周 {len(valid)}/{weeks_in_window}"
                f"（{len(valid) / weeks_in_window:.1%} < {min_coverage:.0%}）"
            )

        percentile = None
        if reason is None:
            percentile = percentile_of(obs.pe, [o.week_pe for o in valid])
        out.append(
            WeeklyPercentile(
                week=obs.week,
                date=obs.date,
                pe=obs.pe,
                percentile=percentile,
                evaluable=percentile is not None,
                reason=reason,
                window_start=window_start,
                weeks_in_window=weeks_in_window,
                valid_weeks=len(valid),
                coverage=coverage,
                pool_size=len(valid),
            )
        )
    return out


__all__ = [
    "ENTRY_PERCENTILE",
    "EXIT_PERCENTILE",
    "MAX_HOLD_MONTHS",
    "MIN_COVERAGE",
    "WINDOW_YEARS",
    "WeeklyObservation",
    "WeeklyPercentile",
    "build_weekly_observations",
    "iso_week",
    "weekly_percentiles",
]
