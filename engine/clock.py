"""统一时钟：测试可用环境变量模拟「今天」，生产默认读取真实时间。

- `INVESTMENT_SYSTEM_TODAY=YYYY-MM-DD`：把「今天」固定为指定日期（12:00 Asia/Singapore）。
  仅供测试/复现使用；生产环境不设置该变量时行为与原来完全一致。
- 所有默认「分析日/生成时间」都应经过本模块，避免测试依赖真实日期。
"""

from __future__ import annotations

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Singapore")
TODAY_ENV = "INVESTMENT_SYSTEM_TODAY"


class ClockError(ValueError):
    """模拟日期环境变量格式非法。"""


def _override() -> date | None:
    raw = os.environ.get(TODAY_ENV, "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:  # pragma: no cover - 明确报错优于静默
        raise ClockError(f"{TODAY_ENV} 必须是 YYYY-MM-DD：{raw!r}") from exc


def now(*, default: datetime | None = None) -> datetime:
    """返回当前时间；设置了模拟日期时返回该日 12:00（Asia/Singapore）。"""
    if default is not None:
        return default
    fixed = _override()
    if fixed is not None:
        return datetime(fixed.year, fixed.month, fixed.day, 12, 0, 0, tzinfo=TZ)
    return datetime.now(TZ)


def today() -> date:
    return now().date()


__all__ = ["TZ", "TODAY_ENV", "ClockError", "now", "today"]
