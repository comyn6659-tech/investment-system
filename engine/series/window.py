"""月度参考窗口与百分位算法。

口径来自第三阶段施工指令 §3：

- 主窗口 = 分析时点所在月份之前的 60 个完整自然月；
- 每月至多一个观测：取该月内最后一个有实际日期、同口径且有效的记录；
- 比较值 = 该来源最新可用的同口径实际观测，显示其真实日期；
- 参考池只含早于比较值日期的记录；比较值若落在参考池里必须移除；
- 有效月份 < 54（90%）时不给分位，也不偷换成「成立以来分位」；
- 分位 = 100 × (小于 + 0.5×等于) / 有效观测数，用原始值计算，展示时才取整。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

WINDOW_MONTHS = 60
MIN_VALID_MONTHS = 54  # 90% 数据完整性工程规则
PERCENTILE_DECIMALS = 1


def month_key(day: str) -> str:
    return str(day)[:7]


def shift_month(month: str, delta: int) -> str:
    year, mon = (int(x) for x in month.split("-"))
    total = year * 12 + (mon - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def month_range(end_month: str, count: int) -> list[str]:
    """返回以 end_month 结尾的 count 个自然月（含端点）。"""
    if count <= 0:
        return []
    return [shift_month(end_month, -(count - 1) + i) for i in range(count)]


def _valid_date(text: str) -> bool:
    try:
        date.fromisoformat(str(text)[:10])
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class MonthlyPoint:
    month: str
    date: str
    value: float

    def to_dict(self) -> dict[str, object]:
        return {"month": self.month, "date": self.date, "value": self.value}


@dataclass(frozen=True)
class WindowResult:
    """一个指标在一次分析时点上的窗口与分位结果。"""

    metric: str
    as_of: str
    analysis_month: str
    window_start_month: str
    window_end_month: str
    expected_months: int
    valid_months: tuple[str, ...]
    missing_months: tuple[str, ...]
    monthly_points: tuple[MonthlyPoint, ...]
    comparison: MonthlyPoint | None
    comparison_removed_from_pool: bool
    pool_size: int
    pool_min: float | None
    pool_median: float | None
    pool_max: float | None
    percentile: float | None
    percentile_display: str | None
    sufficient: bool
    insufficient_reason: str | None
    dropped_non_positive: tuple[MonthlyPoint, ...] = ()
    dropped_future: tuple[MonthlyPoint, ...] = ()
    dropped_duplicate_months: tuple[MonthlyPoint, ...] = ()
    dropped_unparsable: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def coverage_start(self) -> str | None:
        return self.monthly_points[0].date if self.monthly_points else None

    @property
    def coverage_end(self) -> str | None:
        return self.monthly_points[-1].date if self.monthly_points else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "as_of": self.as_of,
            "analysis_month": self.analysis_month,
            "window": {
                "months": self.expected_months,
                "start_month": self.window_start_month,
                "end_month": self.window_end_month,
                "min_valid_months": MIN_VALID_MONTHS,
            },
            "expected_months": self.expected_months,
            "valid_months": len(self.valid_months),
            "valid_month_list": list(self.valid_months),
            "missing_months": list(self.missing_months),
            "coverage_start": self.coverage_start,
            "coverage_end": self.coverage_end,
            "comparison": self.comparison.to_dict() if self.comparison else None,
            "comparison_removed_from_pool": self.comparison_removed_from_pool,
            "pool": {
                "size": self.pool_size,
                "min": self.pool_min,
                "median": self.pool_median,
                "max": self.pool_max,
            },
            "percentile": self.percentile,
            "percentile_display": self.percentile_display,
            "sufficient": self.sufficient,
            "insufficient_reason": self.insufficient_reason,
            "dropped_non_positive": [p.to_dict() for p in self.dropped_non_positive],
            "dropped_future": [p.to_dict() for p in self.dropped_future],
            "dropped_duplicate_months": [p.to_dict() for p in self.dropped_duplicate_months],
            "dropped_unparsable": list(self.dropped_unparsable),
            "notes": list(self.notes),
        }


def percentile_of(current: float, pool: Sequence[float]) -> float | None:
    """100 × (小于 + 0.5×等于) / 有效观测数；池为空时返回 None。"""
    values = [float(v) for v in pool]
    if not values:
        return None
    less = sum(1 for v in values if v < current)
    equal = sum(1 for v in values if v == current)
    return 100.0 * (less + 0.5 * equal) / len(values)


def format_percentile(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{round(value, PERCENTILE_DECIMALS):.{PERCENTILE_DECIMALS}f}"


def build_window(
    observations: Iterable[tuple[str, float]],
    *,
    metric: str,
    as_of: str,
    require_positive: bool = False,
    window_months: int = WINDOW_MONTHS,
    min_valid_months: int = MIN_VALID_MONTHS,
) -> WindowResult:
    """按施工指令的口径构造月度参考池并计算分位。

    `require_positive=True` 用于 PE：非正观测既不进参考池，也不能当比较值。
    """
    if not _valid_date(as_of):
        raise ValueError(f"as_of 不是合法日期: {as_of!r}")
    as_of_day = str(as_of)[:10]
    analysis_month = month_key(as_of_day)
    end_month = shift_month(analysis_month, -1)
    months = month_range(end_month, window_months)
    month_set = set(months)
    window_start, window_end = months[0], months[-1]

    dropped_future: list[MonthlyPoint] = []
    unparsable: list[str] = []
    parsed: list[MonthlyPoint] = []
    for raw_date, raw_value in observations:
        day = str(raw_date)[:10]
        if not _valid_date(day):
            unparsable.append(str(raw_date))
            continue
        point = MonthlyPoint(month=month_key(day), date=day, value=float(raw_value))
        if day > as_of_day:  # 未来/晚于分析时点的记录不参与
            dropped_future.append(point)
            continue
        parsed.append(point)

    parsed.sort(key=lambda p: (p.date, p.value))

    # 先按有效性过滤，再按月取最后一条：约定的「每月最后一个有效观测」，
    # 不是「取最后一条再判断是否有效」——后者会让月中有效、月末无效的月份整月消失。
    eligible: list[MonthlyPoint] = []
    dropped_non_positive: list[MonthlyPoint] = []
    for point in parsed:
        if require_positive and point.value <= 0:
            dropped_non_positive.append(point)
            continue
        eligible.append(point)

    by_month: dict[str, MonthlyPoint] = {}
    duplicates: list[MonthlyPoint] = []
    for point in eligible:
        existing = by_month.get(point.month)
        if existing is not None:
            duplicates.append(existing)
        by_month[point.month] = point

    # 比较值一律取最新可用的同口径实际观测；PE 若该值非正，就没有可用分位，
    # 不允许退回更早的正值来冒充「当前值」。
    comparison: MonthlyPoint | None = parsed[-1] if parsed else None
    if (
        comparison is not None
        and require_positive
        and comparison.value <= 0
        and comparison not in dropped_non_positive
    ):
        dropped_non_positive.append(comparison)

    monthly_points = [by_month[month] for month in months if month in by_month]

    comparison_removed = False
    pool_points = list(monthly_points)
    if comparison is not None:
        kept: list[MonthlyPoint] = []
        for point in pool_points:
            if point.date == comparison.date and point.value == comparison.value:
                comparison_removed = True
                continue
            kept.append(point)
        pool_points = kept

    valid_months = tuple(p.month for p in monthly_points)
    missing_months = tuple(m for m in months if m not in set(valid_months))
    pool_values = [p.value for p in pool_points]

    insufficient_reason: str | None = None
    if comparison is None:
        insufficient_reason = "没有可用的同口径比较值（序列为空）"
    elif require_positive and comparison.value <= 0:
        insufficient_reason = (
            f"最新观测 {comparison.value} 非正（{comparison.date}）：PE 非正时不参与分布，也不给分位"
        )
    elif len(pool_points) < min_valid_months:
        insufficient_reason = (
            f"有效月份 {len(pool_points)} 个，少于要求的 {min_valid_months} 个（{min_valid_months / window_months:.0%}）"
        )

    percentile = None
    if (
        insufficient_reason is None
        and comparison is not None
        and (not require_positive or comparison.value > 0)
    ):
        percentile = percentile_of(comparison.value, pool_values)

    notes: list[str] = [
        "分位是该来源历史样本中的相对位置，不是未来上涨概率，也不是内在价值折扣。",
        "参考池只包含早于比较值日期的记录；比较值若落在参考池中已移除。",
    ]
    if dropped_future:
        notes.append(f"{len(dropped_future)} 条晚于分析时点的记录已剔除。")
    if duplicates:
        notes.append(f"{len(duplicates)} 条同月较早记录按「每月最后一个观测」规则丢弃。")
    if dropped_non_positive:
        notes.append(
            f"{len(dropped_non_positive)} 个非正观测被剔除，未进入本指标分布"
            "（PE 非正不参与）；同月若仍有有效观测，该月仍保留。"
        )

    return WindowResult(
        metric=metric,
        as_of=as_of_day,
        analysis_month=analysis_month,
        window_start_month=window_start,
        window_end_month=window_end,
        expected_months=window_months,
        valid_months=valid_months,
        missing_months=missing_months,
        monthly_points=tuple(monthly_points),
        comparison=comparison,
        comparison_removed_from_pool=comparison_removed,
        pool_size=len(pool_points),
        pool_min=min(pool_values) if pool_values else None,
        pool_median=statistics.median(pool_values) if pool_values else None,
        pool_max=max(pool_values) if pool_values else None,
        percentile=percentile,
        percentile_display=format_percentile(percentile),
        sufficient=insufficient_reason is None,
        insufficient_reason=insufficient_reason,
        dropped_non_positive=tuple(dropped_non_positive),
        dropped_future=tuple(dropped_future),
        dropped_duplicate_months=tuple(duplicates),
        dropped_unparsable=tuple(unparsable),
        notes=tuple(notes),
    )


__all__ = [
    "MIN_VALID_MONTHS",
    "MonthlyPoint",
    "PERCENTILE_DECIMALS",
    "WINDOW_MONTHS",
    "WindowResult",
    "build_window",
    "format_percentile",
    "month_key",
    "month_range",
    "percentile_of",
    "shift_month",
]
