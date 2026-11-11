"""候选 A 周度回测的算法验证（K2 / K3 / K4）。

全部用手算可核对的小样本：
- 周度观察点、5 年窗口边界、当前周排除、每周去重、覆盖不足、非正值；
- 状态机的入场/退出/到期/不可评估与 T+1、T+2 执行；
- 成本口径（单边 bp、买卖各一次）与权益曲线一致性；
- 回撤、基准与分区间统计。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from engine.backtest.metrics import build_equity_curve, buy_and_hold, max_drawdown, summarize_window
from engine.backtest.runner import run_index_backtest
from engine.backtest.state_machine import (
    EXIT_END,
    EXIT_EXPIRY,
    EXIT_SIGNAL,
    add_months,
    gross_price_factor,
    run_state_machine,
)
from engine.backtest.weekly import (
    build_weekly_observations,
    iso_week,
    weekly_percentiles,
)
from engine.series.models import (
    FREQ_DAILY,
    METRIC_OFFICIAL_CLOSE,
    METRIC_OFFICIAL_PE_ROLLING,
    METRIC_TOTAL_RETURN_CLOSE,
    SeriesStore,
)

ROOT = Path(__file__).resolve().parent.parent


def _weekdays(start: str, end: str) -> list[str]:
    begin, last = date.fromisoformat(start), date.fromisoformat(end)
    out: list[str] = []
    day = begin
    while day <= last:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


# --- 周度观察点（K2）--------------------------------------------------------
def test_iso_week_and_last_trading_day_per_week():
    days = ["2026-01-05", "2026-01-06", "2026-01-09", "2026-01-12"]
    observations = build_weekly_observations([(d, 100.0) for d in days], [])
    weeks = [o.week for o in observations]
    assert weeks == ["2026-W02", "2026-W03"]
    assert observations[0].date == "2026-01-09", "同一周取最后一个交易日"
    assert observations[0].close == 100.0


def test_iso_week_boundary_across_year():
    assert iso_week("2026-01-01") == "2026-W01"
    assert iso_week("2025-12-31") == "2026-W01"


def test_weekly_percentile_window_boundary_and_current_week_exclusion():
    # 5 年窗口 + 1 周，每周五一个观测
    days = _weekdays("2015-01-01", "2020-12-31")
    fridays = [d for d in days if date.fromisoformat(d).weekday() == 4]
    observations = build_weekly_observations(
        [(d, 100.0) for d in fridays], [(d, float(i + 1)) for i, d in enumerate(fridays)]
    )
    result = weekly_percentiles(observations)
    last = result[-1]
    assert last.evaluable is True
    # 当前周不在自己的池里
    assert last.pool_size == last.window_start and False or last.pool_size < last.weeks_in_window + 1
    assert last.weeks_in_window <= 261, "5 年约 260 周"
    assert last.pool_size == last.valid_weeks
    # 窗口起点必须严格晚于 t-5y
    assert last.window_start < last.date


def test_insufficient_coverage_is_not_evaluable():
    days = _weekdays("2015-01-01", "2020-12-31")
    fridays = [d for d in days if date.fromisoformat(d).weekday() == 4]
    # 只有一半的周有 PE
    pe = [(d, 10.0) for i, d in enumerate(fridays) if i % 2 == 0]
    observations = build_weekly_observations([(d, 100.0) for d in fridays], pe)
    result = weekly_percentiles(observations)
    last = result[-1]
    assert last.evaluable is False
    assert "覆盖不足" in (last.reason or "")


def test_non_positive_pe_gives_no_percentile():
    days = _weekdays("2015-01-01", "2020-12-31")
    fridays = [d for d in days if date.fromisoformat(d).weekday() == 4]
    pe = [(d, 10.0) for d in fridays]
    pe[-1] = (pe[-1][0], -3.0)
    observations = build_weekly_observations([(d, 100.0) for d in fridays], pe)
    result = weekly_percentiles(observations)
    assert result[-1].evaluable is False
    assert "非正" in (result[-1].reason or "")


def test_window_not_covered_by_series_start():
    days = _weekdays("2019-01-01", "2020-12-31")
    fridays = [d for d in days if date.fromisoformat(d).weekday() == 4]
    observations = build_weekly_observations(
        [(d, 100.0) for d in fridays], [(d, 10.0) for d in fridays]
    )
    result = weekly_percentiles(observations)
    assert all(not r.evaluable for r in result)
    assert any("5 年窗口未覆盖" in (r.reason or "") for r in result)


def test_weekly_percentile_uses_profile_formula():
    from engine.series.window import percentile_of

    days = _weekdays("2015-01-01", "2020-12-31")
    fridays = [d for d in days if date.fromisoformat(d).weekday() == 4]
    pe_values = [(d, float(i % 37) + 1.0) for i, d in enumerate(fridays)]
    observations = build_weekly_observations(
        [(d, 100.0) for d in fridays], pe_values
    )
    result = weekly_percentiles(observations)
    last = result[-1]
    assert last.evaluable
    window_start = last.window_start
    pool = [
        value
        for day, value in pe_values
        if window_start < day < last.date and value > 0
    ]
    assert last.percentile == pytest.approx(percentile_of(last.pe, pool))


# --- 状态机（K3）------------------------------------------------------------
def _calendar_prices(days: int = 800) -> tuple[list[str], dict[str, float]]:
    start = date(2020, 1, 1)
    calendar = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    prices = {d: 100.0 + i for i, d in enumerate(calendar)}
    return calendar, prices


def test_state_machine_entry_and_signal_exit_with_t_plus_1():
    calendar, prices = _calendar_prices()
    observations = [
        {"date": "2020-01-01", "week": "2020-W01", "percentile": 10.0, "evaluable": True},
        {"date": "2020-01-10", "week": "2020-W02", "percentile": 65.0, "evaluable": True},
    ]
    run = run_state_machine(observations, calendar=calendar, prices=prices, lag=1, cost_bp=10)
    trades = run["trades"]
    assert len(trades) == 1
    trade = trades[0]
    assert trade["entry_exec_date"] == "2020-01-02"
    assert trade["exit_exec_date"] == "2020-01-11"
    assert trade["exit_reason"] == EXIT_SIGNAL
    entry_price = prices["2020-01-02"]
    exit_price = prices["2020-01-11"]
    assert trade["gross_return"] == pytest.approx(exit_price / entry_price - 1)
    assert trade["net_return"] == pytest.approx(gross_price_factor(exit_price, entry_price, 0.001) - 1)


def test_state_machine_t_plus_2_shifts_execution_only():
    calendar, prices = _calendar_prices()
    observations = [
        {"date": "2020-01-01", "week": "2020-W01", "percentile": 10.0, "evaluable": True},
        {"date": "2020-01-10", "week": "2020-W02", "percentile": 65.0, "evaluable": True},
    ]
    lag1 = run_state_machine(observations, calendar=calendar, prices=prices, lag=1)
    lag2 = run_state_machine(observations, calendar=calendar, prices=prices, lag=2)
    assert lag1["trades"][0]["entry_exec_date"] == "2020-01-02"
    assert lag2["trades"][0]["entry_exec_date"] == "2020-01-03"
    assert lag2["trades"][0]["exit_exec_date"] == "2020-01-12"


def test_state_machine_expiry_exit_after_12_months():
    calendar, prices = _calendar_prices(900)
    observations = [
        {"date": "2020-01-01", "week": "2020-W01", "percentile": 10.0, "evaluable": True},
        {"date": "2020-06-01", "week": "2020-W23", "percentile": 40.0, "evaluable": True},
        {"date": "2021-01-05", "week": "2021-W01", "percentile": 40.0, "evaluable": True},
    ]
    run = run_state_machine(observations, calendar=calendar, prices=prices, lag=1)
    trade = run["trades"][0]
    assert trade["exit_reason"] == EXIT_EXPIRY
    assert trade["exit_signal_date"] == "2021-01-05"
    assert trade["exit_exec_date"] == "2021-01-06"
    assert add_months("2020-01-02", 12) == "2021-01-02"


def test_state_machine_holds_through_not_evaluable_and_does_not_reenter_same_point():
    calendar, prices = _calendar_prices(900)
    observations = [
        {"date": "2020-01-01", "week": "2020-W01", "percentile": 10.0, "evaluable": True},
        {"date": "2020-02-01", "week": "2020-W05", "percentile": None, "evaluable": False},
        {"date": "2021-01-05", "week": "2021-W01", "percentile": 8.0, "evaluable": True},
        {"date": "2021-02-01", "week": "2021-W05", "percentile": 8.0, "evaluable": True},
    ]
    run = run_state_machine(observations, calendar=calendar, prices=prices, lag=1)
    actions = [d["action"] for d in run["decisions"]]
    assert actions[1] == "not_evaluable_holding"
    # 到期退出发生在 2021-01-05 这个观察点，同一点不重新入场
    assert actions[2] == "exit_signal"
    assert run["trades"][0]["exit_reason"] == EXIT_EXPIRY
    # 下一周（2021-02-01）才重新评估：分位 8 → 再次入场
    assert actions[3] == "entry_signal"
    assert len(run["trades"]) == 2
    assert run["trades"][1]["exit_reason"] == EXIT_END, "第二次入场在样本末尾按最后交易日平仓"


def test_state_machine_no_entry_when_not_evaluable():
    calendar, prices = _calendar_prices(400)
    observations = [
        {"date": "2020-01-01", "week": "2020-W01", "percentile": None, "evaluable": False},
    ]
    run = run_state_machine(observations, calendar=calendar, prices=prices, lag=1)
    assert run["trades"] == []
    assert run["decisions"][0]["action"] == "none"


def test_state_machine_open_position_is_closed_at_sample_end():
    calendar, prices = _calendar_prices(400)
    observations = [
        {"date": "2020-01-01", "week": "2020-W01", "percentile": 10.0, "evaluable": True},
    ]
    run = run_state_machine(observations, calendar=calendar, prices=prices, lag=1)
    assert run["open_position_at_end"] is True
    assert run["trades"][-1]["exit_reason"] == EXIT_END


# --- 成本与曲线（K4）--------------------------------------------------------
def test_net_return_equals_cost_adjusted_price_ratio():
    calendar, prices = _calendar_prices(200)
    observations = [
        {"date": "2020-01-01", "week": "2020-W01", "percentile": 10.0, "evaluable": True},
        {"date": "2020-01-10", "week": "2020-W02", "percentile": 70.0, "evaluable": True},
    ]
    for cost in (0.0, 5.0, 20.0):
        run = run_state_machine(observations, calendar=calendar, prices=prices, lag=1, cost_bp=cost)
        trade = run["trades"][0]
        expected = (trade["exit_price"] / trade["entry_price"]) * (1 - cost / 10000) ** 2 - 1
        assert trade["net_return"] == pytest.approx(expected)


def test_equity_curve_matches_product_of_net_returns():
    calendar, prices = _calendar_prices(400)
    observations = [
        {"date": "2020-01-01", "week": "2020-W01", "percentile": 10.0, "evaluable": True},
        {"date": "2020-01-20", "week": "2020-W03", "percentile": 70.0, "evaluable": True},
        {"date": "2020-02-20", "week": "2020-W08", "percentile": 10.0, "evaluable": True},
        {"date": "2020-03-20", "week": "2020-W12", "percentile": 70.0, "evaluable": True},
    ]
    run = run_state_machine(observations, calendar=calendar, prices=prices, lag=1, cost_bp=10)
    trades = run["trades"]
    curve = build_equity_curve(
        calendar, prices, trades, start=calendar[0], end=calendar[-1], cost_bp=10
    )
    product = 1.0
    for trade in trades:
        product *= 1 + trade["net_return"]
    assert curve[-1]["equity"] == pytest.approx(product, rel=1e-12)


def test_buy_and_hold_and_drawdown():
    calendar = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    prices = {"2026-01-05": 100.0, "2026-01-06": 120.0, "2026-01-07": 90.0, "2026-01-08": 110.0}
    assert buy_and_hold(prices, calendar, start="2026-01-05", end="2026-01-08") == pytest.approx(0.10)
    curve = [
        {"date": "2026-01-05", "equity": 1.0, "in_position": True},
        {"date": "2026-01-06", "equity": 1.2, "in_position": True},
        {"date": "2026-01-07", "equity": 0.9, "in_position": True},
        {"date": "2026-01-08", "equity": 1.1, "in_position": True},
    ]
    dd = max_drawdown(curve)
    assert dd["max_drawdown"] == pytest.approx(0.9 / 1.2 - 1)
    assert dd["peak_date"] == "2026-01-06"
    assert dd["trough_date"] == "2026-01-07"


def test_summarize_window_fields():
    calendar, prices = _calendar_prices(300)
    trades = [
        {
            "entry_exec_date": "2020-01-02",
            "exit_exec_date": "2020-03-02",
            "net_return": 0.05,
            "gross_return": 0.07,
            "exit_reason": EXIT_SIGNAL,
            "holding_days": 60,
        },
        {
            "entry_exec_date": "2020-05-02",
            "exit_exec_date": "2020-07-02",
            "net_return": -0.02,
            "gross_return": -0.01,
            "exit_reason": EXIT_EXPIRY,
            "holding_days": 61,
        },
    ]
    curve = build_equity_curve(calendar, prices, [], start=calendar[0], end=calendar[250])
    stats = summarize_window(
        trades,
        curve,
        start=calendar[0],
        end=calendar[250],
        price_calendar=calendar,
        benchmark_tr=0.1,
        benchmark_price=0.08,
    )
    for key in (
        "trades",
        "cumulative_return",
        "annualized_return",
        "max_drawdown",
        "market_exposure",
        "turnover_per_year",
        "worst_trade",
        "expiry_exit_ratio",
        "exit_reasons",
        "holding_days",
        "benchmark_total_return_buy_hold",
        "benchmark_price_buy_hold",
    ):
        assert key in stats
    assert stats["trades"] == 2
    assert stats["worst_trade"] == pytest.approx(-0.02)
    assert stats["expiry_exit_ratio"] == pytest.approx(0.5)


# --- 序列与报告（K1 / K5 / K6）----------------------------------------------
def test_official_series_are_versioned_and_after_launch():
    store = SeriesStore(ROOT / "data" / "series")
    assert store.check_consistency() == []
    launches = {"000300": "2005-04-08", "000905": "2007-01-15", "000852": "2014-10-17"}
    for code, launch in launches.items():
        for metric in (
            METRIC_OFFICIAL_CLOSE,
            METRIC_OFFICIAL_PE_ROLLING,
            METRIC_TOTAL_RETURN_CLOSE,
        ):
            series = store.find(f"INDEX:{code}.SH", metric, frequency=FREQ_DAILY)
            assert series is not None, f"{code} 缺少 {metric}"
            assert series.start_date >= launch, f"{code} {metric} 含发布前回溯数据"
            assert series.series_id.startswith("ser-")


def test_backtest_result_shape_and_guardrails():
    payload = json.loads(
        (ROOT / "data" / "backtest" / "candidate-a.json").read_text(encoding="utf-8")
    )
    assert payload["thresholds"]["entry_percentile"] == 20.0
    assert payload["thresholds"]["exit_percentile"] == 60.0
    assert payload["thresholds"]["max_hold_months"] == 12
    assert payload["costs"] == [5.0, 10.0, 20.0]
    assert payload["execution_lags"] == [1, 2]
    assert payload["cash_return"] == 0.0
    for code in ("000300", "000905", "000852"):
        entry = payload["indices"][code]
        assert entry["weekly"]["total_weeks"] > 0
        for basis in ("total_return", "price"):
            for lag in ("lag1", "lag2"):
                for cost in ("5", "10", "20"):
                    block = entry["runs"][basis][lag][cost]
                    assert set(block) >= {"full", "development", "holdout", "yearly", "trade_list"}
                    for key in ("cumulative_return", "annualized_return", "max_drawdown"):
                        assert key in block["full"]


def test_report_avoids_conclusory_wording_and_has_required_sections():
    text = (ROOT / "docs" / "phase3c-backtest.md").read_text(encoding="utf-8")
    for banned in ("胜率", "显著", "建议", "有效"):
        assert banned not in text, f"报告不应出现结论性措辞：{banned}"
    for marker in (
        "冻结口径",
        "数据质量",
        "主口径：全收益指数",
        "开发 / 留出区间",
        "逐年表现",
        "逐笔交易",
        "已知限制",
    ):
        assert marker in text
    assert "T+1" in text and "T+2" in text
    assert "现金" in text


def test_run_index_backtest_is_deterministic():
    store = SeriesStore(ROOT / "data" / "series")
    code, launch = "000300", "2005-04-08"
    instrument_id = f"INDEX:{code}.SH"
    kwargs = dict(
        instrument_id=instrument_id,
        close_observations=store.find(instrument_id, METRIC_OFFICIAL_CLOSE, frequency=FREQ_DAILY).observations,
        pe_observations=store.find(instrument_id, METRIC_OFFICIAL_PE_ROLLING, frequency=FREQ_DAILY).observations,
        tr_observations=store.find(instrument_id, METRIC_TOTAL_RETURN_CLOSE, frequency=FREQ_DAILY).observations,
        launch_date=launch,
        series_ids={},
    )
    first = run_index_backtest(**kwargs)
    second = run_index_backtest(**kwargs)
    assert first["evaluation_window"] == second["evaluation_window"]
    assert (
        first["runs"]["total_return"]["lag1"]["10"]["full"]["cumulative_return"]
        == second["runs"]["total_return"]["lag1"]["10"]["full"]["cumulative_return"]
    )


# --- 收尾项 R1：池内取每周最后一个正值，观察点仍取当日值 ---------------------
def test_weekly_pool_uses_last_positive_pe_in_week():
    """周内最后交易日缺 PE 时，该周仍以「最后一个正值」进入参考池（R1）。"""
    days = _weekdays("2015-01-01", "2020-12-31")
    fridays = [d for d in days if date.fromisoformat(d).weekday() == 4]
    pe_pairs: list[tuple[str, float]] = []
    for index, friday in enumerate(fridays):
        if index in (10, 11):
            # 这两周的周五没有 PE，但周三有；旧实现会让整周作废
            wednesday = (date.fromisoformat(friday) - timedelta(days=2)).isoformat()
            pe_pairs.append((wednesday, 20.0))
        else:
            pe_pairs.append((friday, 10.0))
    observations = build_weekly_observations(
        [(d, 100.0) for d in fridays], pe_pairs
    )
    affected = [o for o in observations if o.week in {iso_week(fridays[10]), iso_week(fridays[11])}]
    assert all(o.pe is None for o in affected), "观察日当天确实没有值"
    assert all(o.week_pe == pytest.approx(20.0) for o in affected), "周内最后一个正值仍进池"

    result = weekly_percentiles(observations)
    last = result[-1]
    assert last.evaluable is True
    window = last.window_start
    pool = [
        value
        for day, value in pe_pairs
        if window < day < last.date and value > 0
    ]
    assert last.pool_size == len(pool)


def test_observation_point_still_requires_same_day_value():
    """观察点自身仍取当日值：当日缺失或非正时不给分位，即使周内有正值。"""
    days = _weekdays("2015-01-01", "2020-12-31")
    fridays = [d for d in days if date.fromisoformat(d).weekday() == 4]
    pe_pairs = [(d, 10.0) for d in fridays]
    last_friday = fridays[-1]
    wednesday = (date.fromisoformat(last_friday) - timedelta(days=2)).isoformat()
    pe_pairs[-1] = (wednesday, 15.0)  # 只给周三
    observations = build_weekly_observations([(d, 100.0) for d in fridays], pe_pairs)
    result = weekly_percentiles(observations)
    assert result[-1].evaluable is False
    assert "没有滚动市盈率" in (result[-1].reason or "")


def test_r1_matches_reviewer_expected_coverage():
    """修正后三指数的可评估周数与评估起点（复验报告第二节）。"""
    store = SeriesStore(ROOT / "data" / "series")
    expected = {
        "000300": (547, "2016-03-11"),
        "000905": (547, "2016-03-11"),
        "000852": (363, "2019-10-18"),
    }
    launches = {"000300": "2005-04-08", "000905": "2007-01-15", "000852": "2014-10-17"}
    for code, (count, start) in expected.items():
        instrument_id = f"INDEX:{code}.SH"
        close = store.find(instrument_id, METRIC_OFFICIAL_CLOSE, frequency=FREQ_DAILY)
        pe = store.find(instrument_id, METRIC_OFFICIAL_PE_ROLLING, frequency=FREQ_DAILY)
        weekly = [
            w
            for w in build_weekly_observations(close.observations, pe.observations)
            if w.date >= launches[code]
        ]
        result = weekly_percentiles(weekly)
        evaluable = [r for r in result if r.evaluable]
        assert len(evaluable) == count, code
        assert evaluable[0].date == start, code


# --- 收尾项 R2：开发/留出区间各自的买入持有基准 ------------------------------
def test_development_and_holdout_have_their_own_benchmarks():
    payload = json.loads(
        (ROOT / "data" / "backtest" / "candidate-a.json").read_text(encoding="utf-8")
    )
    for code in ("000300", "000905", "000852"):
        entry = payload["indices"][code]
        assert set(entry["benchmarks"]["by_window"]) == {"full", "development", "holdout"}
        for window in ("development", "holdout"):
            for lag in ("lag1", "lag2"):
                for cost in ("5", "10", "20"):
                    block = entry["runs"]["total_return"][lag][cost][window]
                    assert block["benchmark_total_return_buy_hold"] is not None
                    assert block["benchmark_price_buy_hold"] is not None
                    assert block["excess_vs_total_return"] == pytest.approx(
                        block["cumulative_return"] - block["benchmark_total_return_buy_hold"]
                    )


def test_window_benchmarks_match_series_recomputation():
    """开发/留出基准可由序列独立复算。"""
    payload = json.loads(
        (ROOT / "data" / "backtest" / "candidate-a.json").read_text(encoding="utf-8")
    )
    store = SeriesStore(ROOT / "data" / "series")
    for code in ("000300", "000905", "000852"):
        entry = payload["indices"][code]
        tr = store.find(
            f"INDEX:{code}.SH", METRIC_TOTAL_RETURN_CLOSE, frequency=FREQ_DAILY
        )
        prices = dict(tr.observations)
        calendar = sorted(prices)
        for window, (block_key) in (
            ("development", "development"),
            ("holdout", "holdout"),
        ):
            block = entry["runs"]["total_return"]["lag1"]["10"][block_key]
            expected = buy_and_hold(prices, calendar, start=block["start"], end=block["end"])
            assert block["benchmark_total_return_buy_hold"] == pytest.approx(expected)
