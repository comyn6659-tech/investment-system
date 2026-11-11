"""回测统计：权益曲线、回撤、暴露、换手、分区间与逐年表现。

所有指标都从「逐日权益曲线」和「逐笔成交」推出，口径在文档里写明：
- 空仓时现金按 0% 计息（不产生收益）；
- 全收益口径与价格口径分开计算，不混用；
- 成本按单边 bp，买卖各一次，用乘性方式扣除。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def build_equity_curve(
    calendar: Sequence[str],
    prices: dict[str, float],
    trades: Sequence[dict[str, Any]],
    *,
    start: str,
    end: str,
    cost_bp: float = 0.0,
) -> list[dict[str, Any]]:
    """逐日权益曲线：持仓日跟随价格，空仓日不变（现金 0%）。

    成本在买入日与卖出日各扣一次单边 `cost_bp`，与逐笔净收益的 (1-c)^2 口径一致，
    因此「逐笔净收益连乘」必须等于曲线终值（有回归）。
    """
    cost = cost_bp / 10000.0
    entries = {t["entry_exec_date"] for t in trades if t["exit_exec_date"] > t["entry_exec_date"]}
    exits = {t["exit_exec_date"] for t in trades if t["exit_exec_date"] > t["entry_exec_date"]}
    intervals: list[tuple[str, str]] = [
        (t["entry_exec_date"], t["exit_exec_date"])
        for t in trades
        if t["exit_exec_date"] > t["entry_exec_date"]
    ]

    curve: list[dict[str, Any]] = []
    equity = 1.0
    previous_price: float | None = None
    days = [d for d in calendar if start <= d <= end]
    for day in days:
        price = prices.get(day)
        if price is None:
            continue
        if day in entries:
            equity *= 1.0 - cost
        holding = any(entry < day <= exit_ for entry, exit_ in intervals)
        if holding and previous_price is not None and previous_price > 0:
            equity *= price / previous_price
        if day in exits:
            equity *= 1.0 - cost
        curve.append({"date": day, "equity": equity, "in_position": holding})
        previous_price = price
    return curve


def max_drawdown(curve: Sequence[dict[str, Any]]) -> dict[str, Any]:
    peak = None
    worst = 0.0
    worst_start = None
    worst_end = None
    current_start = None
    for point in curve:
        value = point["equity"]
        if peak is None or value > peak:
            peak = value
            current_start = point["date"]
            continue
        drawdown = value / peak - 1.0
        if drawdown < worst:
            worst = drawdown
            worst_start = current_start
            worst_end = point["date"]
    duration = _days(worst_start, worst_end) if worst_start and worst_end else 0
    return {
        "max_drawdown": worst,
        "peak_date": worst_start,
        "trough_date": worst_end,
        "duration_days": duration,
        "note": "从创新高到最低点；未计恢复时间",
    }


def summarize_window(
    trades: Sequence[dict[str, Any]],
    curve: Sequence[dict[str, Any]],
    *,
    start: str,
    end: str,
    price_calendar: Sequence[str],
    benchmark_tr: float | None = None,
    benchmark_price: float | None = None,
) -> dict[str, Any]:
    window_trades = [t for t in trades if start <= t["entry_exec_date"] <= end]
    equity_start = 1.0
    equity_end = curve[-1]["equity"] if curve else 1.0
    # 只取窗口内的权益变化：以窗口第一天为 1
    if curve:
        base = None
        for point in curve:
            if point["date"] >= start:
                base = point["equity"]
                break
        if base:
            equity_end = curve[-1]["equity"] / base
    cumulative = equity_end - 1.0
    days = _days(start, end) if start < end else 0
    annualized = (equity_end) ** (365.0 / days) - 1.0 if days > 0 and equity_end > 0 else None
    trading_days = [d for d in price_calendar if start <= d <= end]
    in_position_days = sum(1 for p in curve if p["in_position"] and start <= p["date"] <= end)
    net_returns = [t["net_return"] for t in window_trades]
    exits = [t["exit_reason"] for t in window_trades]
    holding_days = [t["holding_days"] for t in window_trades]
    years = days / 365.25 if days > 0 else 0.0
    return {
        "start": start,
        "end": end,
        "trades": len(window_trades),
        "cumulative_return": cumulative,
        "annualized_return": annualized,
        "max_drawdown": max_drawdown([p for p in curve if start <= p["date"] <= end]),
        "trading_days": len(trading_days),
        "days_in_position": in_position_days,
        "market_exposure": (in_position_days / len(trading_days)) if trading_days else None,
        "turnover_per_year": (2 * len(window_trades) / years) if years > 0 else None,
        "worst_trade": min(net_returns) if net_returns else None,
        "best_trade": max(net_returns) if net_returns else None,
        "median_trade": sorted(net_returns)[len(net_returns) // 2] if net_returns else None,
        "positive_trades": sum(1 for r in net_returns if r > 0),
        "expiry_exits": sum(1 for reason in exits if reason == "expiry_exit"),
        "expiry_exit_ratio": (
            sum(1 for reason in exits if reason == "expiry_exit") / len(exits) if exits else None
        ),
        "exit_reasons": {
            reason: sum(1 for r in exits if r == reason)
            for reason in sorted(set(exits))
        },
        "holding_days": {
            "min": min(holding_days) if holding_days else None,
            "median": sorted(holding_days)[len(holding_days) // 2] if holding_days else None,
            "max": max(holding_days) if holding_days else None,
            "list": holding_days,
        },
        "benchmark_total_return_buy_hold": benchmark_tr,
        "benchmark_price_buy_hold": benchmark_price,
        "excess_vs_total_return": (
            cumulative - benchmark_tr if benchmark_tr is not None else None
        ),
        "excess_vs_price": (cumulative - benchmark_price if benchmark_price is not None else None),
    }


def yearly_breakdown(
    trades: Sequence[dict[str, Any]],
    curve: Sequence[dict[str, Any]],
    *,
    calendar: Sequence[str],
) -> list[dict[str, Any]]:
    years = sorted({d[:4] for d in calendar})
    out: list[dict[str, Any]] = []
    for year in years:
        start = max(f"{year}-01-01", calendar[0])
        end = min(f"{year}-12-31", calendar[-1])
        points = [p for p in curve if start <= p["date"] <= end]
        if not points:
            continue
        base = points[0]["equity"]
        final = points[-1]["equity"]
        window_trades = [t for t in trades if start <= t["entry_exec_date"] <= end]
        out.append(
            {
                "year": year,
                "return": (final / base - 1.0) if base else None,
                "trades": len(window_trades),
                "exposure": (
                    sum(1 for p in points if p["in_position"]) / len(points) if points else None
                ),
            }
        )
    return out


def buy_and_hold(
    prices: dict[str, float], calendar: Sequence[str], *, start: str, end: str
) -> float | None:
    days = [d for d in calendar if start <= d <= end and d in prices]
    if len(days) < 2:
        return None
    return prices[days[-1]] / prices[days[0]] - 1.0


__all__ = [
    "build_equity_curve",
    "buy_and_hold",
    "max_drawdown",
    "summarize_window",
    "yearly_breakdown",
]
