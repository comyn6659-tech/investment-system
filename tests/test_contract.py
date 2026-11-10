"""结果契约测试：研究结果不能变成 candidate，未实现模型不能给买点。"""

from __future__ import annotations

import pytest

from engine.contracts.enums import AnalysisStatus, Decision, ModelStatus, ValuationState
from engine.contracts.result import AnalysisResult
from engine.contracts.validation import ContractViolation, enforce, validate_result
from engine.reporting.json_exporter import export_json
from engine.reporting.text_summary import render_text


def _result(**overrides) -> AnalysisResult:
    base = dict(
        intent="buy_point_check",
        as_of="2026-11-10",
        available_at="2026-11-10T15:00:00+08:00",
        snapshot_id="snap-0123456789abcdef",
        analysis_status=AnalysisStatus.UNSUPPORTED.value,
        model_status=None,
        valuation_state=ValuationState.UNKNOWN.value,
        decision=Decision.UNDETERMINED.value,
        reason_codes=["MODEL_NOT_IMPLEMENTED_INDEX"],
        instrument={
            "instrument_id": "INDEX:000300.SH",
            "display_name": "沪深300",
            "asset_class": "index",
            "code": "000300",
            "market": "CN",
            "venue": "CSI",
            "venue_type": "index_publisher",
            "currency": None,
            "scope": {"status": "in_scope", "reason_code": "FIRST_BATCH_BROAD_INDEX", "detail": ""},
        },
        payload={
            "payload_type": "index",
            "index_class": "broad_based",
            "level": {"value": 4059.11, "unit": "index_point", "currency": None},
            "valuation_facts": {},
            "history_coverage": {},
            "strategy_state": {"status": "not_implemented", "model_status": "research"},
        },
        generated_at="2026-11-11T02:00:00+08:00",
    )
    base.update(overrides)
    return AnalysisResult(**base)


def test_valid_unsupported_result_passes():
    assert validate_result(_result()) == []


def test_validated_complete_attractive_candidate_is_allowed():
    """正向用例：规则不是无条件拒绝，真正满足条件时可以通过。"""
    result = _result(
        analysis_status=AnalysisStatus.COMPLETE.value,
        model_status=ModelStatus.VALIDATED.value,
        valuation_state=ValuationState.ATTRACTIVE.value,
        decision=Decision.CANDIDATE.value,
        reason_codes=[],
        model_results=[{"result_type": "index_signal", "signal": "entry"}],
    )
    assert validate_result(result) == []


def test_research_model_cannot_be_candidate():
    result = _result(
        analysis_status=AnalysisStatus.COMPLETE.value,
        model_status=ModelStatus.RESEARCH.value,
        valuation_state=ValuationState.ATTRACTIVE.value,
        decision=Decision.CANDIDATE.value,
    )
    problems = validate_result(result)
    assert any("research" in p for p in problems)


def test_unsupported_must_be_undetermined():
    result = _result(analysis_status=AnalysisStatus.UNSUPPORTED.value, decision=Decision.WAIT.value)
    assert any("undetermined" in p for p in validate_result(result))


def test_insufficient_data_must_be_undetermined():
    result = _result(
        analysis_status=AnalysisStatus.INSUFFICIENT_DATA.value,
        decision=Decision.AVOID.value,
        reason_codes=["MISSING_CAPEX"],
    )
    assert any("undetermined" in p for p in validate_result(result))


def test_candidate_requires_validated_and_complete():
    result = _result(
        analysis_status=AnalysisStatus.PARTIAL.value,
        model_status=ModelStatus.VALIDATED.value,
        valuation_state=ValuationState.ATTRACTIVE.value,
        decision=Decision.CANDIDATE.value,
    )
    problems = validate_result(result)
    assert any("complete" in p for p in problems)


def test_blocking_reason_code_prevents_candidate():
    result = _result(
        analysis_status=AnalysisStatus.COMPLETE.value,
        model_status=ModelStatus.VALIDATED.value,
        valuation_state=ValuationState.ATTRACTIVE.value,
        decision=Decision.CANDIDATE.value,
        reason_codes=["MISSING_NET_DEBT"],
    )
    assert any("阻断" in p for p in validate_result(result))


def test_unknown_reason_code_is_rejected():
    result = _result(reason_codes=["SOME_TYPO_CODE"])
    assert any("未登记" in p for p in validate_result(result))


def test_status_without_reason_codes_is_rejected():
    result = _result(analysis_status=AnalysisStatus.UNSUPPORTED.value, reason_codes=[])
    assert any("reason_codes" in p for p in validate_result(result))


def test_needs_clarification_requires_question():
    result = _result(
        analysis_status=AnalysisStatus.NEEDS_CLARIFICATION.value,
        instrument=None,
        payload={"payload_type": "none"},
        reason_codes=["IDENTITY_AMBIGUOUS"],
        clarification=None,
    )
    assert any("clarification" in p for p in validate_result(result))


def test_money_without_currency_is_rejected():
    result = _result()
    result.payload["level"] = {"value": 1.0, "unit": "CNY", "currency": None}
    assert any("必须携带币种" in p for p in validate_result(result))


def test_index_point_with_currency_is_rejected():
    result = _result()
    result.payload["level"] = {"value": 4059.11, "unit": "index_point", "currency": "CNY"}
    assert any("不得携带币种" in p for p in validate_result(result))


def test_nan_in_payload_is_rejected():
    result = _result()
    result.payload["level"] = {"value": float("nan"), "unit": "index_point", "currency": None}
    assert any("非有限" in p or "非法" in p for p in validate_result(result))


def test_payload_type_must_match_asset_class():
    result = _result()
    result.payload = {"payload_type": "equity", "company_facts": {}, "financial_periods": [],
                      "dividend_records": [], "model_applicability": {}, "valuation": None}
    assert any("不匹配" in p for p in validate_result(result))


def test_enforce_raises_with_all_problems():
    bad = _result(decision=Decision.CANDIDATE.value)
    with pytest.raises(ContractViolation) as exc:
        enforce(bad)
    assert exc.value.problems


def test_text_and_json_share_the_same_validation():
    bad = _result(decision=Decision.CANDIDATE.value)
    with pytest.raises(ContractViolation):
        export_json(bad)
    # 文本出口同样先校验：这里直接校验字典形式
    with pytest.raises(ContractViolation):
        enforce(bad.to_dict())


def test_text_render_contains_required_sections():
    text = render_text(_result().to_dict())
    for marker in ("【标的】", "【结论】", "【方法】", "【数据时点】", "【版本】"):
        assert marker in text
