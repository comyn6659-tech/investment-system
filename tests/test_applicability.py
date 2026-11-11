"""模型适用性、业务证据与研究报告期的测试。

覆盖施工指令 P2-1 与 P2-3 的判定要求：
- 行业只是线索，不单独决定模型；
- 首版未实现路线（保险/券商/控股/周期/困境）不被默认送进 DCF；
- 选择不随当前股价改变；
- 缺少业务证据时停在「尚不能判断」；
- 报告期累计数不可强行年化，TTM 需要可比期间。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.contracts import reason_codes as rc
from engine.evidence.applicability import (
    SELECTION_POLICY,
    SelectionMetrics,
    classify_family,
    decide,
)
from engine.evidence.models import AnnualPoint, BusinessEvidence, EvidenceBook, Segment
from engine.financials.periods import parse_period, single_quarter_value, ttm_value
from engine.valuation.errors import ValuationError

ROOT = Path(__file__).resolve().parent.parent


def _evidence(industry: str, segments=(), annual=()) -> BusinessEvidence:
    return BusinessEvidence(
        instrument_id="EQUITY:000000.SH",
        display_name="测试标的",
        as_of="2026-11-10",
        version="2026-11-10",
        prepared_at="2026-11-11T00:00:00+08:00",
        industry_clue=industry,
        segments=tuple(Segment(segment=name, revenue_share=share) for name, share in segments),
        regional_segments=(),
        annual=tuple(
            AnnualPoint(fiscal_year=year, revenue=revenue, net_profit_parent=profit)
            for year, revenue, profit in annual
        ),
        sources=({"evidence_id": "ev-test", "kind": "vendor_api"},),
    )


def _metrics(**overrides) -> SelectionMetrics:
    base = dict(
        capex_intensity=0.02,
        cash_conversion=0.9,
        payout_latest=0.5,
        payout_mean_3y=0.5,
        dividend_years_observed=3,
        dividend_stability_cv=0.05,
    )
    base.update(overrides)
    return SelectionMetrics(**base)


# --- 企业族判定 -------------------------------------------------------------
def test_bank_family_from_industry_and_segments():
    evidence = _evidence("银行Ⅱ", (("公司金融业务", 0.48), ("个人金融业务", 0.37)))
    family, reasons = classify_family(evidence)
    assert family == "bank"
    assert reasons[0]["signal"] == "industry"


def test_insurance_family_is_recognised_before_bank_segment():
    """综合金融集团里有银行分部，仍应按保险处理。"""
    evidence = _evidence(
        "保险Ⅱ", (("寿险及健康险", 0.47), ("财产保险", 0.31), ("银行", 0.12))
    )
    family, _ = classify_family(evidence)
    assert family == "insurance"


def test_broker_family_from_industry():
    family, _ = classify_family(_evidence("证券Ⅱ", (("证券投资业务", 0.4),)))
    assert family == "broker"


def test_holding_family_from_industry():
    family, _ = classify_family(_evidence("综合"))
    assert family == "holding"


def test_cyclical_needs_industry_and_volatility_together():
    volatile = _evidence(
        "煤炭开采",
        (("煤炭收入", 0.58),),
        annual=[(2020, 2332.0, 391.0), (2021, 3356.0, 500.0), (2022, 3445.0, 696.0), (2025, 2949.0, 528.0)],
    )
    family, reasons = classify_family(volatile)
    assert family == "cyclical"
    assert reasons[0]["signal"] == "industry_plus_revenue_volatility"

    stable = _evidence(
        "煤炭开采",
        annual=[(2020, 1000.0, 100.0), (2021, 1010.0, 101.0), (2022, 1020.0, 102.0)],
    )
    family_stable, _ = classify_family(stable)
    assert family_stable == "non_financial", "行业本身不足以判定周期"


def test_industry_alone_cannot_force_a_model_choice():
    """同一行业但无周期证据时不会被送进「未实现路线」。"""
    evidence = _evidence("白酒Ⅱ", (("茅台酒", 0.86),), annual=[(2024, 1741.0, 862.0), (2025, 1720.0, 823.0)])
    family, _ = classify_family(evidence)
    assert family == "non_financial"


def test_loss_making_company_is_distressed_not_fcff():
    evidence = _evidence(
        "白酒Ⅱ",
        annual=[(2023, 100.0, -5.0), (2024, 90.0, -20.0), (2025, 80.0, -30.0)],
    )
    family, _ = classify_family(evidence)
    assert family == "distressed"


# --- 未实现路线 -------------------------------------------------------------
@pytest.mark.parametrize(
    "industry,segments,expected",
    [
        ("保险Ⅱ", (("寿险及健康险", 0.5),), "insurance"),
        ("证券Ⅱ", (("经纪业务", 0.4),), "broker"),
        ("综合", (("投资收益", 0.9),), "holding"),
    ],
)
def test_unimplemented_families_are_not_sent_to_dcf(industry, segments, expected):
    decision = decide(_evidence(industry, segments), _metrics())
    assert decision.status == "family_not_implemented"
    assert decision.family == expected
    assert decision.primary_model is None
    assert decision.applicable_model_category
    assert decision.required_evidence_to_implement
    assert all(item.reason_code == rc.MODEL_FAMILY_NOT_IMPLEMENTED for item in decision.rejected)
    payload = decision.to_dict()
    assert payload["status"] == "family_not_implemented"
    assert payload["applicable_model_category"]


def test_distressed_company_is_not_given_a_dcf():
    evidence = _evidence("白酒Ⅱ", annual=[(2024, 100.0, -10.0), (2025, 90.0, -20.0)])
    decision = decide(evidence, _metrics())
    assert decision.status == "family_not_implemented"
    assert decision.family == "distressed"


# --- 主模型选择 -------------------------------------------------------------
def test_low_capex_high_conversion_selects_fcff():
    decision = decide(_evidence("白酒Ⅱ"), _metrics(capex_intensity=0.02, cash_conversion=0.9))
    assert decision.status == "selected"
    assert decision.primary_model == "equity_dcf_fcff"
    assert decision.cross_check_model == "equity_dividend_discount"


def test_capital_intensive_high_payout_selects_dividend():
    decision = decide(
        _evidence("电力"), _metrics(capex_intensity=0.21, cash_conversion=0.9, payout_mean_3y=0.71)
    )
    assert decision.primary_model == "equity_dividend_discount"
    assert decision.cross_check_model == "equity_dcf_fcff"


def test_bank_selects_bank_model_and_rejects_generic_models():
    decision = decide(_evidence("银行Ⅱ"), _metrics())
    assert decision.primary_model == "bank_dividend_discount"
    reasons = {item.model_id: item.reason_code for item in decision.rejected}
    assert reasons["equity_dcf_fcff"] == rc.MODEL_NOT_APPLICABLE_TO_BANK
    assert reasons["equity_dividend_discount"] == rc.MODEL_NOT_APPLICABLE_TO_BANK


def test_selection_does_not_depend_on_price():
    """选择函数不接收价格：同一份证据与指标必须给出相同结论。"""
    import inspect

    signature = inspect.signature(decide)
    assert list(signature.parameters) == ["evidence", "metrics"]
    first = decide(_evidence("白酒Ⅱ"), _metrics())
    second = decide(_evidence("白酒Ⅱ"), _metrics())
    assert first.to_dict()["primary_model"] == second.to_dict()["primary_model"]


def test_missing_business_evidence_stops_at_insufficient():
    decision = decide(None, None)
    assert decision.status == "insufficient_evidence"
    assert decision.primary_model is None
    assert decision.reasons[0]["signal"] == "business_evidence_missing"


def test_missing_operating_metrics_stops_between_two_routes():
    decision = decide(_evidence("白酒Ⅱ"), None)
    assert decision.status == "insufficient_evidence"
    assert decision.primary_model is None
    assert decision.reasons[-1]["signal"] == "operating_metrics_missing"


def test_selection_policy_is_exposed_and_unapproved():
    decision = decide(_evidence("白酒Ⅱ"), _metrics())
    policy = decision.to_dict()["policy"]
    assert policy["status"] == "research_candidate_pending_approval"
    assert policy["policy_version"] == SELECTION_POLICY["policy_version"]


# --- 证据文件 ---------------------------------------------------------------
def test_evidence_book_loads_real_files_and_verifies_hash():
    book = EvidenceBook(ROOT / "data" / "evidence")
    assert book.errors == {}
    ids = list(book.instrument_ids())
    assert "EQUITY:600519.SH" in ids
    assert "EQUITY:601398.SH" in ids
    evidence = book.get("EQUITY:600519.SH")
    assert evidence is not None
    assert evidence.industry_clue
    assert evidence.segments
    assert evidence.annual


def test_tampered_evidence_file_is_rejected(tmp_path):
    source = (ROOT / "data" / "evidence" / "EQUITY-600519.SH.json").read_text(encoding="utf-8")
    data = json.loads(source)
    data["industry_clue"] = "被改过的行业"
    (tmp_path / "EQUITY-600519.SH.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    book = EvidenceBook(tmp_path)
    assert book.get("EQUITY:600519.SH") is None
    assert book.errors


# --- 报告期口径 -------------------------------------------------------------
def test_period_parsing_and_single_quarter_split():
    assert parse_period("2025-12-31").kind == "annual"
    assert parse_period("2025-06-30").kind == "h1"
    cumulative = {
        "2025-03-31": 100.0,
        "2025-06-30": 220.0,
        "2025-09-30": 330.0,
        "2025-12-31": 460.0,
    }
    assert single_quarter_value(cumulative, "2025-06-30") == pytest.approx(120.0)
    assert single_quarter_value(cumulative, "2025-09-30") == pytest.approx(110.0)
    assert single_quarter_value(cumulative, "2025-12-31") == pytest.approx(130.0)


def test_non_standard_period_end_is_rejected():
    with pytest.raises(ValuationError):
        parse_period("2025-05-31")


def test_ttm_requires_prior_annual_and_prior_same_period():
    with pytest.raises(ValuationError) as excinfo:
        ttm_value({"2026-09-30": 900.0}, "2026-09-30")
    assert excinfo.value.code == "MODEL_INPUT_MISSING"
    assert "强行年化" in excinfo.value.message


def test_ttm_uses_additive_formula_not_multiplication():
    cumulative = {
        "2025-12-31": 1000.0,
        "2025-09-30": 700.0,
        "2026-09-30": 760.0,
    }
    value, basis = ttm_value(cumulative, "2026-09-30")
    # 760 + 1000 − 700 = 1060（不是 760×4/3）
    assert value == pytest.approx(1060.0)
    assert basis["method"] == "ttm_additive"
    assert "未做任何比例放大" in basis["note"]


def test_annual_period_ttm_is_itself():
    value, basis = ttm_value({"2025-12-31": 1000.0}, "2025-12-31")
    assert value == pytest.approx(1000.0)
    assert basis["method"] == "annual_period"
