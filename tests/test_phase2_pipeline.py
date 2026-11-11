"""阶段二端到端测试：真实快照 + 真实证据 + 研究假设。

覆盖本轮六项验收条件里可自动化的部分：
- P2-3 真实样本：至少一只非金融公司走完「证据→适用性→计算→报告」；
- P2-4 反例：银行关键字段缺失、未实现路线、无业务证据、无假设；
- P2-5 研究与正式决策分离：没有 candidate/wait/avoid，研究结果可导出；
- P2-6 可复现：同一快照 + 同一假设文件得到同一结果。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from engine.assumptions.schema import load_assumptions
from engine.contracts import reason_codes as rc
from engine.contracts.validation import ContractViolation, enforce, validate_result
from engine.data.facts import Fact
from engine.data.snapshot import Snapshot, SnapshotStore
from engine.pipeline import Analyzer, AnalyzerConfig
from engine.reporting.json_exporter import export_json, parse_and_validate
from engine.reporting.text_summary import render_text

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
ASSUMPTIONS = ROOT / "data" / "assumptions"

MAOTAI_FCFF = ASSUMPTIONS / "600519-fcff-base.json"
MAOTAI_DDM = ASSUMPTIONS / "600519-ddm-crosscheck.json"
CJDL_DDM = ASSUMPTIONS / "600900-ddm-base.json"


def _analyzer(snapshot_dir: Path = SNAPSHOT_DIR) -> Analyzer:
    return Analyzer(
        AnalyzerConfig(
            registry_path=ROOT / "data" / "registry" / "instruments.json",
            equity_list_path=ROOT / "data" / "registry" / "cn_a_equities.json",
            snapshot_dir=snapshot_dir,
            evidence_dir=ROOT / "data" / "evidence",
        )
    )


def _snapshot_obj(data: dict) -> Snapshot:
    return Snapshot(
        instrument_id=data["instrument_id"],
        asset_class=data["asset_class"],
        as_of=data["as_of"],
        available_at=data["available_at"],
        fetched_at=data["fetched_at"],
        facts=[Fact.from_dict(f) for f in data["facts"]],
        coverage=data["coverage"],
        producers=list(data["producers"]),
        notes=data.get("notes", ""),
        schema_version=data["schema_version"],
    )


def _write_variant(tmp_path: Path, snapshot_id: str, mutate) -> str:
    data = json.loads((SNAPSHOT_DIR / f"{snapshot_id}.json").read_text(encoding="utf-8"))
    base = _snapshot_obj(data)
    variant = replace(base, facts=[mutate(f) for f in base.facts])
    new_id = variant.snapshot_id
    (tmp_path / f"{new_id}.json").write_text(
        json.dumps(variant.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return new_id


@pytest.fixture(scope="module")
def analyzer() -> Analyzer:
    return _analyzer()


@pytest.fixture(scope="module")
def maotai_result(analyzer):
    return analyzer.analyze(
        "600519",
        assumptions=load_assumptions(MAOTAI_FCFF),
        cross_check_assumptions=load_assumptions(MAOTAI_DDM),
    )


# --- 真实样本完整链路 -------------------------------------------------------
def test_real_non_financial_company_completes_full_chain(maotai_result):
    result = maotai_result
    assert result.analysis_status == "complete"
    assert result.model_status == "research"
    assert result.decision == "undetermined"
    assert validate_result(result) == []

    plan = result.model_plan
    assert plan["primary_model"] == "equity_dcf_fcff"
    assert plan["model_status"] == "research"
    assert plan["selection_policy_version"] == "model-applicability/1"
    assert plan["family"] == "non_financial"

    payload = result.payload
    assert payload["payload_type"] == "equity"
    assert payload["valuation"] is None, "研究结果不得写入正式 valuation 字段"
    research = payload["research_valuation"]
    assert research["payload_version"] == "equity-research/1"
    assert research["status"] == "complete"
    assert research["headline_value_per_share"] > 0
    assert research["assumption_status"] == "research_pending_approval"

    # 三情景 + 强弱排序（不是按估值高低挑模型，而是情景由假设定义）
    names = [s["name"] for s in research["scenarios"]]
    assert names == ["base", "bear", "bull"]
    assert research["valuation_range"]["low"] < research["valuation_range"]["high"]
    assert research["valuation_range"]["basis"] == "scenario_range_not_confidence_interval"

    # 终值贡献与敏感性都要出现
    assert research["scenarios"][0]["terminal_share_of_value"] is not None
    variables = {row["variable"] for row in research["sensitivity"]}
    assert {"收入增速", "折现率", "终值增长"} <= variables

    # 交叉检查来自第二条已实现路线
    assert research["cross_check"]["model_id"] == "equity_dividend_discount"
    assert research["cross_check"]["value_per_share"] > 0

    # 研究与正式决策分离
    assert result.decision == "undetermined"
    assert rc.MODEL_RESEARCH_VALUATION in result.reason_codes
    assert rc.RESEARCH_PARAMETERS_UNAPPROVED in result.reason_codes


def test_research_result_carries_fcff_bridge_and_dividend_book(maotai_result):
    data = maotai_result.payload["research_valuation"]["data_used"]
    bridge = data["fcff_bridge"]
    assert bridge["reconciled"] is True
    assert abs(bridge["reconciliation_relative"]) <= 0.15
    assert bridge["nwc_source"].startswith("现金流量表补充资料")
    assert data["dividend_book"]["fiscal_years"]
    assert data["price"]["price_type"] == "close_unadjusted"
    assert data["price"]["bar_status"] == "closed"


def test_second_real_company_completes_dividend_route(analyzer):
    result = analyzer.analyze("600900", assumptions=load_assumptions(CJDL_DDM))
    assert result.analysis_status == "complete"
    assert result.model_plan["primary_model"] == "equity_dividend_discount"
    research = result.payload["research_valuation"]
    assert research["headline_value_per_share"] > 0
    assert research["cross_check"] is None, "本轮未提供该公司 FCFF 交叉检查"
    assert any("交叉检查" in w for w in research["warnings"])
    assert validate_result(result) == []


def test_same_inputs_replay_identical_values(analyzer, maotai_result):
    again = analyzer.analyze(
        "600519",
        assumptions=load_assumptions(MAOTAI_FCFF),
        cross_check_assumptions=load_assumptions(MAOTAI_DDM),
    )
    first = maotai_result.payload["research_valuation"]
    second = again.payload["research_valuation"]
    assert first["headline_value_per_share"] == second["headline_value_per_share"]
    assert [s["value_per_share"] for s in first["scenarios"]] == [
        s["value_per_share"] for s in second["scenarios"]
    ]
    assert maotai_result.analysis_id == again.analysis_id


# --- 价格不进入模型选择或内在价值 -------------------------------------------
def test_price_change_does_not_move_selection_or_intrinsic_value(tmp_path):
    snapshot_id = SnapshotStore(SNAPSHOT_DIR).latest_for("EQUITY:600519.SH")
    assert snapshot_id is not None

    def halve_price(fact: Fact) -> Fact:
        if fact.fact_id == "price.close":
            return replace(fact, value=fact.value / 2)
        return fact

    variant_id = _write_variant(tmp_path, snapshot_id, halve_price)
    analyzer = _analyzer(tmp_path)
    assumptions = load_assumptions(MAOTAI_FCFF)

    original = _analyzer().analyze("600519", assumptions=assumptions)
    variant = analyzer.analyze("600519", assumptions=assumptions, snapshot_id=variant_id)

    assert (
        variant.model_plan["primary_model"] == original.model_plan["primary_model"]
    ), "模型选择不得随股价改变"
    assert (
        variant.payload["research_valuation"]["headline_value_per_share"]
        == original.payload["research_valuation"]["headline_value_per_share"]
    ), "内在研究估值不得随股价改变"

    before = original.payload["research_valuation"]["price_relationship"]
    after = variant.payload["research_valuation"]["price_relationship"]
    assert before["relation"] != after["relation"]
    assert after["difference_pct"] > before["difference_pct"]


def test_intraday_price_is_not_used_for_comparison(tmp_path):
    snapshot_id = SnapshotStore(SNAPSHOT_DIR).latest_for("EQUITY:600519.SH")

    def mark_intraday(fact: Fact) -> Fact:
        if fact.fact_id == "price.bar_status":
            return replace(fact, value="intraday")
        return fact

    variant_id = _write_variant(tmp_path, snapshot_id, mark_intraday)
    result = _analyzer(tmp_path).analyze(
        "600519", assumptions=load_assumptions(MAOTAI_FCFF), snapshot_id=variant_id
    )
    research = result.payload["research_valuation"]
    assert research["price"] is None
    assert research["price_relationship"] is None
    assert rc.PRICE_UNAVAILABLE_FOR_COMPARISON in result.reason_codes
    assert research["headline_value_per_share"] > 0, "没有现价时仍可给出研究价值"


# --- 反例 -------------------------------------------------------------------
def test_bank_route_stops_with_concrete_data_gap(analyzer):
    result = analyzer.analyze("601398")
    assert result.analysis_status == "insufficient_data"
    assert result.model_status is None, "未执行模型时不得声称模型已运行"
    assert result.decision == "undetermined"
    assert rc.MISSING_BANK_RISK_EVIDENCE in result.reason_codes
    assert result.model_plan["primary_model"] == "bank_dividend_discount"
    assert result.payload["research_valuation"] is None

    missing = {item["fact_id"] for item in result.missing_data}
    assert "bank.cet1_ratio" in missing
    assert "bank.npl_ratio" in missing
    assert "bank.provision_coverage" in missing
    notes = " ".join(item.get("note") or "" for item in result.missing_data)
    assert "stock_financial_abstract" in notes, "必须写明已尝试的接口"
    assert validate_result(result) == []


@pytest.mark.parametrize(
    "query,family",
    [("601318", "insurance"), ("600030", "broker"), ("601088", "cyclical")],
)
def test_unimplemented_routes_are_unsupported_not_dcf(analyzer, query, family):
    result = analyzer.analyze(query)
    assert result.analysis_status == "unsupported"
    assert rc.MODEL_FAMILY_NOT_IMPLEMENTED in result.reason_codes
    assert result.model_plan["primary_model"] is None
    assert result.model_plan["family"] == family
    assert result.model_plan["applicable_model_category"]
    assert result.payload["research_valuation"] is None
    assert result.decision == "undetermined"
    assert validate_result(result) == []


def test_equity_without_business_evidence_stops_at_insufficient(analyzer):
    """浦发银行能识别身份但没有业务证据：停在尚不能判断，不按行业直接套模型。"""
    result = analyzer.analyze("600000")
    assert result.analysis_status == "insufficient_data"
    assert rc.MISSING_BUSINESS_EVIDENCE in result.reason_codes
    assert result.payload["research_valuation"] is None
    assert validate_result(result) == []


def test_equity_without_assumptions_cannot_produce_numbers(analyzer):
    result = analyzer.analyze("600519")
    assert result.analysis_status == "insufficient_data"
    assert rc.MISSING_MODEL_INPUT in result.reason_codes
    assert result.payload["research_valuation"] is None
    assert result.model_plan["primary_model"] == "equity_dcf_fcff"


def test_assumptions_for_another_instrument_are_rejected(analyzer):
    result = analyzer.analyze("600900", assumptions=load_assumptions(MAOTAI_FCFF))
    assert result.analysis_status == "insufficient_data"
    assert result.payload["research_valuation"] is None


def test_stale_assumptions_are_rejected(analyzer):
    data = json.loads(MAOTAI_FCFF.read_text(encoding="utf-8"))
    data["applicable_as_of"] = "2026-06-30"
    stale = type(load_assumptions(MAOTAI_FCFF)).from_dict(data)
    result = analyzer.analyze("600519", assumptions=stale)
    assert result.analysis_status == "insufficient_data"
    assert rc.DATA_STALE in result.reason_codes


def test_mismatched_model_assumptions_are_rejected(analyzer):
    """主模型是 FCFF，却给了 DDM 假设：必须拒绝，不能悄悄换模型。"""
    result = analyzer.analyze("600519", assumptions=load_assumptions(MAOTAI_DDM))
    assert result.analysis_status == "insufficient_data"
    assert result.payload["research_valuation"] is None


# --- 研究与正式决策分离 -----------------------------------------------------
@pytest.mark.parametrize(
    "query",
    ["600519", "600900", "601398", "601318", "601088", "600030", "600000", "沪深300", "510300"],
)
def test_no_equity_or_product_output_is_ever_candidate(analyzer, query):
    result = analyzer.analyze(
        query,
        assumptions=load_assumptions(MAOTAI_FCFF) if query == "600519" else None,
    )
    assert result.decision not in ("candidate", "wait", "avoid")
    assert result.decision == "undetermined"
    assert validate_result(result) == []


def test_research_payload_cannot_claim_a_price_position(maotai_result):
    data = json.loads(export_json(maotai_result))
    data["valuation_state"] = "attractive"
    problems = validate_result(data)
    assert any("价格位置" in item for item in problems)


def test_research_payload_requires_research_model_status(maotai_result):
    data = json.loads(export_json(maotai_result))
    data["model_status"] = "validated"
    problems = validate_result(data)
    assert any("model_status" in item for item in problems)


def test_research_payload_cannot_be_combined_with_candidate(maotai_result):
    data = json.loads(export_json(maotai_result))
    data["decision"] = "candidate"
    problems = validate_result(data)
    assert problems


def test_json_and_text_exports_agree(maotai_result):
    text_json = export_json(maotai_result)
    parsed = parse_and_validate(text_json)
    assert parsed.analysis_id == maotai_result.analysis_id
    rendered = render_text(maotai_result.to_dict())
    for marker in (
        "【模型选择】",
        "【数据与口径】",
        "【研究估值（未获批准，不构成买点）】",
        "【交叉检查】",
        "【与现价的关系】",
        "【研究假设状态】",
    ):
        assert marker in rendered
    assert "元/股" in rendered
    assert "不构成买点" in rendered


def test_force_contract_violation_when_research_payload_has_wrong_state(maotai_result):
    broken = replace(maotai_result, valuation_state="expensive")
    with pytest.raises(ContractViolation):
        enforce(broken)


def test_summary_text_reports_missing_and_invalidation(analyzer):
    result = analyzer.analyze("601398")
    text = render_text(result.to_dict())
    assert "资料不足" in text
    assert "哪些变化会使当前结论失效" in text
    assert "bank.cet1_ratio" in text


@pytest.mark.parametrize(
    "query,needs_assumptions",
    [
        ("600519", True),
        ("600900", True),
        ("601398", False),
        ("601318", False),
        ("601088", False),
        ("600030", False),
        ("600000", False),
        ("沪深300", False),
        ("510300", False),
    ],
)
def test_text_report_renders_for_every_status(analyzer, query, needs_assumptions):
    """任何 analysis_status 都必须能渲染文本摘要（曾因 model_plan 结构变化抛 KeyError）。"""
    assumptions = None
    if query == "600519":
        assumptions = load_assumptions(MAOTAI_FCFF)
    elif query == "600900":
        assumptions = load_assumptions(CJDL_DDM)
    result = analyzer.analyze(query, assumptions=assumptions)
    text = render_text(result.to_dict())
    assert "【标的】" in text
    assert "【结论】" in text
    assert "【方法】" in text
    assert "不形成买点结论" in text
    assert validate_result(result) == []


# --- S1 / S2 反例回归（复验报告）-------------------------------------------
MAOTAI_SNAPSHOT = "EQUITY:600519.SH"


def _maotai_snapshot_id() -> str:
    snapshot_id = SnapshotStore(SNAPSHOT_DIR).latest_for(MAOTAI_SNAPSHOT)
    assert snapshot_id is not None
    return snapshot_id


def _minority_fact_id(fiscal_year: int = 2025) -> str:
    return f"fin.{fiscal_year}-12-31.minority_equity"


def test_s1_missing_minority_interest_blocks_the_model(tmp_path):
    """缺少数数股东权益时不得默认零并给出更高的完整估值。"""
    fact_id = _minority_fact_id()
    baseline = _analyzer().analyze("600519", assumptions=load_assumptions(MAOTAI_FCFF))
    baseline_value = baseline.payload["research_valuation"]["headline_value_per_share"]

    def drop_minority(fact: Fact) -> Fact:
        if fact.fact_id == fact_id:
            return Fact.missing(
                fact_id=fact.fact_id,
                metric=fact.metric,
                period=fact.period,
                unit=fact.unit,
                currency=fact.currency,
            )
        return fact

    variant_id = _write_variant(tmp_path, _maotai_snapshot_id(), drop_minority)
    result = _analyzer(tmp_path).analyze(
        "600519", assumptions=load_assumptions(MAOTAI_FCFF), snapshot_id=variant_id
    )
    assert result.analysis_status == "insufficient_data"
    assert result.payload["research_valuation"] is None
    assert rc.MISSING_MODEL_INPUT in result.reason_codes
    notes = " ".join(item.get("note") or "" for item in result.missing_data)
    assert "少数股东权益" in notes
    assert baseline_value > 0


def test_s1_zero_minority_interest_is_still_valued(tmp_path):
    """有来源的已知零必须仍可计算。"""
    fact_id = _minority_fact_id()

    def zero_minority(fact: Fact) -> Fact:
        if fact.fact_id == fact_id:
            return Fact.known_zero(
                fact_id=fact.fact_id,
                metric=fact.metric,
                period=fact.period,
                unit=fact.unit,
                currency=fact.currency,
                source=fact.source,
                available_at=fact.available_at,
                fetched_at=fact.fetched_at,
            )
        return fact

    variant_id = _write_variant(tmp_path, _maotai_snapshot_id(), zero_minority)
    result = _analyzer(tmp_path).analyze(
        "600519", assumptions=load_assumptions(MAOTAI_FCFF), snapshot_id=variant_id
    )
    assert result.analysis_status == "complete"
    research = result.payload["research_valuation"]
    assert research["headline_value_per_share"] > 0
    treatments = {
        item["item"]: item["status"]
        for item in research["scenarios"][0]["detail"]["equity_bridge_items"]
    }
    assert treatments["minority_equity"] == "sourced_zero"


def test_s1_conflicting_minority_interest_blocks_the_model(tmp_path):
    fact_id = _minority_fact_id()

    def conflict_minority(fact: Fact) -> Fact:
        if fact.fact_id == fact_id:
            return Fact.conflict(
                fact_id=fact.fact_id,
                metric=fact.metric,
                values=[9_000_000_000.0, 9_321_442_876.89],
                period=fact.period,
                unit=fact.unit,
                currency=fact.currency,
            )
        return fact

    variant_id = _write_variant(tmp_path, _maotai_snapshot_id(), conflict_minority)
    result = _analyzer(tmp_path).analyze(
        "600519", assumptions=load_assumptions(MAOTAI_FCFF), snapshot_id=variant_id
    )
    assert result.analysis_status == "insufficient_data"
    assert result.payload["research_valuation"] is None


def test_s1_bridge_policy_is_a_required_assumption_field(analyzer):
    data = json.loads(MAOTAI_FCFF.read_text(encoding="utf-8"))
    data["extras"].pop("bridge_policy")
    stripped = type(load_assumptions(MAOTAI_FCFF)).from_dict(data)
    result = analyzer.analyze("600519", assumptions=stripped)
    assert result.analysis_status == "insufficient_data"
    assert rc.MISSING_MODEL_INPUT in result.reason_codes


def _cjdl_assumption_variant(**scenario_overrides):
    from engine.assumptions.schema import AssumptionSet

    data = json.loads(CJDL_DDM.read_text(encoding="utf-8"))
    data["scenarios"]["base"].update(scenario_overrides)
    return AssumptionSet.from_dict(data)


def test_s2_excess_payout_scenario_is_refused_end_to_end(analyzer):
    """末期分配超过盈利、终值增长为零时，不能把超额分配当永续。"""
    assumptions = _cjdl_assumption_variant(
        drivers={
            "eps_growth": [-0.066, 0.03, 0.03, 0.03, 0.025],
            "payout": [1.2, 1.2, 1.2, 1.2, 1.2],
            "roe": 0.1559,
        },
        terminal_growth=0.0,
    )
    result = analyzer.analyze("600900", assumptions=assumptions)
    assert result.analysis_status == "insufficient_data"
    assert result.payload["research_valuation"] is None
    assert rc.TERMINAL_DISTRIBUTION_UNFUNDED in result.reason_codes


def test_s2_supported_terminal_split_computes_end_to_end(analyzer):
    """显式可持续终值分配 + 依据时，超额部分只进预测期，结果可计算。"""
    assumptions = _cjdl_assumption_variant(
        drivers={
            "eps_growth": [-0.066, 0.03, 0.03, 0.03, 0.025],
            "payout": [1.2, 1.2, 1.2, 1.2, 1.2],
            "roe": 0.1559,
        },
        terminal_growth=0.0,
        sustainable_terminal_dps=1.0,
        sustainable_terminal_basis="测试：超额部分由已知一次性资金支持，仅限预测期",
    )
    result = analyzer.analyze("600900", assumptions=assumptions)
    assert result.analysis_status == "complete"
    research = result.payload["research_valuation"]
    assert research["headline_value_per_share"] > 0
    scenario_detail = research["scenarios"][0]["detail"]
    assert scenario_detail["discounting"]["terminal"]["next_period_cash_flow"] == pytest.approx(
        1.0
    )
    codes = {issue["code"] for issue in scenario_detail["issues"]}
    assert "EXCESS_DISTRIBUTION_TERMINAL_SPLIT" in codes
