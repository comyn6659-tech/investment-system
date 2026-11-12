"""数据新鲜度护栏（决策②后的运行参数，不是投资规则）。

三个条件任一触发，个股政策结果一律 `undetermined` 并给原因码：
1. 价格日期距分析日超过 `price_max_trading_days` 个交易日；
2. 按披露时限（年报 4 月 30 日）应当已有更新年报，但快照未包含；
3. 快照缺少政策计算所需字段。

交易日按周一至周五计数（不含法定节假日；本项目没有交易日历，宁可略严）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from engine.contracts import reason_codes as rc
from engine.data.snapshot import Snapshot
from engine.financials.annual import load_annual_history
from engine.policy.equity import EquityPolicy


@dataclass(frozen=True)
class FreshnessHit:
    code: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def trading_days_between(start: date, end: date) -> int:
    """start 之后到 end（含）之间的工作日数；start >= end 返回 0。"""
    if end <= start:
        return 0
    days = 0
    cursor = start + timedelta(days=1)
    while cursor <= end:
        if cursor.weekday() < 5:
            days += 1
        cursor += timedelta(days=1)
    return days


def expected_latest_annual(analysis_date: date, due_month: int, due_day: int) -> int:
    """按年报披露时限推断分析日应当能看到的最新财年。"""
    due = date(analysis_date.year, due_month, due_day)
    return analysis_date.year - 1 if analysis_date >= due else analysis_date.year - 2


def _runtime(policy: EquityPolicy) -> dict[str, Any]:
    node = (policy.data.get("runtime_parameters") or {}).get("freshness") or {}
    if not node:
        raise ValueError("已批准政策缺少 runtime_parameters.freshness")
    return node


def check_freshness(
    policy: EquityPolicy, snapshot: Snapshot, *, analysis_date: date
) -> list[FreshnessHit]:
    runtime = _runtime(policy)
    hits: list[FreshnessHit] = []

    # 1) 价格日期
    price = snapshot.fact("price.close")
    price_date = _parse_date(price.period if price is not None else None)
    if price is None or not price.is_usable_number or price_date is None:
        hits.append(FreshnessHit(rc.MISSING_POLICY_FIELD, "缺少可用的已收盘价格事实 price.close"))
    else:
        elapsed = trading_days_between(price_date, analysis_date)
        limit = int(runtime["price_max_trading_days"])
        if elapsed > limit:
            hits.append(
                FreshnessHit(
                    rc.STALE_PRICE,
                    f"价格日期 {price_date.isoformat()} 距分析日 {analysis_date.isoformat()} "
                    f"已 {elapsed} 个交易日，超过 {limit} 个交易日",
                )
            )

    # 2) 更新年报
    due = runtime.get("annual_report_due") or {"month": 4, "day": 30}
    expected = expected_latest_annual(
        analysis_date, int(due["month"]), int(due["day"])
    )
    history = load_annual_history(snapshot)
    latest = history.latest.fiscal_year if history.latest is not None else None
    if latest is None or latest < expected:
        hits.append(
            FreshnessHit(
                rc.STALE_FINANCIALS,
                f"按披露时限应已包含 {expected} 年年报，快照最新年报为 "
                f"{latest if latest is not None else '无'}",
            )
        )

    # 3) 政策所需字段
    if latest is not None:
        for suffix in (runtime.get("required_fields") or {}).get("annual", []):
            fact = snapshot.fact(f"fin.{latest}-12-31.{suffix}")
            if fact is None or not fact.is_usable_number:
                hits.append(
                    FreshnessHit(
                        rc.MISSING_POLICY_FIELD,
                        f"缺少 {latest} 年报字段 fin.{latest}-12-31.{suffix}",
                    )
                )
    for fact_id in (runtime.get("required_fields") or {}).get("profile", []):
        fact = snapshot.fact(fact_id)
        if fact is None or not fact.is_usable_number:
            hits.append(FreshnessHit(rc.MISSING_POLICY_FIELD, f"缺少字段 {fact_id}"))

    return hits


__all__ = [
    "FreshnessHit",
    "check_freshness",
    "expected_latest_annual",
    "trading_days_between",
]
