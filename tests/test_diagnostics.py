"""事后诊断的算法验证（对应 J2 / J3 / J4）。

全部为手算可核对的小样本：
- 分位复用画像函数，最新观测日与画像输出一致；
- 前瞻收益的起止点、未到期排除、敏感性起点；
- 分组阈值的边界归属、独立段计数、统计量。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.diagnostics import (
    FROZEN_THRESHOLDS,
    GROUP_LABELS,
    GROUPS,
    bucket_flags,
    build_index_diagnostics,
    calendar_month_end,
    count_segments,
    forward_price_return,
    last_price_on_or_before,
    load_price_points,
    percentile_at,
    quantile,
    summarize_returns,
)
from engine.series.models import (
    FREQ_DAILY,
    FREQ_MONTHLY_REFERENCE,
    METRIC_PB,
    METRIC_PE_ROLLING,
    METRIC_PRICE_INDEX_CLOSE,
    IndexSeries,
    SeriesSource,
    SeriesStore,
)

ROOT = Path(__file__).resolve().parent.parent


def _source() -> SeriesSource:
    return SeriesSource(
        source_id="test.vendor",
        publisher="测试来源",
        interface="test",
        url=None,
        retrieved_at="2026-11-11T23:00:00+08:00",
    )


def _months(start: str, count: int, *, first_value: float = 1.0) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    year, month = (int(x) for x in start.split("-"))
    for i in range(count):
        total = year * 12 + (month - 1) + i
        y, m = total // 12, total % 12 + 1
        out.append((f"{y:04d}-{m:02d}-28", first_value + i))
    return out


def _price_points(start: str, days: int, *, base: float = 100.0, step: float = 1.0):
    """每个自然日一条（含周末也没关系，函数只按日期比较）。"""
    from datetime import date, timedelta

    begin = date.fromisoformat(start)
    return tuple(
        type("P", (), {"date": (begin + timedelta(days=i)).isoformat(), "close": base + i * step})
        for i in range(days)
    )


# --- 分位复用（J2）----------------------------------------------------------
def test_percentile_at_latest_matches_profile_output():
    store = SeriesStore(ROOT / "data" / "series")
    for code, expected_pe in (("000300", 75.833333), ("000905", 75.0), ("000852", 65.0)):
        series = store.find(
            f"INDEX:{code}.SH", METRIC_PE_ROLLING, frequency=FREQ_MONTHLY_REFERENCE
        )
        assert series is not None
        value, window = percentile_at(series, "2026-11-10", require_positive=True)
        assert value == pytest.approx(expected_pe, abs=1e-5), code
        assert window["pool"]["size"] == 60

    pb = store.find("INDEX:000300.SH", METRIC_PB, frequency=FREQ_MONTHLY_REFERENCE)
    value, _ = percentile_at(pb, "2026-11-10", require_positive=False)
    assert value == pytest.approx(39.166667, abs=1e-5)


def test_percentile_at_uses_only_data_up_to_that_date():
    series = IndexSeries(
        instrument_id="INDEX:000300.SH",
        metric="pe_rolling",
        field="滚动市盈率",
        frequency=FREQ_MONTHLY_REFERENCE,
        source=_source(),
        observations=tuple(_months("2021-11", 60)) + (("2026-11-10", 0.5),),
    )
    at_nov, _ = percentile_at(series, "2026-11-10", require_positive=True)
    at_oct, _ = percentile_at(series, "2026-10-28", require_positive=True)
    assert at_nov is not None and at_oct is not None
    # 10-28 那天还看不到 11-10 的观测：两者比较值不同，分位必须不同
    assert at_nov != at_oct


def test_percentile_below_coverage_threshold_is_none():
    series = IndexSeries(
        instrument_id="INDEX:000300.SH",
        metric="pe_rolling",
        field="滚动市盈率",
        frequency=FREQ_MONTHLY_REFERENCE,
        source=_source(),
        observations=tuple(_months("2024-01", 20)),
    )
    value, window = percentile_at(series, "2025-08-28", require_positive=True)
    assert value is None
    assert window["sufficient"] is False


# --- 价格对齐与前瞻收益（J3）------------------------------------------------
def test_last_price_on_or_before_picks_previous_trading_day():
    points = load_price_points(
        IndexSeries(
            instrument_id="INDEX:000300.SH",
            metric="price_index_close",
            field="close",
            frequency=FREQ_DAILY,
            source=_source(),
            observations=(("2026-01-05", 10.0), ("2026-01-07", 11.0), ("2026-01-09", 12.0)),
        )
    )
    assert last_price_on_or_before(points, "2026-01-06").date == "2026-01-05"
    assert last_price_on_or_before(points, "2026-01-07").date == "2026-01-07"
    assert last_price_on_or_before(points, "2026-01-01") is None


def test_calendar_month_end_alignment():
    assert calendar_month_end("2026-01-15", 3) == "2026-04-30"
    assert calendar_month_end("2026-11-10", 3) == "2027-02-28"
    assert calendar_month_end("2024-11-30", 12) == "2025-11-30"
    assert calendar_month_end("2026-12-31", 1) == "2027-01-31"


def test_forward_return_hand_calculation():
    points = load_price_points(
        IndexSeries(
            instrument_id="INDEX:000300.SH",
            metric="price_index_close",
            field="close",
            frequency=FREQ_DAILY,
            source=_source(),
            observations=(
                ("2025-01-10", 100.0),
                ("2025-01-13", 101.0),
                ("2025-04-29", 110.0),
                ("2025-04-30", 111.0),
                ("2025-05-06", 120.0),
            ),
        )
    )
    result = forward_price_return(points, signal_date="2025-01-11", horizon_months=3)
    assert result.matured is True
    # 起点：不晚于 01-11 的最后一个交易日 = 01-10（100）
    assert result.start_date == "2025-01-10" and result.start_price == 100.0
    # 终点：不晚于 01-11 + 3 个月 = 04-11 的最后一个交易日。数据里 04-11 之前最大是 01-13
    # → 说明终点必须按「目标月末」而不是「加 3 个月的同一天」来对齐
    assert result.reason is None
    assert result.end_date == "2025-04-30" or result.end_date == "2025-04-29"


def test_forward_return_endpoint_is_month_end_not_same_day():
    points = load_price_points(
        IndexSeries(
            instrument_id="INDEX:000300.SH",
            metric="price_index_close",
            field="close",
            frequency=FREQ_DAILY,
            source=_source(),
            observations=(
                ("2025-01-10", 100.0),
                ("2025-04-15", 105.0),
                ("2025-04-30", 120.0),
            ),
        )
    )
    result = forward_price_return(points, signal_date="2025-01-10", horizon_months=3)
    # 2025-01-10 + 3 个日历月 → 2025-04-30 为月末
    assert result.end_date == "2025-04-30"
    assert result.return_pct == pytest.approx(20.0)


def test_forward_return_not_matured_is_excluded():
    points = load_price_points(
        IndexSeries(
            instrument_id="INDEX:000300.SH",
            metric="price_index_close",
            field="close",
            frequency=FREQ_DAILY,
            source=_source(),
            observations=(("2026-10-30", 100.0), ("2026-11-10", 110.0)),
        )
    )
    result = forward_price_return(points, signal_date="2026-10-30", horizon_months=3)
    assert result.matured is False
    assert result.reason == "not_matured"
    assert result.return_pct is None
    assert result.start_date == "2026-10-30"


def test_forward_return_without_start_price_is_reported():
    points = load_price_points(
        IndexSeries(
            instrument_id="INDEX:000300.SH",
            metric="price_index_close",
            field="close",
            frequency=FREQ_DAILY,
            source=_source(),
            observations=(("2026-06-01", 100.0),),
        )
    )
    result = forward_price_return(points, signal_date="2025-01-10", horizon_months=3)
    assert result.matured is False
    assert result.reason == "no_price_at_start"


def test_sensitivity_start_shifts_to_next_observation_date():
    points = load_price_points(
        IndexSeries(
            instrument_id="INDEX:000300.SH",
            metric="price_index_close",
            field="close",
            frequency=FREQ_DAILY,
            source=_source(),
            observations=(
                ("2025-01-10", 100.0),
                ("2025-02-14", 90.0),
                ("2025-04-30", 108.0),
            ),
        )
    )
    base = forward_price_return(points, signal_date="2025-01-10", horizon_months=3)
    shifted = forward_price_return(
        points, signal_date="2025-01-10", horizon_months=3, start_on="2025-02-14"
    )
    assert base.start_date == "2025-01-10"
    assert shifted.start_date == "2025-02-14"
    assert base.end_date == shifted.end_date, "终点不随起点移动"
    assert shifted.return_pct == pytest.approx((108 / 90 - 1) * 100)


# --- 分组与统计（J4）--------------------------------------------------------
def test_thresholds_are_frozen():
    assert FROZEN_THRESHOLDS == {
        "pe_low_max": 20.0,
        "pe_high_min": 60.0,
        "pb_low_max": 40.0,
        "pb_high_min": 70.0,
    }


def test_bucket_boundaries_belong_to_low_and_high():
    low = bucket_flags(20.0, 40.0)
    assert low["pe_low"] and not low["pe_mid"] and not low["pe_high"]
    assert low["pb_low"] and not low["pb_mid"] and not low["pb_high"]
    assert low["b_entry"] is True

    high = bucket_flags(60.0, 70.0)
    assert high["pe_high"] and not high["pe_mid"] and not high["pe_low"]
    assert high["pb_high"] and not high["pb_mid"] and not high["pb_low"]
    assert high["b_exit"] is True

    mid = bucket_flags(40.0, 55.0)
    assert mid["pe_mid"] and not mid["pe_low"] and not mid["pe_high"]
    assert mid["pb_mid"] and not mid["pb_low"] and not mid["pb_high"]
    assert mid["b_entry"] is False and mid["b_exit"] is False
    assert mid["baseline"] is True


def test_b_entry_and_exit_require_both_metrics():
    only_pe = bucket_flags(10.0, None)
    assert "b_entry" not in only_pe and "baseline" not in only_pe
    assert "pe_low" in only_pe
    only_pb = bucket_flags(None, 10.0)
    assert "b_entry" not in only_pb and "baseline" not in only_pb
    assert "pb_low" in only_pb
    assert bucket_flags(None, None) == {}


def test_count_segments_counts_runs():
    assert count_segments([True, True, False, True, False, False, True]) == 3
    assert count_segments([False, False]) == 0
    assert count_segments([True, True, True]) == 1


def test_quantile_linear_interpolation():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert quantile(values, 0.25) == pytest.approx(2.0)
    assert quantile(values, 0.5) == pytest.approx(3.0)
    assert quantile(values, 0.75) == pytest.approx(4.0)
    assert quantile([10.0], 0.25) == pytest.approx(10.0)
    assert quantile([], 0.25) is None


def test_summarize_returns_fields():
    from engine.diagnostics import ForwardReturn

    samples = [
        ForwardReturn("2025-01-10", 100.0, "2025-04-30", 110.0, 10.0, True, None),
        ForwardReturn("2025-02-28", 100.0, "2025-05-30", 95.0, -5.0, True, None),
        ForwardReturn("2026-10-30", 100.0, None, None, None, False, "not_matured"),
    ]
    stats = summarize_returns(samples)
    assert stats["matured"] == 2
    assert stats["not_matured"] == 1
    assert stats["median"] == pytest.approx(2.5)
    assert stats["min"] == pytest.approx(-5.0)
    assert stats["max"] == pytest.approx(10.0)
    assert stats["positive_ratio"] == pytest.approx(0.5)
    assert stats["start_date_min"] == "2025-01-10"
    assert stats["start_date_max"] == "2025-02-28"


# --- 端到端 -----------------------------------------------------------------
def test_diagnostics_end_to_end_on_real_series():
    store = SeriesStore(ROOT / "data" / "series")
    entry = build_index_diagnostics(
        instrument_id="INDEX:000300.SH",
        pe_series=store.find("INDEX:000300.SH", METRIC_PE_ROLLING, frequency=FREQ_MONTHLY_REFERENCE),
        pb_series=store.find("INDEX:000300.SH", METRIC_PB, frequency=FREQ_MONTHLY_REFERENCE),
        price_series=store.find("INDEX:000300.SH", METRIC_PRICE_INDEX_CLOSE, frequency=FREQ_DAILY),
    )
    assert entry["evaluable_months"] > 100
    assert entry["not_evaluable_months"] > 0, "早期月份不足 60 个月覆盖，必须计入不可评估"
    assert set(entry["groups"]) == set(GROUPS)
    for name in GROUPS:
        block = entry["groups"][name]
        assert block["label"] == GROUP_LABELS[name]
        for h in ("3", "6", "12"):
            stats = block["horizons"][h]
            assert stats["matured"] + stats["not_matured"] == block["months"]
    baseline = entry["groups"]["baseline"]
    assert baseline["months"] == entry["evaluable_months"]
    assert baseline["segments"] == 1
    assert all("groups" in sample and "returns" in sample for sample in entry["samples"])


def test_diagnostics_latest_sample_matches_profile_json():
    payload = json.loads(
        (ROOT / "data" / "diagnostics" / "ex-post-diagnostics.json").read_text(encoding="utf-8")
    )
    assert payload["return_basis"].startswith("price_index_close")
    assert payload["thresholds"] == FROZEN_THRESHOLDS
    for code, expected in (("000300", 75.833333), ("000905", 75.0), ("000852", 65.0)):
        entry = payload["indices"][code]
        last = entry["samples"][-1]
        assert last["t"] == "2026-11-10"
        assert last["pe_percentile"] == pytest.approx(expected, abs=1e-5)
        assert entry["groups"]["baseline"]["months"] == entry["evaluable_months"]


def test_report_avoid_conclusory_wording():
    text = (ROOT / "docs" / "archive" / "phase3b-diagnostics.md").read_text(encoding="utf-8")
    for banned in ("胜率", "显著", "建议", "有效"):
        assert banned not in text, f"报告不应出现结论性措辞：{banned}"
    assert "不含分红" in text
    assert "事后" in text
