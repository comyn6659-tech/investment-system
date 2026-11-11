"""ETF 产品资料完整性检查与「指数/ETF 同一画像」的回归（对应 I4、I5）。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from engine.data.facts import Fact
from engine.data.snapshot import Snapshot, SnapshotStore
from engine.identity.catalog import IdentityCatalog
from engine.pipeline import Analyzer, AnalyzerConfig
from engine.reporting.json_exporter import export_json
from engine.reporting.product_check import build_product_check
from engine.reporting.text_summary import render_text

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = ROOT / "data" / "snapshots"


@pytest.fixture(scope="module")
def analyzer() -> Analyzer:
    return Analyzer(
        AnalyzerConfig(
            registry_path=ROOT / "data" / "registry" / "instruments.json",
            equity_list_path=ROOT / "data" / "registry" / "cn_a_equities.json",
            snapshot_dir=SNAPSHOT_DIR,
            evidence_dir=ROOT / "data" / "evidence",
            series_dir=ROOT / "data" / "series",
        )
    )


@pytest.fixture(scope="module")
def catalog() -> IdentityCatalog:
    return IdentityCatalog.load(ROOT / "data" / "registry" / "instruments.json")


def _snapshot(catalog: IdentityCatalog, code: str = "510300") -> tuple:
    store = SnapshotStore(SNAPSHOT_DIR)
    instrument = catalog.by_id[f"ETF:{code}.SH"]
    snapshot = store.load(store.latest_for(instrument.instrument_id))
    return instrument, snapshot


def _with_facts(snapshot: Snapshot, mutate) -> Snapshot:
    return replace(snapshot, facts=[mutate(f) for f in snapshot.facts])


def _drop(fact_ids: set[str]):
    def mutate(fact: Fact) -> Fact:
        if fact.fact_id not in fact_ids:
            return fact
        return Fact.missing(
            fact_id=fact.fact_id,
            metric=fact.metric,
            period=fact.period,
            unit=fact.unit,
            currency=fact.currency,
        )

    return mutate


def _set(fact_id: str, value):
    def mutate(fact: Fact) -> Fact:
        if fact.fact_id != fact_id:
            return fact
        return replace(fact, value=value, period=fact.period)

    return mutate


# --- 同日折溢价 -------------------------------------------------------------
def test_same_day_premium_is_computed_from_price_and_nav(catalog):
    instrument, snapshot = _snapshot(catalog)
    check = build_product_check(instrument, catalog.underlying(instrument), snapshot)
    price = snapshot.fact("etf.price").value
    nav = snapshot.fact("etf.nav").value
    premium = check["premium_discount_same_day"]
    assert check["price"]["price"]["period"] == check["nav"]["nav_date"]["value"]
    assert premium["value"] == pytest.approx((price / nav - 1.0) * 100.0)
    assert premium["unit"] == "percent"
    assert premium["currency"] is None
    assert premium["label"] == "事后同日折溢价"
    assert "披露时刻未知" in (premium.get("note") or "")


def test_cross_day_premium_is_refused(catalog):
    instrument, snapshot = _snapshot(catalog)
    variant = _with_facts(snapshot, _set("etf.nav_date", "2026-11-09"))
    check = build_product_check(instrument, catalog.underlying(instrument), variant)
    premium = check["premium_discount_same_day"]
    assert premium["value"] is None
    assert premium["status"] == "rejected"
    assert "不一致" in premium["note"]
    assert check["status"] == "partial"
    assert "premium" in check["missing_computable"]


def test_missing_nav_blocks_premium(catalog):
    instrument, snapshot = _snapshot(catalog)
    variant = _with_facts(snapshot, _drop({"etf.nav"}))
    check = build_product_check(instrument, catalog.underlying(instrument), variant)
    assert check["nav"]["value"]["value"] is None
    assert check["premium_discount_same_day"]["value"] is None
    assert check["status"] == "partial"
    assert set(check["missing_computable"]) >= {"nav", "premium"}


def test_non_positive_nav_blocks_premium(catalog):
    instrument, snapshot = _snapshot(catalog)
    variant = _with_facts(snapshot, _set("etf.nav", 0.0))
    check = build_product_check(instrument, catalog.underlying(instrument), variant)
    assert check["premium_discount_same_day"]["value"] is None
    assert "非正" in check["premium_discount_same_day"]["note"]


# --- 资料缺口 ---------------------------------------------------------------
def test_trading_status_and_spread_are_explicitly_missing(catalog):
    instrument, snapshot = _snapshot(catalog)
    check = build_product_check(instrument, catalog.underlying(instrument), snapshot)
    items = {item["item"] for item in check["missing"]}
    assert "二级市场交易状态（停复牌）" in items
    assert "买卖价差" in items
    assert check["trading"]["secondary_market_status"]["value"] is None
    assert check["trading"]["bid_ask_spread"]["value"] is None
    assert "不等于二级市场交易状态" in check["trading"]["note"]


def test_nav_disclosure_time_unknown_is_reported(catalog):
    instrument, snapshot = _snapshot(catalog)
    check = build_product_check(instrument, catalog.underlying(instrument), snapshot)
    assert check["nav"]["disclosure_time_known"] is False
    assert any(item["item"] == "净值披露时刻" for item in check["missing"])
    assert "不能证明" in (check["premium_discount_same_day"].get("note") or "") or check[
        "premium_discount_same_day"
    ]["value"] is not None


def test_turnover_average_reports_actual_days(catalog):
    instrument, snapshot = _snapshot(catalog)
    check = build_product_check(instrument, catalog.underlying(instrument), snapshot)
    turnover = check["turnover"]
    assert turnover["days_used"] == 20
    assert turnover["is_full_20d"] is True
    assert "20" in turnover["average_label"]


def test_incomplete_turnover_window_is_not_labelled_as_full_20d(catalog):
    instrument, snapshot = _snapshot(catalog)
    variant = _with_facts(snapshot, _set("etf.turnover_avg_days_used", 5))
    check = build_product_check(instrument, catalog.underlying(instrument), variant)
    turnover = check["turnover"]
    assert turnover["days_used"] == 5
    assert turnover["is_full_20d"] is False
    assert turnover["average_label"] == "近 5 个交易日平均成交额"


def test_fees_carry_source_and_unknown_effective_date(catalog):
    instrument, snapshot = _snapshot(catalog)
    check = build_product_check(instrument, catalog.underlying(instrument), snapshot)
    fees = check["fees"]
    assert fees["management"]["value"] == pytest.approx(0.15)
    assert fees["custody"]["value"] == pytest.approx(0.05)
    assert fees["effective_date_known"] is False


# --- 指数与 ETF 的画像一致性 ------------------------------------------------
@pytest.mark.parametrize(
    "etf_code,index_id",
    [("510300", "INDEX:000300.SH"), ("510500", "INDEX:000905.SH"), ("512100", "INDEX:000852.SH")],
)
def test_etf_maps_to_the_right_index(analyzer, etf_code, index_id):
    result = analyzer.analyze(etf_code)
    assert result.payload["underlying_index"]["instrument_id"] == index_id
    assert result.payload["product_check"]["identity"]["underlying_index"]["instrument_id"] == index_id


@pytest.mark.parametrize(
    "etf_code,index_query",
    [("510300", "沪深300"), ("510500", "中证500"), ("512100", "中证1000")],
)
def test_index_profile_is_identical_from_index_and_etf_entry(analyzer, etf_code, index_query):
    index_result = analyzer.analyze(index_query)
    etf_result = analyzer.analyze(etf_code)
    index_profile = index_result.payload["valuation_profile"]
    etf_profile = etf_result.payload["product_check"]["underlying_index_profile"]
    assert json.dumps(index_profile, sort_keys=True) == json.dumps(etf_profile, sort_keys=True)


def test_etf_does_not_change_index_algorithm(analyzer):
    """ETF 分支只加产品资料，不改指数算法：model_plan 与 series id 必须一致。"""
    index_result = analyzer.analyze("沪深300")
    etf_result = analyzer.analyze("510300")
    assert etf_result.model_plan["primary_model"] == index_result.model_plan["primary_model"]
    assert (
        etf_result.model_plan["series_ids"]
        == index_result.model_plan["series_ids"]
    )
    assert etf_result.model_results[0]["metrics"] == index_result.model_results[0]["metrics"]


# --- 出口与护栏 -------------------------------------------------------------
def test_etf_exports_validate_and_have_no_formal_decision(analyzer):
    result = analyzer.analyze("510300")
    assert result.analysis_status == "complete"
    assert result.model_status == "research"
    assert result.decision == "undetermined"
    payload = json.loads(export_json(result))
    assert payload["payload"]["premium_discount"] is None
    assert payload["payload"]["product_check"]["premium_discount_same_day"]["value"] is not None
    text = render_text(result.to_dict())
    assert "【ETF 产品资料完整性检查】" in text
    assert "【5 年月度估值画像（描述性统计，不是交易信号）】" in text
    assert "不构成买卖条件" in text


def test_product_check_cannot_claim_validated(analyzer):
    from engine.contracts.validation import validate_result

    result = analyzer.analyze("510300")
    data = json.loads(export_json(result))
    data["model_status"] = "validated"
    problems = validate_result(data)
    assert any("model_status" in item for item in problems)


def test_index_profile_cannot_be_a_trading_signal(analyzer):
    from engine.contracts.validation import validate_result

    result = analyzer.analyze("沪深300")
    data = json.loads(export_json(result))
    data["payload"]["valuation_profile"]["is_trading_signal"] = True
    assert any("is_trading_signal" in item for item in validate_result(data))

    data2 = json.loads(export_json(result))
    data2["model_results"][0]["is_trading_signal"] = True
    assert any("is_trading_signal" in item for item in validate_result(data2))


def test_index_profile_without_model_result_is_rejected(analyzer):
    from engine.contracts.validation import validate_result

    result = analyzer.analyze("沪深300")
    data = json.loads(export_json(result))
    data["model_results"] = []
    problems = validate_result(data)
    assert any("model_result" in item.lower() or "model_results" in item for item in problems)
