"""时间语义：所有时点比较必须先解析成带时区的时间对象。

为什么需要单独一层：
- 之前用字符串比较，`2026-11-10T08:00:00+00:00`（当地 16:00）会被当成早于
  `2026-11-10T12:00:00+08:00`，历史分析会提前用到未来信息；
- 只有日期的公告（`2026-10-31`）不等于「当天 00:00 已可知」；
- 日期精度不足必须显式记录，不能默认精确到秒。

约定：
- 日期字符串按 Asia/Singapore 解释；
- 只有日期的可用时间按「当日结束时才视为已知」处理（保守上界），并标记 precision=date；
- 无时区的时刻按 Asia/Singapore 解释，并标记 precision=second；
- 无法解析一律返回 None（视为「可获得时间未知」），不猜。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_TZ = ZoneInfo("Asia/Singapore")

# 只有日期的公告，保守地视为当日 23:59:59 才可知
_END_OF_DAY = time(23, 59, 59)


class TimeParseError(ValueError):
    """时间字符串无法解析。"""


@dataclass(frozen=True)
class Instant:
    """带时区的时点 + 精度说明。"""

    value: datetime
    precision: str  # "date" | "second"
    source: str

    @property
    def is_date_only(self) -> bool:
        return self.precision == "date"

    def to_iso(self) -> str:
        return self.value.isoformat(timespec="seconds")

    def __le__(self, other: "Instant") -> bool:
        return self.value <= other.value

    def __lt__(self, other: "Instant") -> bool:
        return self.value < other.value


def parse_instant(value: str | None, *, tz: ZoneInfo = DEFAULT_TZ) -> Instant | None:
    """把字符串解析成 Instant；无法解析返回 None（不猜）。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    # 纯日期：YYYY-MM-DD
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            day = date.fromisoformat(text)
        except ValueError:
            return None
        return Instant(datetime.combine(day, _END_OF_DAY, tzinfo=tz), "date", text)

    # ISO 时间；把末尾 Z 换成 +00:00 以便 fromisoformat 处理
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return Instant(parsed, "second", text)


def end_of_day(value: str, *, tz: ZoneInfo = DEFAULT_TZ) -> Instant | None:
    """把一个日期（或带时间的字符串）解释成「该时点的当日结束」（保守上界）。"""
    instant = parse_instant(value, tz=tz)
    if instant is None:
        return None
    if instant.is_date_only:
        return instant
    local = instant.value.astimezone(tz)
    return Instant(
        datetime.combine(local.date(), _END_OF_DAY, tzinfo=tz), "date", instant.source
    )


def is_available_by(available_at: str | None, cutoff: str | None, *, tz: ZoneInfo = DEFAULT_TZ) -> bool:
    """available_at 是否在 cutoff 之前（含同一时刻）。

    - 任一无法解析 → False（宁可不用，也不提前使用未来信息）；
    - 只有日期的可用时间按当日结束时处理。
    """
    available = parse_instant(available_at, tz=tz)
    limit = parse_instant(cutoff, tz=tz)
    if available is None or limit is None:
        return False
    return available.value <= limit.value


def infer_frequency(dates: list[date]) -> str:
    """按相邻观测间隔推断采样频率（用于数据调查，不用于投资判断）。"""
    ordered = sorted(set(dates))
    if len(ordered) < 3:
        return "insufficient_observations"
    gaps = [(b - a).days for a, b in zip(ordered, ordered[1:]) if (b - a).days > 0]
    if not gaps:
        return "insufficient_observations"
    gaps.sort()
    median = gaps[len(gaps) // 2]
    if median <= 3:
        return "daily"
    if median <= 10:
        return "weekly"
    if median <= 20:
        return "biweekly"
    if median <= 45:
        return "monthly"
    if median <= 120:
        return "quarterly"
    return f"irregular(median_gap={median}d)"


__all__ = [
    "DEFAULT_TZ",
    "Instant",
    "TimeParseError",
    "end_of_day",
    "infer_frequency",
    "is_available_by",
    "parse_instant",
]

_ = timedelta
