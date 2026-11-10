"""ETF 作为交易载体的执行检查（阶段三实现）。

设计书 §8：指数信号相同不代表产品执行条件相同。
阶段一只记录事实结构，不计算任何执行结论。
"""

from __future__ import annotations

EXECUTION_FIELDS = (
    "etf.price",
    "etf.nav",
    "etf.nav_date",
    "etf.nav_disclosed_at",
    "etf.premium_discount_pct",
    "etf.management_fee_pct",
    "etf.custody_fee_pct",
    "etf.trading_status",
    "etf.tracking_note",
)


def implemented() -> bool:
    return False
