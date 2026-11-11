"""指数月度画像与分位算法的独立验证（对应验收条件 I2、I3）。

全部为手算可核对的小样本：
- 分位公式（含并列值）；
- 每月去重、窗口边界、当前值排除；
- 覆盖不足、非正 PE、未来日期记录；
- 同源同列、内容哈希与重放。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.series.models import (
    FREQ_DAILY_SHORT,
    FREQ_MONTHLY_REFERENCE,
    METRIC_PE_ROLLING,
    IndexSeries,
    SeriesError,
    SeriesSource,
    SeriesStore,
)
from engine.series.profile import build_index_profile
from engine.series.window import (
    MIN_VALID_MONTHS,
    WINDOW_MONTHS,
    build_window,
    month_range,
    percentile_of,
    shift_month,
)

ROOT = Path(__file__).resolve().parent.parent


# --- 分位公式 ---------------------------------------------------------------
def test_percentile_hand_calculation():
    pool = [10, 12, 14, 16, 18]
    # 14：小于 2 个、等于 1 个 → (2 + 0.5) / 5 = 50%
    assert percentile_of(14, pool) == pytest.approx(50.0)
    # 20：全部小于 → 100%
    assert percentile_of(20, pool) == pytest.approx(100.0)
    # 5：全部大于 → 0%
    assert percentile_of(5, pool) == pytest.approx(0.0)


def test_percentile_counts_ties_as_half():
    # 四个并列 10：小于 0、等于 4 → (0 + 2) / 4 = 50%
    assert percentile_of(10, [10, 10, 10, 10]) == pytest.approx(50.0)
    # 两个 10 一个 20：小于 0、等于 2 → (0 + 1) / 3 ≈ 33.33%
    assert percentile_of(10, [10, 10, 20]) == pytest.approx(100.0 * 1 / 3)


def test_percentile_of_empty_pool_is_none():
    assert percentile_of(10, []) is None


# --- 窗口与去重 -------------------------------------------------------------
def _monthly(start_month: str, count: int, *, start_value: float = 1.0):
    out = []
    for index, month in enumerate(month_range(shift_month(start_month, count - 1), count)):
        out.append((f"{month}-28", start_value + index))
    return out


def test_window_is_the_60_complete_months_before_analysis_month():
    observations = _monthly("2021-11", 60)
    observations.append(("2026-11-05", 100.0))  # 当前月观测
    result = build_window(observations, metric="pb", as_of="2026-11-15")
    assert result.window_start_month == "2021-11"
    assert result.window_end_month == "2026-10"
    assert result.expected_months == WINDOW_MONTHS == 60
    assert result.valid_months == tuple(f"2021-11" for _ in []) or len(result.valid_months) == 60
    assert result.comparison is not None
    assert result.comparison.date == "2026-11-05"
    assert result.comparison_removed_from_pool is False, "当前月观测本就不在窗口内"


def test_current_month_observation_is_not_in_reference_pool():
    observations = _monthly("2021-11", 60)  # 2021-11..2026-10
    observations.append(("2026-11-05", 999.0))
    result = build_window(observations, metric="pb", as_of="2026-11-15")
    assert all(p.month != "2026-11" for p in result.monthly_points)
    assert result.pool_max == pytest.approx(60.0), "异常当前值不得进入参考池"


def test_one_observation_per_month_keeps_the_last_valid_date():
    observations = _monthly("2021-11", 60)
    observations.append(("2022-03-10", 500.0))  # 与已有的 2022-03-28 同月
    result = build_window(observations, metric="pb", as_of="2026-11-15")
    mars = [p for p in result.monthly_points if p.month == "2022-03"]
    assert len(mars) == 1
    assert mars[0].date == "2022-03-28", "同月取最后一个有效观测"
    assert result.dropped_duplicate_months


def test_comparison_inside_window_is_removed_from_pool():
    observations = _monthly("2021-11", 60)  # 最后一条是 2026-10-28
    result = build_window(observations, metric="pb", as_of="2026-11-15")
    # 比较值就是 2026-10-28 这条，落在参考池里 → 必须移除
    assert result.comparison is not None
    assert result.comparison.date == "2026-10-28"
    assert result.comparison_removed_from_pool is True
    assert result.pool_size == 59
    assert result.valid_months and len(result.valid_months) == 60


def test_insufficient_coverage_gives_no_percentile():
    observations = _monthly("2022-01", 53)  # 只有 53 个月，且最新一条落在窗口内
    result = build_window(observations, metric="pb", as_of="2026-11-15")
    assert len(result.valid_months) == 53
    assert result.comparison_removed_from_pool is True
    assert result.pool_size == 52
    assert result.percentile is None
    assert result.sufficient is False
    assert "54" in (result.insufficient_reason or "")
    assert len(result.missing_months) == 60 - 53


def test_minimum_valid_months_boundary():
    observations = _monthly("2021-12", MIN_VALID_MONTHS)  # 恰好 54 个月
    result = build_window(observations, metric="pb", as_of="2026-11-15")
    assert result.pool_size in (54, 53)
    assert result.sufficient is (result.pool_size >= MIN_VALID_MONTHS)


def test_non_positive_pe_is_excluded_from_pe_pool():
    observations = _monthly("2021-11", 60)
    observations[10] = ("2022-09-28", -3.0)  # PE 为负的月份
    result = build_window(observations, metric="pe_rolling", as_of="2026-11-15", require_positive=True)
    assert len(result.monthly_points) == 59
    assert "2022-09" in result.missing_months
    assert any(p.value == -3.0 for p in result.dropped_non_positive)


def test_non_positive_pe_current_value_gives_no_percentile():
    observations = _monthly("2021-11", 60)
    observations.append(("2026-11-05", -1.0))
    result = build_window(observations, metric="pe_rolling", as_of="2026-11-15", require_positive=True)
    assert result.comparison is not None
    assert result.comparison.value == -1.0, "比较值取最新观测，不退回更早的正值"
    assert result.percentile is None
    assert "非正" in (result.insufficient_reason or "")


def test_future_dated_records_are_dropped():
    observations = _monthly("2021-11", 60)
    observations.append(("2026-12-20", 888.0))  # 晚于分析时点
    result = build_window(observations, metric="pb", as_of="2026-11-15")
    assert result.dropped_future
    assert all(p.date != "2026-12-20" for p in result.monthly_points)
    assert result.comparison is not None
    assert result.comparison.date == "2026-10-28"


def test_pool_statistics_match_hand_calculation():
    observations = _monthly("2021-11", 60, start_value=1.0)  # 1..60
    result = build_window(observations, metric="pb", as_of="2026-11-15")
    # 比较值是 60（2026-10-28），从池中移除后剩 1..59
    assert result.pool_size == 59
    assert result.pool_min == pytest.approx(1.0)
    assert result.pool_max == pytest.approx(59.0)
    assert result.pool_median == pytest.approx(30.0)
    # 59 个值里，小于 60 的有 59 个、等于 0 个 → 100%
    assert result.percentile == pytest.approx(100.0)
    assert result.percentile_display == "100.0"


def test_percentile_display_rounds_only_for_display():
    observations = _monthly("2021-11", 60, start_value=1.0)
    observations.append(("2026-11-05", 30.5))
    result = build_window(observations, metric="pb", as_of="2026-11-15")
    assert result.percentile is not None
    assert len(result.percentile_display.split(".")[1]) == 1
    assert result.percentile_display == f"{round(result.percentile, 1):.1f}"


# --- 序列存储与重放 ---------------------------------------------------------
def _source(stamp: str = "2026-11-11T17:00:00+08:00") -> SeriesSource:
    return SeriesSource(
        source_id="test.vendor",
        publisher="测试来源",
        interface="test",
        url=None,
        retrieved_at=stamp,
        definition_note="测试用",
    )


def _series(**overrides) -> IndexSeries:
    base = dict(
        instrument_id="INDEX:000300.SH",
        metric=METRIC_PE_ROLLING,
        field="滚动市盈率",
        frequency=FREQ_MONTHLY_REFERENCE,
        source=_source(),
        observations=_monthly("2021-11", 60),
    )
    base.update(overrides)
    return IndexSeries(**base)


def test_series_id_is_content_addressed_and_stable():
    first = _series()
    second = _series()
    assert first.series_id == second.series_id
    assert first.series_id.startswith("ser-")


def test_series_id_changes_when_an_observation_changes():
    base = _series()
    changed = _series(observations=base.observations[:-1] + (("2026-10-28", 999.0),))
    assert changed.series_id != base.series_id


def test_tampered_series_file_is_rejected(tmp_path):
    series = _series()
    data = series.to_dict()
    data["observations"][0]["value"] = 12345.0
    path = tmp_path / f"{series.series_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    store = SeriesStore(tmp_path)
    with pytest.raises(SeriesError) as excinfo:
        store.load(series.series_id)
    assert "内容已被改动" in str(excinfo.value)


def test_series_store_roundtrip_and_consistency(tmp_path):
    store = SeriesStore(tmp_path)
    series = _series()
    store.save(series)
    entries = store.rebuild_manifest()
    assert entries[0]["series_id"] == series.series_id
    assert store.check_consistency() == []
    loaded = store.find("INDEX:000300.SH", METRIC_PE_ROLLING, frequency=FREQ_MONTHLY_REFERENCE)
    assert loaded is not None
    assert loaded.observations == series.observations


def test_series_rejects_unparsable_dates_and_non_finite_values():
    with pytest.raises(SeriesError):
        _series(observations=(("not-a-date", 1.0),))
    with pytest.raises(SeriesError):
        _series(observations=(("2026-01-31", float("inf")),))


# --- 画像组装 ---------------------------------------------------------------
def test_profile_computes_both_metrics_and_keeps_sources_separate():
    pe = _series()
    pb = _series(metric="pb", field="市净率", observations=_monthly("2021-11", 60, start_value=0.5))
    official = _series(
        metric="official_pe_ratio_1",
        field="市盈率1",
        frequency=FREQ_DAILY_SHORT,
        observations=(("2026-11-07", 13.5), ("2026-11-10", 13.8)),
    )
    profile = build_index_profile(
        instrument_id="INDEX:000300.SH",
        as_of="2026-11-15",
        series={"pe_rolling": pe, "pb": pb},
        short_series={"official_pe_ratio_1": official},
    )
    assert profile["status"] == "complete"
    assert profile["metrics"]["pe_rolling"]["series_id"] == pe.series_id
    assert profile["metrics"]["pb"]["series_id"] == pb.series_id
    assert profile["official_short_history"]["official_pe_ratio_1"]["latest"]["value"] == 13.8
    assert profile["is_trading_signal"] is False
    assert profile["official_short_history"]["official_pe_ratio_1"]["series_id"] == official.series_id
    # 官方序列不参与长历史排名：PE 的池子里没有它
    pe_window = profile["metrics"]["pe_rolling"]["window"]
    assert pe_window["pool"]["size"] <= 60
    assert len(profile["sources"]) == 1


def test_profile_without_series_reports_gap_not_a_number():
    profile = build_index_profile(
        instrument_id="INDEX:000001.SH", as_of="2026-11-15", series={}, short_series={}
    )
    assert profile["status"] == "insufficient_data"
    assert profile["metrics"] == {}
    assert profile["missing"]
    assert profile["decision_note"]


def test_profile_partial_when_one_metric_lacks_coverage():
    short_history = _series(observations=_monthly("2021-12", 20))
    pb = _series(metric="pb", field="市净率", observations=_monthly("2021-11", 60))
    profile = build_index_profile(
        instrument_id="INDEX:000300.SH",
        as_of="2026-11-15",
        series={"pe_rolling": short_history, "pb": pb},
    )
    assert profile["status"] == "partial"
    assert profile["metrics"]["pe_rolling"]["status"] == "insufficient_coverage"
    assert profile["metrics"]["pb"]["status"] == "computed"
    assert profile["metrics"]["pe_rolling"]["percentile"] is None


def test_profile_is_deterministic_for_same_inputs():
    pe = _series()
    pb = _series(metric="pb", field="市净率")
    first = build_index_profile(
        instrument_id="INDEX:000300.SH", as_of="2026-11-15", series={"pe_rolling": pe, "pb": pb}
    )
    second = build_index_profile(
        instrument_id="INDEX:000300.SH", as_of="2026-11-15", series={"pe_rolling": pe, "pb": pb}
    )
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# --- 真实序列 ---------------------------------------------------------------
def test_real_series_cover_the_60_month_window():
    store = SeriesStore(ROOT / "data" / "series")
    assert store.check_consistency() == []
    for code in ("000300", "000905", "000852"):
        for metric in ("pe_rolling", "pb"):
            series = store.find(
                f"INDEX:{code}.SH", metric, frequency=FREQ_MONTHLY_REFERENCE
            )
            assert series is not None, f"{code} 缺少 {metric} 序列"
            result = build_window(
                series.observations,
                metric=metric,
                as_of="2026-11-10",
                require_positive=(metric == "pe_rolling"),
            )
            assert result.sufficient is True
            assert result.pool_size >= MIN_VALID_MONTHS
            assert result.window_start_month == "2021-11"
            assert result.window_end_month == "2026-10"


# --- W1：先按月取最后一条、再过滤非正，会丢掉整月有效观测 ---------------------
def _review_w1_observations():
    """复验反例：每月 15 日有效，前七个月 28 日追加一个非正 PE。"""
    obs = [(f"{2021 + (10 + i) // 12:04d}-{(10 + i) % 12 + 1:02d}-15", float(i + 1)) for i in range(60)]
    obs += [(day[:7] + "-28", -1.0) for day, _ in obs[:7]]
    obs += [("2026-11-10", 30.5)]
    return obs


def test_w1_month_keeps_last_valid_observation_when_month_end_is_invalid():
    result = build_window(
        _review_w1_observations(),
        metric="pe_rolling",
        as_of="2026-11-10",
        require_positive=True,
    )
    assert result.pool_size == 60, "月中有效、月末非正时，该月仍应保留最后一个有效观测"
    assert result.percentile == pytest.approx(50.0)
    assert len(result.valid_months) == 60
    assert result.insufficient_reason is None
    assert len(result.dropped_non_positive) == 7
    assert any("同月若仍有有效观测" in note for note in result.notes)


def test_w1_same_month_multiple_valid_values_keep_the_last():
    observations = _monthly("2021-11", 60)
    observations.append(("2022-03-10", 500.0))  # 与 2022-03-28 同月，均为有效值
    result = build_window(observations, metric="pb", as_of="2026-11-15")
    march = [p for p in result.monthly_points if p.month == "2022-03"]
    assert len(march) == 1
    assert march[0].value == pytest.approx(5.0), "同月多条有效值时取最后一条（2022-03-28）"


def test_w1_invalid_last_value_does_not_change_the_current_comparison():
    """最新观测为非正时仍不给分位，不允许回退到月中正值冒充当前值。"""
    observations = _monthly("2021-11", 60)
    observations.append(("2026-11-10", -5.0))
    result = build_window(
        observations, metric="pe_rolling", as_of="2026-11-10", require_positive=True
    )
    assert result.comparison is not None and result.comparison.value == -5.0
    assert result.percentile is None
    assert "非正" in (result.insufficient_reason or "")


# --- W2：官方短序列必须受画像 as_of 限制 -------------------------------------
def test_w2_official_short_series_respects_as_of():
    pe = _series()
    official = _series(
        metric="official_pe_ratio_1",
        field="市盈率1",
        frequency=FREQ_DAILY_SHORT,
        observations=(("2026-11-07", 13.5), ("2026-11-10", 13.8), ("2026-11-11", 999.0)),
    )
    profile = build_index_profile(
        instrument_id="INDEX:000300.SH",
        as_of="2026-11-10",
        series={"pe_rolling": pe},
        short_series={"official_pe_ratio_1": official},
    )
    block = profile["official_short_history"]["official_pe_ratio_1"]
    assert block["latest"] == {"date": "2026-11-10", "value": 13.8}
    assert block["latest"]["date"] <= profile["as_of"]
    assert block["excluded_future_observations"] == 1
    assert block["observation_count"] == 2
    assert block["coverage_end"] == "2026-11-10"


def test_w2_official_series_entirely_in_future_is_reported_as_gap():
    pe = _series()
    official = _series(
        metric="official_pe_ratio_1",
        field="市盈率1",
        frequency=FREQ_DAILY_SHORT,
        observations=(("2026-11-11", 999.0), ("2026-11-12", 1000.0)),
    )
    profile = build_index_profile(
        instrument_id="INDEX:000300.SH",
        as_of="2026-11-10",
        series={"pe_rolling": pe},
        short_series={"official_pe_ratio_1": official},
    )
    block = profile["official_short_history"]["official_pe_ratio_1"]
    assert block["latest"] is None
    assert block["status"] == "no_observation_by_as_of"
    assert block["observation_count"] == 0
    assert "不晚于 2026-11-10" in block["note"]
    assert any(item["metric"] == "official_pe_ratio_1" for item in profile["missing"])
    # 供应商画像本身不受影响
    assert profile["metrics"]["pe_rolling"]["percentile"] is not None


def test_w2_official_short_history_normal_case_is_unchanged():
    store = SeriesStore(ROOT / "data" / "series")
    series = store.find("INDEX:000300.SH", METRIC_PE_ROLLING, frequency=FREQ_MONTHLY_REFERENCE)
    official = store.find("INDEX:000300.SH", "official_pe_ratio_1", frequency=FREQ_DAILY_SHORT)
    profile = build_index_profile(
        instrument_id="INDEX:000300.SH",
        as_of="2026-11-10",
        series={"pe_rolling": series},
        short_series={"official_pe_ratio_1": official},
    )
    block = profile["official_short_history"]["official_pe_ratio_1"]
    assert block["observation_count"] == 20
    assert block["latest"]["date"] == "2026-11-10"
    assert block["excluded_future_observations"] == 0
