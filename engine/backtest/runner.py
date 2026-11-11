"""单个指数的候选 A 研究回测编排。

数据 → 周度观察点 → 5 年分位 → 状态机（T+1 主口径 / T+2 敏感性）
→ 逐笔成交 → 权益曲线 → 全样本 / 开发区间 / 留出区间 / 逐年统计。

口径固定：全收益为主、价格并列；成本 5/10/20 bp 单边；现金 0% 计息；
阈值与持有上限来自施工单冻结值，本模块不接受调参。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

from engine.backtest.metrics import (
    build_equity_curve,
    buy_and_hold,
    summarize_window,
    yearly_breakdown,
)
from engine.backtest.state_machine import run_state_machine
from engine.backtest.weekly import (
    ENTRY_PERCENTILE,
    EXIT_PERCENTILE,
    MAX_HOLD_MONTHS,
    build_weekly_observations,
    weekly_percentiles,
)

DEFAULT_COSTS = (5.0, 10.0, 20.0)
DEFAULT_LAGS = (1, 2)
SPLIT_DATE = "2021-01-01"


def data_quality(
    *,
    close_observations: Sequence[tuple[str, float]],
    pe_observations: Sequence[tuple[str, float]],
    launch_date: str,
) -> dict[str, Any]:
    """逐年数据质量：交易日、PE 非空、PE 非正、与收盘交易日的对齐情况。"""
    close_days = {d for d, _ in close_observations if d >= launch_date}
    pe_map = {d: v for d, v in pe_observations if d >= launch_date}
    years: dict[str, dict[str, int]] = {}
    for day in sorted(close_days):
        year = day[:4]
        bucket = years.setdefault(
            year, {"trading_days": 0, "pe_present": 0, "pe_positive": 0, "pe_non_positive": 0}
        )
        bucket["trading_days"] += 1
        if day in pe_map:
            bucket["pe_present"] += 1
            if pe_map[day] > 0:
                bucket["pe_positive"] += 1
            else:
                bucket["pe_non_positive"] += 1
    pe_only_days = sorted(set(pe_map) - close_days)
    return {
        "launch_date": launch_date,
        "years": [
            {
                "year": year,
                **counts,
                "pe_coverage": (counts["pe_present"] / counts["trading_days"])
                if counts["trading_days"]
                else None,
            }
            for year, counts in sorted(years.items())
        ],
        "pe_days_not_in_close_calendar": len(pe_only_days),
        "pe_days_not_in_close_calendar_sample": pe_only_days[:5],
        "first_close": min(close_days) if close_days else None,
        "last_close": max(close_days) if close_days else None,
        "first_pe": min(pe_map) if pe_map else None,
        "last_pe": max(pe_map) if pe_map else None,
        "trading_days_total": len(close_days),
        "pe_present_total": sum(1 for d in close_days if d in pe_map),
    }


def _reason_category(reason: str | None) -> str:
    if not reason:
        return "未知"
    for prefix in ("覆盖不足", "5 年窗口未覆盖", "观察点没有滚动市盈率", "观察点滚动市盈率非正", "窗口内没有交易日"):
        if reason.startswith(prefix):
            return prefix
    return reason


def _reason_summary(percentiles) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in percentiles:
        if item.evaluable:
            continue
        key = _reason_category(item.reason)
        summary[key] = summary.get(key, 0) + 1
    return summary


def _price_map(observations: Sequence[tuple[str, float]]) -> dict[str, float]:
    return {d: v for d, v in observations}


def _run_basis(
    *,
    observations: Sequence[dict[str, Any]],
    calendar: Sequence[str],
    prices: Mapping[str, float],
    evaluation_start: str,
    evaluation_end: str,
    cost_scenarios: Sequence[float],
    lags: Sequence[int],
    split_date: str,
    benchmarks: Mapping[str, Mapping[str, float | None]],
) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for lag in lags:
        per_cost: dict[str, Any] = {}
        for cost in cost_scenarios:
            run = run_state_machine(
                observations,
                calendar=calendar,
                prices=dict(prices),
                lag=lag,
                cost_bp=cost,
                entry_threshold=ENTRY_PERCENTILE,
                exit_threshold=EXIT_PERCENTILE,
                max_hold_months=MAX_HOLD_MONTHS,
                sample_end=evaluation_end,
            )
            trades = run["trades"]
            curve = build_equity_curve(
                calendar,
                dict(prices),
                trades,
                start=evaluation_start,
                end=evaluation_end,
                cost_bp=cost,
            )
            full = summarize_window(
                trades,
                curve,
                start=evaluation_start,
                end=evaluation_end,
                price_calendar=calendar,
                benchmark_tr=(benchmarks["full"] or {}).get("tr"),
                benchmark_price=(benchmarks["full"] or {}).get("price"),
            )
            dev = summarize_window(
                [t for t in trades if t["entry_exec_date"] < split_date],
                [p for p in curve if p["date"] < split_date],
                start=evaluation_start,
                end=min(split_date, evaluation_end),
                price_calendar=calendar,
                benchmark_tr=(benchmarks["development"] or {}).get("tr"),
                benchmark_price=(benchmarks["development"] or {}).get("price"),
            )
            holdout = summarize_window(
                [t for t in trades if t["entry_exec_date"] >= split_date],
                [p for p in curve if p["date"] >= split_date],
                start=max(split_date, evaluation_start),
                end=evaluation_end,
                price_calendar=calendar,
                benchmark_tr=(benchmarks["holdout"] or {}).get("tr"),
                benchmark_price=(benchmarks["holdout"] or {}).get("price"),
            )
            per_cost[str(int(cost))] = {
                "cost_bp": cost,
                "full": full,
                "development": dev,
                "holdout": holdout,
                "yearly": yearly_breakdown(trades, curve, calendar=calendar),
                "decisions_summary": {
                    "entry_signals": sum(
                        1 for d in run["decisions"] if d["action"] == "entry_signal"
                    ),
                    "exit_signals": sum(
                        1 for d in run["decisions"] if d["action"] == "exit_signal"
                    ),
                    "not_evaluable_holding": sum(
                        1 for d in run["decisions"] if d["action"] == "not_evaluable_holding"
                    ),
                },
                "trade_list": trades,
            }
        results[f"lag{lag}"] = per_cost
    return results


def run_index_backtest(
    *,
    instrument_id: str,
    close_observations: Sequence[tuple[str, float]],
    pe_observations: Sequence[tuple[str, float]],
    tr_observations: Sequence[tuple[str, float]],
    launch_date: str,
    series_ids: Mapping[str, str],
    cost_scenarios: Sequence[float] = DEFAULT_COSTS,
    lags: Sequence[int] = DEFAULT_LAGS,
    split_date: str = SPLIT_DATE,
) -> dict[str, Any]:
    quality = data_quality(
        close_observations=close_observations,
        pe_observations=pe_observations,
        launch_date=launch_date,
    )
    weekly = build_weekly_observations(close_observations, pe_observations)
    weekly = [w for w in weekly if w.date >= launch_date]
    percentiles = weekly_percentiles(weekly)
    evaluable = [p for p in percentiles if p.evaluable]
    if not evaluable:
        return {
            "instrument_id": instrument_id,
            "series_ids": dict(series_ids),
            "launch_date": launch_date,
            "data_quality": quality,
            "weekly": {"total_weeks": len(percentiles), "evaluable": 0},
            "runs": {},
            "note": "没有任何可评估的观察点",
        }

    evaluation_start = evaluable[0].date
    evaluation_end = max(d for d, _ in close_observations)
    tr_end = max(d for d, _ in tr_observations)
    evaluation_end = min(evaluation_end, tr_end)

    observations = [
        {
            "date": p.date,
            "week": p.week,
            "percentile": p.percentile,
            "evaluable": p.evaluable,
        }
        for p in percentiles
    ]
    close_calendar = sorted(d for d, _ in close_observations if d >= launch_date)
    tr_calendar = sorted(d for d, _ in tr_observations if d >= launch_date)

    tr_prices = _price_map(tr_observations)
    close_prices = _price_map(close_observations)

    windows = {
        "full": (evaluation_start, evaluation_end),
        "development": (evaluation_start, min(split_date, evaluation_end)),
        "holdout": (max(split_date, evaluation_start), evaluation_end),
    }
    benchmarks = {
        key: {
            "tr": buy_and_hold(tr_prices, tr_calendar, start=start, end=end),
            "price": buy_and_hold(close_prices, close_calendar, start=start, end=end),
        }
        for key, (start, end) in windows.items()
    }

    runs = {
        "total_return": _run_basis(
            observations=observations,
            calendar=tr_calendar,
            prices=tr_prices,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            cost_scenarios=cost_scenarios,
            lags=lags,
            split_date=split_date,
            benchmarks=benchmarks,
        ),
        "price": _run_basis(
            observations=observations,
            calendar=close_calendar,
            prices=close_prices,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
            cost_scenarios=cost_scenarios,
            lags=lags,
            split_date=split_date,
            benchmarks=benchmarks,
        ),
    }

    return {
        "instrument_id": instrument_id,
        "series_ids": dict(series_ids),
        "launch_date": launch_date,
        "evaluation_window": {"start": evaluation_start, "end": evaluation_end},
        "split_date": split_date,
        "thresholds": {
            "entry_percentile": ENTRY_PERCENTILE,
            "exit_percentile": EXIT_PERCENTILE,
            "max_hold_months": MAX_HOLD_MONTHS,
            "note": "施工单冻结值，未做任何搜索",
        },
        "benchmarks": {
            "total_return_buy_hold": benchmarks["full"]["tr"],
            "price_buy_hold": benchmarks["full"]["price"],
            "cash_return": 0.0,
            "by_window": benchmarks,
            "note": "全样本与开发/留出区间分别报告同期买入持有基准（收尾项 R2）",
        },
        "data_quality": quality,
        "weekly": {
            "total_weeks": len(percentiles),
            "evaluable": len(evaluable),
            "not_evaluable": len(percentiles) - len(evaluable),
            "first_evaluable": evaluable[0].date,
            "last_evaluable": evaluable[-1].date,
            "not_evaluable_summary": _reason_summary(percentiles),
            "not_evaluable_reasons": sorted(
                {_reason_category(p.reason) for p in percentiles if not p.evaluable and p.reason}
            ),
            "observations": [p.to_dict() for p in percentiles],
        },
        "runs": runs,
    }


__all__ = [
    "DEFAULT_COSTS",
    "DEFAULT_LAGS",
    "SPLIT_DATE",
    "data_quality",
    "run_index_backtest",
]
