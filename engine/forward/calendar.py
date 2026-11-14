"""中国 A 股交易日历（版本化，只用于判断是否需要抓取当日行情）。

覆盖 2026 年交易所公告的节假日休市；周末始终休市。超出已版本化年份时只按周末判断，
并在 note 中提示覆盖范围，不猜测未来节假日。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATH = ROOT / "data" / "calendar" / "cn-trading-holidays-2026.json"


class CalendarError(RuntimeError):
    pass


@lru_cache(maxsize=2)
def _load(path: str) -> dict[str, Any]:
    node = Path(path)
    if not node.exists():
        raise CalendarError(f"交易日历不存在：{node}")
    payload = json.loads(node.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "cn-trading-calendar/1":
        raise CalendarError(f"交易日历 schema 非法：{payload.get('schema_version')!r}")
    return payload


def load(path: Path | None = None) -> dict[str, Any]:
    return _load(str(Path(path or DEFAULT_PATH)))


def _in_ranges(day: date, ranges: list[list[str]]) -> bool:
    text = day.isoformat()
    return any(start <= text <= end for start, end in ranges)


def is_trading_day(day: date, *, path: Path | None = None) -> bool:
    if day.weekday() >= 5:
        return False
    payload = load(path)
    if day.year != int(payload.get("year", 0)):
        return True
    return not _in_ranges(day, payload.get("closed_ranges") or [])


def previous_trading_day(day: date, *, path: Path | None = None) -> date:
    current = day
    for _ in range(30):
        if is_trading_day(current, path=path):
            return current
        current -= timedelta(days=1)
    raise CalendarError("找不到上一个交易日")


def latest_trading_day(now: date, *, path: Path | None = None) -> date:
    return previous_trading_day(now, path=path)


def status_note(day: date, *, path: Path | None = None) -> str:
    payload = load(path)
    if int(payload.get("year", 0)) != day.year:
        return f"交易日历只版本化到 {payload.get('year')} 年；{day.isoformat()} 仅按周末判断"
    return f"交易日历 {payload.get('year')}（上交所休市安排）"


__all__ = [
    "CalendarError",
    "DEFAULT_PATH",
    "is_trading_day",
    "latest_trading_day",
    "load",
    "previous_trading_day",
    "status_note",
]
