"""研究假设配置的测试：可重放、带来源、不能自行升级为已验证。

覆盖施工指令 §3.3 的要求：
- 每组假设记录标的、模型与版本、适用日期、基准财年、预测期、折现率、终值、
  情景路径、来源与未批准状态；
- 内容哈希即 assumption_id，改一个数字就换身份；
- 状态只允许研究候选 / 草稿，没有任何路径能升级成 validated。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.assumptions.schema import (
    ALLOWED_STATUSES,
    ASSUMPTION_SCHEMA_VERSION,
    STATUS_CANDIDATE,
    AssumptionSet,
    load_assumptions,
)
from engine.valuation.errors import ValuationError

ROOT = Path(__file__).resolve().parent.parent
ASSUMPTIONS = ROOT / "data" / "assumptions"


def test_all_reference_assumption_files_load_and_validate():
    files = sorted(ASSUMPTIONS.glob("*.json"))
    assert len(files) >= 3
    for path in files:
        assumptions = load_assumptions(path)
        assert assumptions.status == STATUS_CANDIDATE
        assert assumptions.policy_version.startswith("unapproved")
        assert assumptions.scenario("base")
        assert assumptions.discount_rate.provenance.source
        assert assumptions.terminal.provenance.source
        for name in assumptions.provenance:
            assert assumptions.provenance[name].source


def test_assumption_id_is_content_addressed_and_stable():
    path = ASSUMPTIONS / "600519-fcff-base.json"
    first = load_assumptions(path)
    second = load_assumptions(path)
    assert first.assumption_id == second.assumption_id
    assert first.assumption_id.startswith("assump-")
    assert len(first.assumption_id) == len("assump-") + 16


def test_changing_one_number_changes_the_assumption_id(tmp_path):
    data = json.loads((ASSUMPTIONS / "600519-fcff-base.json").read_text(encoding="utf-8"))
    original = AssumptionSet.from_dict(data).assumption_id
    data["scenarios"]["base"]["drivers"]["revenue_growth"][0] = 0.5
    changed = AssumptionSet.from_dict(data).assumption_id
    assert changed != original


def test_declared_id_mismatch_is_rejected(tmp_path):
    data = json.loads((ASSUMPTIONS / "600519-fcff-base.json").read_text(encoding="utf-8"))
    data["assumption_id"] = "assump-0000000000000000"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValuationError) as excinfo:
        load_assumptions(path)
    assert "内容已被改动" in excinfo.value.message


def test_status_cannot_be_validated():
    data = json.loads((ASSUMPTIONS / "600519-fcff-base.json").read_text(encoding="utf-8"))
    data["status"] = "validated"
    with pytest.raises(ValuationError) as excinfo:
        AssumptionSet.from_dict(data)
    assert "validated" in excinfo.value.message
    assert "validated" not in ALLOWED_STATUSES


def test_driver_length_must_match_horizon():
    data = json.loads((ASSUMPTIONS / "600519-fcff-base.json").read_text(encoding="utf-8"))
    data["scenarios"]["base"]["drivers"]["revenue_growth"] = [0.01, 0.02]
    with pytest.raises(ValuationError) as excinfo:
        AssumptionSet.from_dict(data)
    assert "长度" in excinfo.value.message


def test_missing_provenance_is_rejected():
    data = json.loads((ASSUMPTIONS / "600519-fcff-base.json").read_text(encoding="utf-8"))
    data["provenance"].pop("ebit_margin")
    with pytest.raises(ValuationError) as excinfo:
        AssumptionSet.from_dict(data)
    assert "ebit_margin" in excinfo.value.message


def test_empty_source_is_rejected():
    data = json.loads((ASSUMPTIONS / "600519-fcff-base.json").read_text(encoding="utf-8"))
    data["provenance"]["ebit_margin"]["source"] = "   "
    with pytest.raises(ValuationError) as excinfo:
        AssumptionSet.from_dict(data)
    assert "来源" in excinfo.value.message


def test_model_version_must_match_registry():
    data = json.loads((ASSUMPTIONS / "600519-fcff-base.json").read_text(encoding="utf-8"))
    data["model_version"] = "9.9.9-fake"
    with pytest.raises(ValuationError) as excinfo:
        AssumptionSet.from_dict(data)
    assert "登记版本" in excinfo.value.message


def test_unimplemented_model_cannot_carry_assumptions():
    data = json.loads((ASSUMPTIONS / "600519-fcff-base.json").read_text(encoding="utf-8"))
    data["model_id"] = "index_strategy_a_valuation_reversion"
    data["model_version"] = "0.0.0-research"
    with pytest.raises(ValuationError) as excinfo:
        AssumptionSet.from_dict(data)
    assert "尚未实现" in excinfo.value.message


def test_discount_rate_must_exceed_terminal_growth():
    data = json.loads((ASSUMPTIONS / "600519-fcff-base.json").read_text(encoding="utf-8"))
    data["discount_rate"]["value"] = 0.01
    data["terminal"]["growth"] = 0.02
    with pytest.raises(ValuationError) as excinfo:
        AssumptionSet.from_dict(data)
    assert "必须大于终值增长" in excinfo.value.message


def test_schema_version_is_pinned():
    assert ASSUMPTION_SCHEMA_VERSION == "assumption-set/1"
    data = json.loads((ASSUMPTIONS / "600519-fcff-base.json").read_text(encoding="utf-8"))
    data["schema_version"] = "assumption-set/0"
    with pytest.raises(ValuationError):
        AssumptionSet.from_dict(data)


def test_bank_assumptions_require_sourced_risk_thresholds():
    """银行假设必须显式给出 bank_risk 与监管下限，代码里没有隐含默认值。"""
    from engine.valuation.bank import BankRiskEvidence

    missing = BankRiskEvidence.missing_fields({"cet1_ratio": 0.13, "npl_ratio": "未取得"})
    assert "required_cet1_ratio" in missing
    assert "npl_ratio" in missing
