"""端到端（离线）流程测试：阶段一所有结果都不能是 candidate。"""

from __future__ import annotations

import pytest

from engine.contracts.enums import AnalysisStatus, Decision, ValuationState
from engine.contracts.validation import validate_result


@pytest.mark.parametrize("query", ["工商银行", "沪深300", "510300", "512880", "000001", "!!!", ""])
def test_no_input_ever_yields_candidate(query, analyzer):
    result = analyzer.analyze(query)
    assert result.decision == Decision.UNDETERMINED.value
    assert validate_result(result) == []


def test_equity_result_reports_facts_without_becoming_a_buy_point(analyzer):
    """阶段二起个股模型已实现：缺研究假设时停在资料不足，但仍如实展示事实。"""
    result = analyzer.analyze("工商银行")
    assert result.analysis_status == AnalysisStatus.INSUFFICIENT_DATA.value
    assert result.model_status is None, "没有执行模型时不得声称模型已运行"
    assert result.decision == Decision.UNDETERMINED.value
    assert result.valuation_state in (
        ValuationState.UNKNOWN.value,
        ValuationState.NOT_APPLICABLE.value,
    )
    assert result.snapshot_id is not None
    payload = result.payload
    assert payload["payload_type"] == "equity"
    assert payload["valuation"] is None
    assert payload["research_valuation"] is None
    assert payload["financial_periods"], "应展示财报报告期"
    assert payload["dividend_records"], "应展示按财年归集的分红"


def test_equity_dividends_carry_fiscal_year_and_status(analyzer):
    result = analyzer.analyze("工商银行")
    summary = result.payload["dividend_summary"]
    assert summary is not None
    assert summary["fiscal_years"], "应按财年归集"
    for entry in summary["fiscal_years"]:
        assert entry["fiscal_year"] >= 1990
        for component in entry["components"]:
            assert component["kind"], "每条分红必须标注中期/年度/特别类型"
            assert component["status"], "每条分红必须标注实施状态"
            assert component["per_share"] > 0
    # 中期与年度分配相加，不重复计入
    fy2024 = next(e for e in summary["fiscal_years"] if e["fiscal_year"] == 2024)
    kinds = {c["kind"] for c in fy2024["components"]}
    assert kinds == {"中期分红", "年度分红"}
    assert fy2024["ordinary_dps"] == pytest.approx(0.308, abs=1e-6)
    # 财年合计与「过去十二个月实际支付」分开标注
    assert summary["ltm_paid_per_share"] is not None
    assert summary["ltm_paid_per_share"] != fy2024["ordinary_dps"]


def test_index_result_carries_level_without_currency(analyzer):
    result = analyzer.analyze("沪深300")
    assert result.analysis_status == AnalysisStatus.COMPLETE.value
    assert result.decision == Decision.UNDETERMINED.value
    level = result.payload["level"]
    assert level["unit"] == "index_point"
    assert level["currency"] is None
    assert level["value"] > 0
    # 阶段三：月度画像已实现，周度候选仍未实现
    profile = result.payload["valuation_profile"]
    assert profile["status"] == "complete"
    assert profile["metrics"]["pe_rolling"]["percentile_display"] is not None


def test_index_without_series_reports_gap_instead_of_guessing(analyzer):
    """上证指数不在首批、也没有序列：明确报缺口，不偷用别的来源。"""
    result = analyzer.analyze("上证指数")
    assert result.analysis_status == AnalysisStatus.INSUFFICIENT_DATA.value
    assert "INDEX_NOT_IN_FIRST_BATCH" in result.reason_codes
    assert "INDEX_PROFILE_SERIES_MISSING" in result.reason_codes
    assert result.payload["valuation_profile"]["metrics"] == {}
    assert result.model_results == []


def test_etf_result_separates_index_and_product(analyzer):
    result = analyzer.analyze("510300")
    assert result.analysis_status == AnalysisStatus.COMPLETE.value
    payload = result.payload
    assert payload["underlying_index"]["instrument_id"] == "INDEX:000300.SH"
    assert payload["price"]["unit"] == "CNY_per_share"
    assert payload["price"]["currency"] == "CNY"
    # 正式折溢价字段仍保留给已验证模型，同日折溢价只写在产品资料里
    assert payload["premium_discount"] is None
    check = payload["product_check"]
    assert check["premium_discount_same_day"]["value"] is not None
    assert check["underlying_index_profile"]["metrics"]["pe_rolling"]["percentile_display"]
    # 取不到的字段必须明确列出，不能因为「有价格」就默认可交易
    missing_ids = {item.get("fact_id") for item in result.missing_data}
    assert {
        "etf.trading_status",
        "etf.bid_ask_spread_cny",
        "etf.nav_disclosed_at",
    } <= missing_ids
    items = {item.get("item") for item in check["missing"]}
    assert "买卖价差" in items


def test_out_of_scope_does_not_enter_broad_index_flow(analyzer):
    result = analyzer.analyze("512880")
    assert result.analysis_status == AnalysisStatus.UNSUPPORTED.value
    assert result.valuation_state == ValuationState.NOT_APPLICABLE.value
    assert "OUT_OF_SCOPE_INDUSTRY_ETF" in result.reason_codes
    assert result.model_plan["primary_model"] is None
    assert result.model_plan["candidate_models"] == []


def test_strategy_index_is_out_of_scope(analyzer):
    result = analyzer.analyze("000922.CSI")
    assert "OUT_OF_SCOPE_STRATEGY_INDEX" in result.reason_codes


def test_run_of_the_mill_broad_index_is_flagged_not_silently_modelled(analyzer):
    result = analyzer.analyze("中证A500")
    assert result.analysis_status == AnalysisStatus.INSUFFICIENT_DATA.value
    assert "INDEX_NOT_IN_FIRST_BATCH" in result.reason_codes
    assert result.model_results == []


def test_ambiguous_input_asks_and_stops(analyzer):
    result = analyzer.analyze("000001")
    assert result.analysis_status == AnalysisStatus.NEEDS_CLARIFICATION.value
    assert result.instrument is None
    assert len(result.identity["candidates"]) == 2
    assert result.clarification


def test_invalid_input_stops_without_data_call(analyzer):
    result = analyzer.analyze("")
    assert result.analysis_status == AnalysisStatus.NEEDS_CLARIFICATION.value
    assert result.snapshot_id is None
    assert "INPUT_EMPTY" in result.reason_codes


def test_replay_is_byte_identical_except_generated_at(analyzer):
    first = analyzer.analyze("沪深300")
    second = analyzer.analyze("沪深300")
    assert first.replay_fingerprint() == second.replay_fingerprint()
    assert first.analysis_id == second.analysis_id


def test_explicit_snapshot_replay_matches_latest(analyzer):
    latest = analyzer.analyze("中证500")
    replay = analyzer.analyze("中证500", snapshot_id=latest.snapshot_id)
    assert replay.replay_fingerprint() == latest.replay_fingerprint()


def test_unknown_snapshot_id_is_engine_error_not_fabrication(analyzer):
    result = analyzer.analyze("沪深300", snapshot_id="snap-doesnotexist0000")
    assert result.analysis_status == AnalysisStatus.ENGINE_ERROR.value
    assert result.decision == Decision.UNDETERMINED.value
    assert result.snapshot_id is None
    assert validate_result(result) == []
