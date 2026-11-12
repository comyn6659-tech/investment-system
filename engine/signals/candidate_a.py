"""候选 A 研究信号：与回测同源的最新周度观察点。

规则（第三批施工单 §3，冻结不动）：
- 观察点 = 每个 ISO 周最后一个交易日；
- 分位 = 观察点官方滚动市盈率在之前 5 个日历年周度池中的位置；
- 入场条件：分位 ≤ 20；退出条件：分位 ≥ 60；持有满 12 个日历月到期；
- 本模块只报告「条件是否成立」，不跟踪实际持仓，也不产生买卖指令；
- 信号明确标注 research / 未批准，结论仍为 undetermined。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from engine.backtest.weekly import (
    ENTRY_PERCENTILE,
    EXIT_PERCENTILE,
    MAX_HOLD_MONTHS,
    MIN_COVERAGE,
    WINDOW_YEARS,
    build_weekly_observations,
    iso_week,
    weekly_percentile_at,
)
from engine.series.models import (
    FREQ_DAILY,
    METRIC_OFFICIAL_CLOSE,
    METRIC_OFFICIAL_PE_ROLLING,
    METRIC_TOTAL_RETURN_CLOSE,
    SeriesStore,
)
from engine.signals.ledger import RULE_VERSION

BACKTEST_JSON = "data/backtest/candidate-a.json"
BACKTEST_REPORT = "docs/phase3c-backtest.md"
MODEL_ID = "index_strategy_a_valuation_reversion"
MAIN_BASIS = "total_return"
MAIN_LAG = 1
MAIN_COST_BP = 10.0


def classify_signal(
    percentile: float | None,
    evaluable: bool,
    *,
    entry: float = ENTRY_PERCENTILE,
    exit_: float = EXIT_PERCENTILE,
) -> str:
    if not evaluable or percentile is None:
        return "not_evaluable"
    if percentile <= entry:
        return "entry"
    if percentile >= exit_:
        return "exit"
    return "none"


def _index_code(instrument_id: str) -> str:
    # INDEX:000300.SH → 000300
    body = instrument_id.split(":", 1)[-1]
    return body.split(".", 1)[0]


def load_backtest_summary(
    path: str | Path = BACKTEST_JSON, instrument_id: str = ""
) -> dict[str, Any] | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    entry = (data.get("indices") or {}).get(_index_code(instrument_id))
    if not entry:
        return None
    runs = entry.get("runs") or {}
    run = ((runs.get(MAIN_BASIS) or {}).get(f"lag{MAIN_LAG}") or {}).get(
        f"{int(MAIN_COST_BP)}"
    )
    if not run:
        return None
    full = run.get("full") or {}
    benchmarks = entry.get("benchmarks") or {}
    return {
        "return_basis": MAIN_BASIS,
        "execution_lag": MAIN_LAG,
        "cost_bp": MAIN_COST_BP,
        "start": full.get("start"),
        "end": full.get("end"),
        "trades": full.get("trades"),
        "cumulative_return": full.get("cumulative_return"),
        "annualized_return": full.get("annualized_return"),
        "max_drawdown": (full.get("max_drawdown") or {}).get("max_drawdown"),
        "benchmark_total_return": benchmarks.get("total_return_buy_hold"),
        "excess_vs_total_return": full.get("excess_vs_total_return"),
        "generated_at": data.get("generated_at"),
        "report_path": BACKTEST_REPORT,
        "data_path": BACKTEST_JSON,
        "note": (
            "回测记录摘要，只用于说明该规则的历史测算结果；"
            "样本交易次数少、理论指数不可直接投资，不能据此推断规则好坏"
        ),
    }


def build_candidate_a_signal(
    store: SeriesStore,
    instrument_id: str,
    *,
    as_of: str,
    backtest_path: str | Path = BACKTEST_JSON,
) -> dict[str, Any]:
    """返回最新周度观察点的研究信号（缺序列时如实说明）。"""
    close = store.find(instrument_id, METRIC_OFFICIAL_CLOSE, frequency=FREQ_DAILY)
    pe = store.find(instrument_id, METRIC_OFFICIAL_PE_ROLLING, frequency=FREQ_DAILY)
    tr = store.find(instrument_id, METRIC_TOTAL_RETURN_CLOSE, frequency=FREQ_DAILY)

    rule = {
        "entry_percentile": ENTRY_PERCENTILE,
        "exit_percentile": EXIT_PERCENTILE,
        "window_years": WINDOW_YEARS,
        "min_coverage": MIN_COVERAGE,
        "max_hold_months": MAX_HOLD_MONTHS,
        "execution": "信号在观察点收盘后形成，研究回测按下一交易日收盘成交（T+1）",
        "rule_version": RULE_VERSION,
    }
    base: dict[str, Any] = {
        "model_id": MODEL_ID,
        "model_status": "research",
        "is_trading_signal": False,
        "research_signal": True,
        "approved": False,
        "label": "候选 A 研究信号（未批准）",
        "rule": rule,
        "as_of": as_of,
        "series_ids": {
            "close": close.series_id if close else None,
            "pe": pe.series_id if pe else None,
            "total_return": tr.series_id if tr else None,
        },
        "backtest": load_backtest_summary(backtest_path, instrument_id),
    }

    if close is None or pe is None:
        base.update(
            {
                "status": "insufficient_data",
                "signal": "not_evaluable",
                "reason": "缺少官方指数收盘或官方滚动市盈率日频序列，无法计算周度分位",
                "latest": None,
            }
        )
        return base

    observations = [
        obs
        for obs in build_weekly_observations(close.observations, pe.observations)
        if obs.date <= as_of
    ]
    if not observations:
        base.update(
            {
                "status": "insufficient_data",
                "signal": "not_evaluable",
                "reason": f"官方序列在 {as_of} 之前没有周度观察点",
                "latest": None,
            }
        )
        return base

    ordered = sorted(observations, key=lambda obs: obs.date)
    point = weekly_percentile_at(ordered, len(ordered) - 1)
    signal = classify_signal(point.percentile, point.evaluable)
    last_day = date.fromisoformat(point.date)
    as_of_day = date.fromisoformat(as_of)
    week_complete = not (
        iso_week(point.date) == iso_week(as_of) and as_of_day.isoweekday() < 5
    )

    base.update(
        {
            "status": "complete" if point.evaluable else "insufficient_data",
            "signal": signal,
            "entry_condition_met": bool(
                point.evaluable
                and point.percentile is not None
                and point.percentile <= ENTRY_PERCENTILE
            ),
            "exit_condition_met": bool(
                point.evaluable
                and point.percentile is not None
                and point.percentile >= EXIT_PERCENTILE
            ),
            "position_context": "只描述条件是否成立；本报告不跟踪实际持仓，也不产生买卖指令",
            "latest": {
                **point.to_dict(),
                # 展示值保留两位小数：文本与 JSON 都按两位显示，避免
                # 「先舍入到一位、再按两位格式化」造成 67.70% 这类偏差。
                "percentile_display": (
                    None if point.percentile is None else round(point.percentile, 2)
                ),
                "week_complete": week_complete,
                "note": (
                    "最新观察点所在自然周尚未结束：周度规则要到该周最后一个交易日收盘后才最终确定"
                    if not week_complete
                    else "该观察点是所在 ISO 周最后一个已收盘交易日"
                ),
            },
            "reason": point.reason,
        }
    )
    return base


__all__ = [
    "BACKTEST_JSON",
    "BACKTEST_REPORT",
    "MODEL_ID",
    "build_candidate_a_signal",
    "classify_signal",
    "load_backtest_summary",
]
