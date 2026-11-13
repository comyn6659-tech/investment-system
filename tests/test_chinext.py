"""创业板指（399006）第四批验收测试：Q1–Q8。

重点不是「再算一遍估值」，而是证明：
- 只接入官方收盘、全收益与官方当前估值快照；
- 官方估值历史不可得时，不计算分位、不生成信号、不回测；
- 第三方估值与板块 PE 不能进入分位；
- 名称识别、既有指数/账本/ETF 结果不被破坏。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.backtest.weekly import build_weekly_observations, weekly_percentiles
from engine.contracts.enums import ResolutionStatus
from engine.identity.catalog import IdentityCatalog
from engine.identity.resolver import resolve
from engine.pipeline import Analyzer, AnalyzerConfig
from engine.series.models import (
    FREQ_DAILY,
    FREQ_DAILY_SHORT,
    FREQ_MONTHLY_REFERENCE,
    METRIC_OFFICIAL_CLOSE,
    METRIC_OFFICIAL_PB_1,
    METRIC_OFFICIAL_PE_1,
    METRIC_OFFICIAL_PE_ROLLING,
    METRIC_PE_ROLLING,
    METRIC_TOTAL_RETURN_CLOSE,
    IndexSeries,
    SeriesSource,
    SeriesStore,
)
from engine.series.profile import build_index_profile
from engine.signals.candidate_a import build_candidate_a_signal
from engine.signals.ledger import SignalLedger

ROOT = Path(__file__).resolve().parent.parent
SERIES_DIR = ROOT / "data" / "series"
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
LEDGER_PATH = ROOT / "data" / "ledger" / "candidate-a-signals.jsonl"
EQUITY_LEDGER = ROOT / "data" / "ledger" / "equity-conclusions.jsonl"
BACKTEST_JSON = ROOT / "data" / "backtest" / "candidate-a.json"
FEASIBILITY = ROOT / "data" / "coverage" / "chinext-feasibility.json"
CHINEXT = "INDEX:399006.SZ"


@pytest.fixture(scope="module")
def store() -> SeriesStore:
    return SeriesStore(SERIES_DIR)


@pytest.fixture(scope="module")
def chinext_series(store):
    close = store.find(CHINEXT, METRIC_OFFICIAL_CLOSE, frequency=FREQ_DAILY)
    tr = store.find(CHINEXT, METRIC_TOTAL_RETURN_CLOSE, frequency=FREQ_DAILY)
    pe = store.find(CHINEXT, METRIC_OFFICIAL_PE_1, frequency=FREQ_DAILY_SHORT)
    pb = store.find(CHINEXT, METRIC_OFFICIAL_PB_1, frequency=FREQ_DAILY_SHORT)
    assert close and tr and pe and pb
    return close, tr, pe, pb


@pytest.fixture(scope="module")
def chinext_analyzer() -> Analyzer:
    return Analyzer(AnalyzerConfig())


# --- Q1 可行性 --------------------------------------------------------------
def test_feasibility_record_is_explicit():
    data = json.loads(FEASIBILITY.read_text(encoding="utf-8"))
    assert data["decision"] == "short_history_only"
    assert data["official_history_available"] is True
    assert data["official_pe_history_available"] is False
    assert data["current_official_valuation"]["date"] == "2026-11-11"
    assert data["current_official_valuation"]["pe_dynamic"] == pytest.approx(25.1325)
    assert data["current_official_valuation"]["pb"] == pytest.approx(4.07)
    probes = {p["probe_id"]: p for p in data["probes"]}
    assert probes["c1_cni_daily_close_399006"]["official"] is True
    assert probes["c1_cni_daily_close_399006"]["adopted"] is True
    assert probes["c1_cni_total_return_399606"]["official"] is True
    assert probes["c2_cni_current_valuation"]["official"] is True
    assert probes["c2_cni_pe_history"]["status"] == "missing"
    assert probes["c2_cni_pe_history"]["adopted"] is False
    assert probes["c2_ceic_chinext_board_pe"]["adopted"] is False
    assert probes["c2_ceic_chinext_board_pe"]["official"] is False
    assert "5 年月度估值画像" in " ".join(data["not_implemented"])
    assert (ROOT / "docs" / "phase4c-chinext-feasibility.md").exists()


# --- 官方序列 ---------------------------------------------------------------
def test_chinext_official_series(chinext_series):
    close, tr, pe, pb = chinext_series
    for item in (close, tr):
        assert item.source.source_id == "cnindex.official_history"
        assert item.start_date == "2010-06-01"
        assert item.end_date == "2026-11-11"
        assert len(item.observations) == 3993
        assert item.observations[0] == ("2010-06-01", pytest.approx(973.2333))
    assert close.observations[-1] == ("2026-11-11", pytest.approx(2572.0672))
    assert tr.observations[-1] == ("2026-11-11", pytest.approx(2875.2326))
    assert close.metric == METRIC_OFFICIAL_CLOSE
    assert tr.metric == METRIC_TOTAL_RETURN_CLOSE
    assert close.unit == "index_point"

    for item in (pe, pb):
        assert item.source.source_id == "cnindex.index_list"
        assert item.frequency == FREQ_DAILY_SHORT
        assert len(item.observations) == 1
        assert item.end_date == "2026-11-11"
    assert pe.observations[0][1] == pytest.approx(25.1325)
    assert pb.observations[0][1] == pytest.approx(4.07)
    # 没有官方 PE 日频长序列；不得用第三方列伪造
    assert store_is_missing(METRIC_OFFICIAL_PE_ROLLING)


def store_is_missing(metric: str, store: SeriesStore | None = None) -> bool:
    return (store or SeriesStore(SERIES_DIR)).find(
        CHINEXT, metric, frequency=FREQ_DAILY
    ) is None


def test_series_hash_can_be_recomputed(store, chinext_series):
    close = chinext_series[0]
    stored = json.loads((SERIES_DIR / f"{close.series_id}.json").read_text(encoding="utf-8"))
    rebuilt = IndexSeries.from_dict(stored)
    assert rebuilt.series_id == close.series_id
    assert rebuilt.observations == close.observations
    # 独立重算：用原始观测重新构造，内容哈希必须一致
    manual = IndexSeries(
        instrument_id=close.instrument_id,
        metric=close.metric,
        field=close.field,
        frequency=close.frequency,
        source=SeriesSource.from_dict(stored["source"]),
        observations=tuple((row["date"], row["value"]) for row in stored["observations"]),
        unit=close.unit,
        transform=close.transform,
        aggregation_method=close.aggregation_method,
        denominator_basis=close.denominator_basis,
        loss_sample_handling=close.loss_sample_handling,
        revision_availability=close.revision_availability,
        notes=close.notes,
    )
    assert manual.series_id == close.series_id


# --- Q2 非官方来源不能进入分位 ----------------------------------------------
def _third_party_pe(
    observations, *, frequency=FREQ_MONTHLY_REFERENCE, metric=METRIC_PE_ROLLING
) -> IndexSeries:
    return IndexSeries(
        instrument_id=CHINEXT,
        metric=metric,
        field="滚动市盈率",
        frequency=frequency,
        source=SeriesSource(
            source_id="legulegu.index_pe_pb",
            publisher="乐咕乐股",
            interface="第三方",
            url="https://legulegu.com/",
            retrieved_at="2026-11-12T00:00:00+08:00",
        ),
        observations=tuple(observations),
    )


def test_non_official_profile_series_is_rejected():
    third = _third_party_pe([("2026-10-31", 20.0)])
    profile = build_index_profile(
        instrument_id=CHINEXT,
        as_of="2026-11-11",
        series={METRIC_PE_ROLLING: third},
        official_only=True,
    )
    assert profile["status"] == "insufficient_data"
    assert profile["metrics"] == {}
    assert any("非官方来源" in item["reason"] for item in profile["missing"])


def test_non_official_signal_series_is_rejected(tmp_path):
    store = SeriesStore(tmp_path / "series")
    real = SeriesStore(SERIES_DIR)
    close = real.find(CHINEXT, METRIC_OFFICIAL_CLOSE, frequency=FREQ_DAILY)
    store.save(close)
    store.save(
        _third_party_pe(
            [("2026-11-10", 20.0), ("2026-11-11", 20.0)],
            frequency=FREQ_DAILY,
            metric=METRIC_OFFICIAL_PE_ROLLING,
        )
    )
    store.rebuild_manifest()
    signal = build_candidate_a_signal(store, CHINEXT, as_of="2026-11-11")
    assert signal["status"] == "insufficient_data"
    assert signal["signal"] == "not_evaluable"
    assert "official_pe_rolling" in signal["rejected_non_official_metrics"]
    assert "不是中证/国证" in signal["reason"]


def test_chinext_signal_is_not_computed(store):
    signal = build_candidate_a_signal(store, CHINEXT, as_of="2026-11-11")
    assert signal["status"] == "insufficient_data"
    assert signal["signal"] == "not_evaluable"
    assert signal["latest"] is None
    assert signal["source_policy"] == "only_csindex_or_cnindex"
    assert signal["series_ids"]["close"]
    assert signal["series_ids"]["total_return"]
    assert signal["series_ids"]["pe"] is None
    assert "缺少官方指数收盘或官方滚动市盈率" in signal["reason"]
    assert signal["backtest"] is None


# --- Q3 共用代码与独立重算 --------------------------------------------------
def test_shared_profile_and_signal_functions(monkeypatch, chinext_analyzer):
    import engine.pipeline as pipeline

    calls = {}
    original_profile = pipeline.build_index_profile
    original_signal = pipeline.build_candidate_a_signal

    def profile_wrapper(*args, **kwargs):
        calls["profile"] = kwargs
        return original_profile(*args, **kwargs)

    def signal_wrapper(*args, **kwargs):
        calls["signal"] = (args, kwargs)
        return original_signal(*args, **kwargs)

    monkeypatch.setattr(pipeline, "build_index_profile", profile_wrapper)
    monkeypatch.setattr(pipeline, "build_candidate_a_signal", signal_wrapper)
    result = chinext_analyzer.analyze("创业板指")
    assert result.analysis_status == "insufficient_data"
    assert calls["profile"]["instrument_id"] == CHINEXT
    assert calls["profile"]["official_only"] is True
    assert calls["signal"][1]["as_of"] == "2026-11-11"


def test_official_short_history_is_used_by_profile(chinext_series):
    close, tr, pe, pb = chinext_series
    profile = build_index_profile(
        instrument_id=CHINEXT,
        as_of="2026-11-11",
        series={},
        short_series={METRIC_OFFICIAL_PE_1: pe, METRIC_OFFICIAL_PB_1: pb},
        official_only=True,
    )
    assert profile["status"] == "insufficient_data"
    assert profile["metrics"] == {}
    assert set(profile["official_short_history"]) == {
        METRIC_OFFICIAL_PE_1,
        METRIC_OFFICIAL_PB_1,
    }
    latest = profile["official_short_history"][METRIC_OFFICIAL_PE_1]["latest"]
    assert latest == {"date": "2026-11-11", "value": pytest.approx(25.1325)}
    assert "没有可用的官方日度或月度历史" in " ".join(profile["limitations"])
    assert "不计算分位" in profile["decision_note"]


# --- Q4 回测不做 ------------------------------------------------------------
def test_no_chinext_backtest_entry():
    data = json.loads(BACKTEST_JSON.read_text(encoding="utf-8"))
    assert "399006" not in data["indices"]
    assert "399006" not in json.dumps(data, ensure_ascii=False)


# --- Q5 账本 ----------------------------------------------------------------
def test_ledger_has_no_chinext_record_and_existing_three_unchanged():
    ledger = SignalLedger(LEDGER_PATH)
    assert ledger.check() == []
    records = ledger.load()
    assert len(records) == 3
    assert {record["instrument_id"] for record in records} == {
        "INDEX:000300.SH",
        "INDEX:000905.SH",
        "INDEX:000852.SH",
    }
    assert all(record["instrument_id"] != CHINEXT for record in records)
    # 个股结论账本也不因本批变化
    lines = [line for line in EQUITY_LEDGER.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) >= 7


# --- Q6 名称识别 ------------------------------------------------------------
def test_name_resolution_three_cases(catalog):
    for text in ("创业板指", "创业板指数", "399006"):
        resolution = resolve(text, catalog)
        assert resolution.status == ResolutionStatus.RESOLVED.value
        assert resolution.instrument is not None
        assert resolution.instrument.instrument_id == CHINEXT

    ambiguous = resolve("创业板", catalog)
    assert ambiguous.status == ResolutionStatus.AMBIGUOUS.value
    assert "交易板块" in (ambiguous.clarification_question or "")
    assert "创业板指" in (ambiguous.clarification_question or "")
    assert [item.instrument_id for item in ambiguous.candidates] == [CHINEXT]

    variant = resolve("创业板50", catalog)
    assert variant.status == ResolutionStatus.OUT_OF_SCOPE.value
    assert variant.reason_codes == ("OUT_OF_SCOPE_INDEX_VARIANT",)


def test_cli_bare_board_needs_clarification(chinext_analyzer):
    result = chinext_analyzer.analyze("创业板")
    assert result.analysis_status == "needs_clarification"
    assert "创业板指" in (result.clarification or "")


# --- CLI / 指数结果 ---------------------------------------------------------
def test_cli_chinext_is_undetermined(chinext_analyzer):
    result = chinext_analyzer.analyze("创业板指")
    assert result.analysis_status == "insufficient_data"
    assert result.decision == "undetermined"
    assert result.model_status is None
    assert result.valuation_state == "unknown"
    assert "INDEX_PROFILE_SERIES_MISSING" in result.reason_codes
    assert "INDEX_SIGNAL_RESEARCH_ONLY" not in result.reason_codes
    assert result.payload.get("policy_conclusion") is None
    profile = result.payload["valuation_profile"]
    assert profile["status"] == "insufficient_data"
    assert profile["metrics"] == {}
    assert len(profile["official_short_history"]) == 2
    library = result.payload["history_coverage"]["series_library"]
    assert library["status"] == "available"
    assert library["supports_weekly_5y_history"] is False
    assert "official_close" in library["series"]
    assert "total_return_close" in library["series"]
    assert "official_pe_rolling" not in library["series"]


def test_index_snapshot_is_official_only(snapshot_dir):
    from engine.data.snapshot import SnapshotStore

    store = SnapshotStore(snapshot_dir)
    latest = store.latest_for(CHINEXT)
    assert latest
    snapshot = store.load(latest)
    assert snapshot.as_of == "2026-11-11"
    assert snapshot.instrument_id == CHINEXT
    assert {fact.source.source_id for fact in snapshot.facts} == {
        "cnindex.official_history",
        "cnindex.index_list",
    }
    coverage = snapshot.coverage
    assert coverage["valuation_history"]["available"] is False
    assert coverage["supports_5y_window"] is False
    assert "point_in_time_granularity" in coverage


# --- ETF --------------------------------------------------------------------
def test_etf_159915_registry_and_product_check(chinext_analyzer):
    catalog = IdentityCatalog.load(ROOT / "data" / "registry" / "instruments.json")
    etf = catalog.get("ETF:159915.SZ")
    assert etf is not None
    assert etf.underlying_index_id == CHINEXT
    assert etf.display_name == "创业板ETF易方达"

    result = chinext_analyzer.analyze("159915")
    assert result.instrument_id == "ETF:159915.SZ"
    assert result.analysis_status == "insufficient_data"  # 指数估值历史不足
    product = result.payload["product_check"]
    assert product["identity"]["underlying_index"]["instrument_id"] == CHINEXT
    assert "创业板指数" in (product["identity"]["tracking_note"]["value"] or "")
    assert product["price"]["price"]["value"] == pytest.approx(2.590, abs=1e-6)
    assert product["nav"]["value"]["value"] == pytest.approx(2.5894, abs=1e-6)
    assert product["fees"]["management"]["value"] == pytest.approx(0.15)
    assert product["fees"]["custody"]["value"] == pytest.approx(0.05)
    assert product["trading"]["secondary_market_status"]["status"] in ("missing", "unknown")


# --- Q7 既有结果不变 --------------------------------------------------------
def test_existing_three_indices_unchanged(store):
    expected = {
        "INDEX:000300.SH": "exit",
        "INDEX:000905.SH": "exit",
        "INDEX:000852.SH": "exit",
    }
    for instrument_id, signal_name in expected.items():
        signal = build_candidate_a_signal(store, instrument_id, as_of="2026-11-11")
        assert signal["status"] == "complete"
        assert signal["signal"] == signal_name
        assert signal["latest"]["percentile"] is not None


def test_chinext_signal_has_no_weekly_observations_because_no_pe(chinext_series):
    close, _, pe, _ = chinext_series
    # 即使有收盘，没有官方 PE 也不能构造出可评估的周度观察点
    weekly = build_weekly_observations(close.observations, pe.observations)
    assert weekly
    assert sum(1 for point in weekly if point.pe is not None) == 1
    assert all(not item.evaluable for item in weekly_percentiles(weekly))
