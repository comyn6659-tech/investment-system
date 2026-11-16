"""「最近变动」：页面与 `./ask` 共用同一份取数与排序。

只追加的 `changes.jsonl` 永久保留（满足「至少保留 1 年」）；展示默认看最近 365 天。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from engine.monitoring.ledger import MonitoringStore

TZ = ZoneInfo("Asia/Singapore")
DEFAULT_WINDOW_DAYS = 365
DEFAULT_LIMIT = 50


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(TZ)


def _parse_day(value: Any) -> date | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def recent_changes(
    store: MonitoringStore,
    *,
    now: datetime | None = None,
    days: int = DEFAULT_WINDOW_DAYS,
    limit: int | None = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    clock = _now(now)
    start = clock.date() - timedelta(days=days)
    rows = []
    for row in store.changes():
        day = _parse_day(row.get("date"))
        if day is None or day < start:
            continue
        rows.append(
            {
                "record_id": str(row.get("record_id") or ""),
                "date": day.isoformat(),
                "type": str(row.get("type") or ""),
                "code": str(row.get("code") or ""),
                "name": str(row.get("name") or ""),
                "detail": str(row.get("detail") or ""),
                "evidence": row.get("evidence") or {},
                "recorded_at": str(row.get("recorded_at") or ""),
            }
        )
    rows.sort(key=lambda item: (item["date"], item["recorded_at"]), reverse=True)
    if limit is not None:
        rows = rows[:limit]
    return rows


def changes_data(
    store: MonitoringStore,
    *,
    now: datetime | None = None,
    days: int = DEFAULT_WINDOW_DAYS,
    limit: int | None = DEFAULT_LIMIT,
) -> dict[str, Any]:
    items = recent_changes(store, now=now, days=days, limit=limit)
    return {
        "items": items,
        "count": len(items),
        "window_days": days,
        "total": len(store.changes()),
        "source": "data/ledger/changes.jsonl（只追加，至少保留 1 年）",
    }


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_WINDOW_DAYS",
    "changes_data",
    "recent_changes",
]
