"""个股政策 v1 草案的验收测试（施工单 A，L1–L5）。

覆盖：
- L1 政策集中、版本化、每个参数带依据；代码不散落政策数字（改政策即改行为）；
- L2 折现率、终值、再投资一致性、情景的手算测试；600519 两模型分歧；
- L3 确定性等级与结论映射的边界条件；C 级 / partial 一律无法判断；
- L4 30 只测试集全部有结果、快照可重放；
- L5 试运行报告字段齐全（含全集分布与安全边际敏感性）。
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from engine.data.snapshot import SnapshotStore
from engine.policy.equity import EquityPolicy, PolicyError, load_policy
from engine.policy.valuation import (
    CompanyInputs,
    PolicyModelError,
    build_discount,
    growth_paths,
    normalized_risk_free,
    terminal_spec,
    value_scenario,
)
from engine.policy import grading

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "data" / "policies" / "equity-policy-v1.1-draft.json"
TRIAL_JSON = ROOT / "data" / "policy-trial" / "trial-report.json"
TRIAL_MD = ROOT / "docs" / "phase4a-equity-policy-trial.md"

CODES = (
    "600519", "000858", "600887", "603288", "000895", "600436", "000333",
    "000651", "600660", "600031", "600309", "002415", "300750", "002594",
    "600276", "600900", "601088", "601006", "600941", "601668", "601919",
    "600019", "601899", "601857", "000002", "601012", "600036", "601398",
    "601318", "600030",
)


@pytest.fixture(scope="module")
def policy() -> EquityPolicy:
    return load_policy(POLICY_PATH)


def _inputs(**overrides) -> CompanyInputs:
    base = dict(
        instrument_id="EQUITY:TEST.SH",
        base_year=2025,
        shares=100.0,
        price=10.0,
        revenue=1000.0,
        ebit_margin=0.2,
        ebit_margin_years=("2025", "2024", "2023"),
        tax_rate=0.25,
        tax_rate_years=("2025", "2024", "2023"),
        da_pct=0.05,
        capex_pct=0.05,
        nwc_intensity=0.1,
        payout=0.5,
        payout_years=(2025, 2024, 2023),
        roe_latest=0.15,
        roe_5y=0.18,
        revenue_cagr5=0.05,
        year1_growth=0.05,
        year1_basis="test",
        debt=100.0,
        debt_components={"short_loan": 100.0},
        missing_debt_components=(),
        average_debt=150.0,
        interest_expense=5.0,
        monetary_funds=100.0,
        cash_equivalents_end=40.0,
        cash_equivalents_basis="END_CCE",
        non_cash_equivalents=60.0,
        operating_cash_need=20.0,
        available_cash=20.0,
        minority_equity=0.0,
        net_profit=100.0,
        net_profit_parent=100.0,
        minority_profit=0.0,
        loan_advance=None,
        accept_deposit_interbank=None,
        total_assets=2000.0,
        ocf_5y=600.0,
        profit_5y=500.0,
        loss_years=0,
        available_years=11,
        ebitda=300.0,
        notes=(),
        loss_missing_10=(),
        loss_missing_7=(),
        ocf_missing_years=(),
        dividend_missing_years=(),
        payout_missing_years=(),
    )
    base.update(overrides)
    return CompanyInputs(**base)


# --- L1 ---------------------------------------------------------------------
def test_policy_is_versioned_draft_and_cannot_self_approve(policy):
    assert policy.policy_id == "equity-policy-v1.1-draft"
    assert policy.status == "draft_unapproved"
    assert policy.approved is False
    assert policy.policy_digest.startswith("pol-")

    tampered = copy.deepcopy(policy.data)
    tampered["approved"] = True
    problems = EquityPolicy(data=tampered, path="mem").validate()
    assert any("approved" in problem for problem in problems)

    draft = copy.deepcopy(policy.data)
    draft["status"] = "approved"
    problems = EquityPolicy(data=draft, path="mem").validate()
    assert any("status" in problem for problem in problems)


def test_every_parameter_has_source_or_reason(policy):
    """政策树里的每个参数叶子都必须能说出「为什么是这个数」。"""
    missing: list[str] = []

    def walk(node, path):
        if isinstance(node, dict):
            is_leaf = "basis" in node or "source" in node
            if is_leaf:
                if not (node.get("basis") or node.get("source")):
                    missing.append(path)
                return
            for key, value in node.items():
                walk(value, f"{path}.{key}")

    walk(policy.data["parameters"], "parameters")
    assert missing == []


def test_policy_module_has_no_hardcoded_policy_numbers():
    """模块源码不得出现政策值；阈值只能来自 JSON。"""
    for name in ("equity.py", "valuation.py", "grading.py", "trial.py"):
        source = (ROOT / "engine" / "policy" / name).read_text(encoding="utf-8")
        for literal in ("0.0514", "0.40", "0.25", "0.015", "0.025", "0.8", "0.6"):
            assert literal not in source, f"{name} 出现政策数字 {literal}"


def test_changing_policy_changes_behavior(policy):
    """把 A 级安全边际改成 30%，同级结果必须立刻变成 30%：证明代码读政策而不是写死。"""
    data = copy.deepcopy(policy.data)
    data["parameters"]["grade"]["A"]["margin_of_safety"] = 0.30
    changed = EquityPolicy(data=data, path="mem")
    inputs = _inputs()
    result = grading.evaluate_grade(
        changed,
        inputs=inputs,
        base_value=100.0,
        divergence=0.10,
        approximation_total_per_share=1.0,
    )
    assert result.grade == "A"
    assert result.margin == pytest.approx(0.30)
    original = grading.evaluate_grade(
        policy,
        inputs=inputs,
        base_value=100.0,
        divergence=0.10,
        approximation_total_per_share=1.0,
    )
    assert original.margin == pytest.approx(0.25)


def test_missing_parameter_path_raises(policy):
    with pytest.raises(PolicyError):
        policy.value("discount_rate.not_a_parameter")


# --- L2：折现率 --------------------------------------------------------------
def test_normalized_risk_free_is_max_of_latest_and_ten_year_mean(policy):
    observations = []
    for index in range(120):
        year = 2016 + index // 12
        month = index % 12 + 1
        observations.append({"date": f"{year}-{month:02d}-28", "value_pct": 3.0})
    macro = {
        "series_id": "macro-test",
        "latest": {"date": "2026-11-11", "value_pct": 1.60},
        "month_end_observations": observations,
    }
    result = normalized_risk_free(policy, macro)
    assert result["month_end_mean"]["months"] == 120
    assert result["normalized_pct"] == pytest.approx(3.0)
    assert result["chosen_because"] == "month_end_mean"
    assert result["normalized"] == pytest.approx(0.03)


def test_discount_rate_build_matches_hand_calculation(policy):
    inputs = _inputs()
    discount = build_discount(policy, inputs, risk_free=0.0276)
    ke = 0.0276 + 0.0514
    assert discount.cost_of_equity == pytest.approx(ke)
    assert discount.cost_of_debt == pytest.approx(5.0 / 150.0)
    market_cap = inputs.price * inputs.shares
    net_debt = max(0.0, inputs.debt - inputs.available_cash)
    equity_weight = market_cap / (market_cap + net_debt)
    expected_wacc = (
        equity_weight * ke
        + (1 - equity_weight) * (5.0 / 150.0) * (1 - 0.25)
    )
    assert discount.equity_weight == pytest.approx(equity_weight)
    assert discount.wacc == pytest.approx(expected_wacc)
    assert discount.company_premium == 0.0


def test_cost_of_debt_floors_at_risk_free(policy):
    inputs = _inputs(interest_expense=1.0, average_debt=1000.0)  # 0.1% < rf
    discount = build_discount(policy, inputs, risk_free=0.0276)
    assert discount.cost_of_debt == pytest.approx(0.0276)
    assert any("下限" in note for note in discount.notes)


# --- L2：增长与情景 ----------------------------------------------------------
def test_growth_paths_use_disclosed_year_one_and_capped_cagr(policy):
    inputs = _inputs(year1_growth=0.05, revenue_cagr5=0.12)
    paths = growth_paths(policy, inputs)
    # R7：稳态 = min(10%, 5y CAGR)；第 2–5 年从第 1 年线性过渡到稳态
    assert paths["base"] == pytest.approx([0.05, 0.0625, 0.075, 0.0875, 0.10])
    assert paths["pessimistic"] == pytest.approx([0.03, 0.0425, 0.055, 0.0675, 0.08])
    assert paths["optimistic"] == pytest.approx([0.07, 0.0825, 0.095, 0.1075, 0.12])


def test_terminal_growth_is_capped_by_roe_and_retention(policy):
    inputs = _inputs(roe_latest=0.01, payout=0.5)  # 上限 = 0.5%
    terminal = terminal_spec(
        policy, inputs, scenario="base", risk_free=0.0276, cost_of_equity=0.079
    )
    assert terminal.growth == pytest.approx(0.005)
    assert "roe_retention" in terminal.capped_by
    assert terminal.reinvestment_rate == pytest.approx(0.005 / 0.079)
    assert terminal.payout_rate == pytest.approx(1 - 0.005 / 0.079)


def test_terminal_growth_is_capped_by_risk_free(policy):
    inputs = _inputs()
    terminal = terminal_spec(
        policy, inputs, scenario="base", risk_free=0.01, cost_of_equity=0.0614
    )
    assert terminal.growth == pytest.approx(0.01)
    assert "growth_cap" in terminal.capped_by


def test_optimistic_scenario_uses_five_year_average_roe(policy):
    inputs = _inputs(roe_5y=0.20)
    terminal = terminal_spec(
        policy, inputs, scenario="optimistic", risk_free=0.0276, cost_of_equity=0.079
    )
    assert terminal.new_investment_return == pytest.approx(0.20)
    assert terminal.reinvestment_rate == pytest.approx(0.025 / 0.20)


# --- L2：FCFF 手算 ------------------------------------------------------------
def test_fcff_value_matches_hand_calculation(policy):
    inputs = _inputs()
    discount = build_discount(policy, inputs, risk_free=0.0276)
    ke = discount.cost_of_equity
    wacc = discount.wacc
    growth = 0.05
    g = 0.02
    revenue = inputs.revenue
    flows = []
    for _ in range(5):
        prior = revenue
        revenue = prior * (1 + growth)
        nopat = revenue * inputs.ebit_margin * (1 - inputs.tax_rate)
        fcff = (
            nopat
            + revenue * inputs.da_pct
            - revenue * inputs.capex_pct
            - (revenue - prior) * inputs.nwc_intensity
        )
        flows.append(fcff)
        last_nopat = nopat
    terminal_cf = last_nopat * (1 + g) * (1 - g / wacc)
    terminal_value = terminal_cf / (wacc - g)
    explicit = sum(cf / (1 + wacc) ** (i + 1) for i, cf in enumerate(flows))
    ev = explicit + terminal_value / (1 + wacc) ** 5
    equity = ev - inputs.debt + inputs.available_cash - inputs.minority_equity
    expected = equity / inputs.shares

    outcome = value_scenario(
        policy,
        model_id="equity_dcf_fcff",
        inputs=inputs,
        discount=discount,
        scenario="base",
        growth=[growth] * 5,
        risk_free=0.0276,
    )
    assert outcome.value_per_share == pytest.approx(expected, rel=1e-9)
    assert outcome.terminal.reinvestment_rate == pytest.approx(g / wacc)
    assert outcome.terminal.new_investment_return == pytest.approx(wacc)


def test_dividend_value_matches_hand_calculation(policy):
    inputs = _inputs()
    discount = build_discount(policy, inputs, risk_free=0.0276)
    ke = discount.cost_of_equity
    growth = 0.05
    g = 0.02
    eps = inputs.net_profit_parent / inputs.shares
    cash_flows = []
    for _ in range(5):
        eps = eps * (1 + growth)
        cash_flows.append(eps * inputs.payout)
    terminal_dps = eps * (1 + g) * (1 - g / ke)
    terminal_value = terminal_dps / (ke - g)
    expected = sum(cf / (1 + ke) ** (i + 1) for i, cf in enumerate(cash_flows)) + (
        terminal_value / (1 + ke) ** 5
    )
    outcome = value_scenario(
        policy,
        model_id="equity_dividend_discount",
        inputs=inputs,
        discount=discount,
        scenario="base",
        growth=[growth] * 5,
        risk_free=0.0276,
    )
    assert outcome.value_per_share == pytest.approx(expected, rel=1e-9)


def test_terminal_payout_equals_one_minus_g_over_roi(policy):
    inputs = _inputs()
    discount = build_discount(policy, inputs, risk_free=0.0276)
    outcome = value_scenario(
        policy,
        model_id="equity_dcf_fcff",
        inputs=inputs,
        discount=discount,
        scenario="base",
        growth=[0.05] * 5,
        risk_free=0.0276,
    )
    terminal = outcome.terminal
    assert terminal.payout_rate == pytest.approx(1 - terminal.growth / terminal.new_investment_return)
    # 基准情景新投资回报率 = 股权成本时，终值公式里的 (1−g/Ke) 与 (1+g)/(WACC−g)
    # 仍然必须按显式公式计算；当 WACC = Ke 时它退化为末期 NOPAT / Ke（价值中性增长）。
    nopat_last = outcome.detail["years"][-1]["nopat"]
    expected_terminal = (
        nopat_last * (1 + terminal.growth) * (1 - terminal.growth / discount.wacc)
        / (discount.wacc - terminal.growth)
    )
    assert outcome.detail["discounting"]["terminal"]["terminal_value"] == pytest.approx(
        expected_terminal
    )


# --- L2：600519 两模型分歧 ----------------------------------------------------
def test_maotai_two_model_divergence_under_new_rules():
    if not TRIAL_JSON.exists():
        pytest.skip("试运行报告尚未生成")
    payload = json.loads(TRIAL_JSON.read_text(encoding="utf-8"))
    record = next(item for item in payload["records"] if item["code"] == "600519")
    assert record["status"] == "complete"
    assert record["values"]["base"] > 0
    cross = record["cross_check"]
    assert cross["model_id"] == "equity_dividend_discount"
    assert cross["value_per_share"] > 0
    expected = cross["value_per_share"] / record["values"]["base"] - 1
    assert cross["divergence"] == pytest.approx(expected)
    assert abs(cross["divergence"]) < 0.24  # 阶段二约 24% 的分歧必须缩小
    markdown = TRIAL_MD.read_text(encoding="utf-8")
    assert "两模型分歧" in markdown
    assert "终值再投资率" in markdown


# --- L3：等级 -----------------------------------------------------------------
def _grade(policy, **overrides):
    divergence = overrides.pop("divergence", 0.10)
    approximation = overrides.pop("approximation", 1.0)
    unquantified = overrides.pop("unquantified", ())
    family = overrides.pop("family", "non_financial")
    bank_evidence_ok = overrides.pop("bank_evidence_ok", False)
    inputs = _inputs(**overrides)
    return grading.evaluate_grade(
        policy,
        inputs=inputs,
        base_value=100.0,
        divergence=divergence,
        approximation_total_per_share=approximation,
        approximation_unquantified=unquantified,
        family=family,
        bank_evidence_ok=bank_evidence_ok,
    )


def test_grade_a_when_all_conditions_pass(policy):
    result = _grade(policy, divergence=0.10, approximation=1.0)
    assert result.grade == "A"
    assert result.margin == pytest.approx(0.25)
    assert all(condition["passed"] for condition in (c.to_dict() for c in result.conditions_a))


def test_grade_b_when_ocf_ratio_between_thresholds(policy):
    result = _grade(policy, ocf_5y=300.0, divergence=0.10, approximation=1.0)
    assert result.grade == "B"
    assert result.margin == pytest.approx(0.40)


def test_grade_c_when_ocf_ratio_below_b(policy):
    result = _grade(policy, ocf_5y=250.0, divergence=0.10, approximation=1.0)
    assert result.grade == "C"
    assert result.margin is None


def test_grade_c_when_approximation_cannot_be_quantified(policy):
    result = _grade(
        policy,
        divergence=0.10,
        approximation=None,
        unquantified=("restricted_cash",),
    )
    assert result.grade == "C"


def test_grade_c_when_cross_check_unavailable(policy):
    inputs = _inputs()
    result = grading.evaluate_grade(
        policy,
        inputs=inputs,
        base_value=100.0,
        divergence=None,
        approximation_total_per_share=1.0,
    )
    assert result.grade == "C"
    assert "cross_check_divergence" in result.unmet


def test_grade_b_uses_seven_year_loss_window(policy):
    # 10 年内有 2 年亏损（8–9 年前），但最近 7 年无亏损：A 不满足，B 满足
    result = _grade(policy, loss_years=2, loss_years_7=0)
    assert result.grade == "B"
    # 最近 7 年有 2 年亏损：B 也不满足
    assert _grade(policy, loss_years=2, loss_years_7=2).grade == "C"


def test_grade_boundary_net_debt_to_ebitda(policy):
    # (100 - 20) / 300 = 0.267 < 2 → A 条件通过
    assert _grade(policy).grade == "A"
    # (100 - 20) / 40 = 2.0 → 不满足 A 的「< 2」，但满足 B 的「< 3」
    result = _grade(policy, ebitda=40.0)
    assert result.grade == "B"


def test_bank_without_risk_evidence_cannot_exceed_c(policy):
    result = _grade(policy, family="bank", bank_evidence_ok=False)
    assert result.grade == "C"
    assert "bank_risk_evidence" in result.unmet


def test_grade_c_has_no_margin_and_cannot_conclude(policy):
    assert policy.node("grade.C")["margin_of_safety"] is None
    result = grading.conclude(
        policy, grade="C", status="complete", price=1.0, values=_VALUES, margin=None
    )
    assert result["conclusion"] == "undetermined"


# --- L3：结论映射 -------------------------------------------------------------
_VALUES = {"pessimistic": 100.0, "base": 120.0, "optimistic": 150.0}


def _conclude(policy, price, *, grade="A", status="complete", values=_VALUES):
    return grading.conclude(
        policy, grade=grade, status=status, price=price, values=values, margin=0.25
    )


def test_conclusion_boundaries(policy):
    assert _conclude(policy, 90.0)["conclusion"] == "candidate_buy_point"  # 恰好等于 120×0.75
    assert _conclude(policy, 90.01)["conclusion"] == "wait"
    assert _conclude(policy, 100.0)["conclusion"] == "wait"  # ≤ 悲观价值但不是买点
    assert _conclude(policy, 150.0)["conclusion"] == "wait"  # 恰好等于乐观价值
    assert _conclude(policy, 150.01)["conclusion"] == "avoid"


def test_conclusion_requires_pessimistic_condition(policy):
    values = {"pessimistic": 80.0, "base": 120.0, "optimistic": 150.0}
    # 价格低于 base×(1−M)=90 但高于悲观价值 80 → 不是候选买点
    assert _conclude(policy, 85.0, values=values)["conclusion"] == "wait"


def test_grade_c_and_partial_are_always_undetermined(policy):
    for kwargs in (
        {"grade": "C", "status": "complete"},
        {"grade": "A", "status": "partial"},
        {"grade": "B", "status": "insufficient_data"},
        {"grade": "A", "status": "unsupported"},
    ):
        assert _conclude(policy, 90.0, **kwargs)["conclusion"] == "undetermined"


def test_margin_sensitivity_grid_covers_both_policy_ranges(policy):
    rows = grading.margin_sensitivity(
        policy, grade="A", status="complete", price=90.0, values=_VALUES
    )
    assert [row["margin_of_safety"] for row in rows] == pytest.approx(
        [0.20, 0.25, 0.30, 0.35, 0.40, 0.45]
    )
    conclusions = {row["margin_of_safety"]: row["conclusion"] for row in rows}
    assert conclusions[0.20] == "candidate_buy_point"  # 120×0.8 = 96 ≥ 90
    assert conclusions[0.25] == "candidate_buy_point"  # 120×0.75 = 90，恰好相等
    assert conclusions[0.30] == "wait"  # 120×0.7 = 84 < 90


# --- L4 / L5：试运行报告 ------------------------------------------------------
@pytest.fixture(scope="module")
def trial() -> dict:
    if not TRIAL_JSON.exists():
        pytest.skip("试运行报告尚未生成")
    return json.loads(TRIAL_JSON.read_text(encoding="utf-8"))


def test_trial_covers_all_thirty_codes(trial):
    codes = [record["code"] for record in trial["records"]]
    assert len(codes) == 30
    assert set(codes) == set(CODES)
    assert all(record.get("status") in {"complete", "partial", "insufficient_data", "unsupported"} for record in trial["records"])
    assert all(record.get("snapshot_id") for record in trial["records"])


def test_trial_snapshots_replay(trial):
    store = SnapshotStore(ROOT / "data" / "policy-trial" / "snapshots")
    for record in trial["records"]:
        snapshot = store.load(record["snapshot_id"])
        assert snapshot.instrument_id == record["instrument_id"]
        assert snapshot.as_of == record["as_of"]


def test_trial_complete_records_have_full_fields(trial):
    complete = [record for record in trial["records"] if record["status"] == "complete"]
    assert complete, "至少应有一条完整记录"
    for record in complete:
        assert record["values"]["base"] > 0
        assert record["values"]["pessimistic"] <= record["values"]["base"] <= record["values"]["optimistic"]
        assert record["grade"]["grade"] in ("A", "B", "C")
        assert record["trial_conclusion"]["conclusion"] in (
            "candidate_buy_point", "wait", "avoid", "undetermined"
        )
        assert record["trial_conclusion"]["is_trial_only"] is True
        assert len(record["sensitivity"]) == 4
        assert {row["variable"] for row in record["sensitivity"]} == {
            "discount_rate", "terminal_growth"
        }
        assert record["approximations"]["items"]
        assert record["discount"]["cost_of_equity"] > 0


def test_trial_distribution_and_margin_sensitivity(trial):
    distribution = trial["distribution"]
    assert sum(distribution["conclusions"].values()) == 30
    assert sum(distribution["grades"].values()) == 30
    margins = [row["margin_of_safety"] for row in trial["margin_sensitivity"]]
    assert margins == pytest.approx([0.20, 0.25, 0.30, 0.35, 0.40, 0.45])
    for row in trial["margin_sensitivity"]:
        assert row["candidate_buy_point"] + row["wait"] + row["avoid"] + row["undetermined"] == 30


def test_policy_carries_designer_decisions_and_guardrail(policy):
    """M3/M4：设计决定、基期异常阈值与杠杆护栏都要在政策文件里。"""
    decisions = policy.data.get("designer_decisions_2026_11_12") or []
    assert len(decisions) == 7
    assert any("杠杆" in item["item"] for item in decisions)
    assert policy.value("quality.leverage_guardrail") == pytest.approx(0.50)
    assert policy.node("quality.leverage_guardrail")["basis"]
    assert policy.value("reporting.base_year_anomaly.revenue_yoy_abs") == pytest.approx(0.40)
    assert policy.value("reporting.base_year_anomaly.profit_yoy_abs") == pytest.approx(0.60)


def test_trial_base_year_anomaly_list(trial):
    """M3：基期异常变动清单列出 000858 及其两个同比数值。"""
    record = next(item for item in trial["records"] if item["code"] == "000858")
    change = record["base_year_change"]
    assert change["fiscal_year"] == 2025
    assert change["revenue"] == pytest.approx(40528509770.23)
    assert change["prior_revenue"] == pytest.approx(89175178322.7)
    assert change["revenue_yoy"] == pytest.approx(40528509770.23 / 89175178322.7 - 1)
    assert change["revenue_yoy"] < -0.40
    assert change["profit_yoy"] < -0.60
    if TRIAL_MD.exists():
        text = TRIAL_MD.read_text(encoding="utf-8")
        assert "基期异常变动清单" in text
        assert "| 000858 |" in text


def test_trial_leverage_guardrail_forces_c(trial):
    """M4：601668 超过 50% 护栏，状态 partial、等级 C、结论无法判断，但价值仍展示。"""
    record = next(item for item in trial["records"] if item["code"] == "601668")
    guardrail = record["leverage_guardrail"]
    assert guardrail["exceeded"] is True
    assert guardrail["ratio"] > 0.50
    assert record["status"] == "partial"
    assert record["grade"]["grade"] == "C"
    assert record["trial_conclusion"]["conclusion"] == "undetermined"
    assert "LEVERAGE_GUARDRAIL_EXCEEDED" in record["reason_codes"]
    assert record["values"]["base"] > 0  # 研究价值仍可展示
    assert any("杠杆" in note for note in record["grade"]["notes"])


def test_delivery_documents_independent_verification_of_000858():
    """M3(a)：交付文档必须写明独立来源核对结果。"""
    text = (ROOT / "docs" / "phase4a-delivery.md").read_text(encoding="utf-8")
    assert "000858" in text and "已核对" in text
    assert "405.29" in text and "89.54" in text
    assert "同花顺" in text


def test_trial_markdown_contains_required_sections():
    if not TRIAL_MD.exists():
        pytest.skip("试运行报告尚未生成")
    text = TRIAL_MD.read_text(encoding="utf-8")
    for keyword in (
        "政策版本与折现率",
        "测试集与数据",
        "预演结果",
        "全集分布",
        "安全边际敏感性",
        "两模型分歧",
        "复现与产物",
    ):
        assert keyword in text
    assert "候选买点数量不作为验收条件" in text


# === R1–R14（施工单 4a-R，N1 每项至少一个测试） =============================
from engine.policy.trial import (  # noqa: E402
    _approximations,
    base_year_anomaly,
    continuity_guardrail_reasons,
)


def test_r1_fcff_terminal_roi_is_wacc_and_growth_cancels(policy):
    inputs = _inputs()
    discount = build_discount(policy, inputs, risk_free=0.0276)
    outcome = value_scenario(
        policy,
        model_id="equity_dcf_fcff",
        inputs=inputs,
        discount=discount,
        scenario="base",
        growth=[0.05] * 5,
        risk_free=0.0276,
    )
    assert outcome.terminal.new_investment_return == pytest.approx(discount.wacc)
    # ROI = WACC 时终值化简为末期 NOPAT ÷ WACC，g 抵消
    nopat_last = outcome.detail["years"][-1]["nopat"]
    assert outcome.detail["discounting"]["terminal"]["terminal_value"] == pytest.approx(
        nopat_last * (1 + outcome.terminal.growth) / discount.wacc
    )
    plus = value_scenario(
        policy,
        model_id="equity_dcf_fcff",
        inputs=inputs,
        discount=discount,
        scenario="base",
        growth=[0.05] * 5,
        risk_free=0.0276,
        terminal_growth_override=0.025,
    )
    assert abs(plus.value_per_share / outcome.value_per_share - 1) < 0.01


def test_r2_continuity_guardrail_boundaries(policy):
    base = dict(
        policy=policy,
        route_conclusions=["fcff_primary"],
        model_id="equity_dcf_fcff",
        explicit_cash_sum=1.0,
        terminal_share=0.8999,
    )
    assert continuity_guardrail_reasons(**base) == []
    hits = continuity_guardrail_reasons(
        **{**base, "route_conclusions": ["fcff_primary_by_default"], "explicit_cash_sum": 0.0, "terminal_share": 0.9}
    )
    assert {hit["code"] for hit in hits} == {
        "TERMINAL_GUARDRAIL_BY_DEFAULT",
        "TERMINAL_GUARDRAIL_EXPLICIT_CASH",
        "TERMINAL_GUARDRAIL_TERMINAL_SHARE",
    }
    # 股息路线不适用「显式期 FCFF ≤ 0」，但终值占比仍适用
    hits = continuity_guardrail_reasons(
        **{**base, "model_id": "equity_dividend_discount", "explicit_cash_sum": -1.0, "terminal_share": 0.95}
    )
    assert [hit["code"] for hit in hits] == ["TERMINAL_GUARDRAIL_TERMINAL_SHARE"]


def test_r2_wanhua_triggers_all_three_and_becomes_partial(trial):
    record = next(item for item in trial["records"] if item["code"] == "600309")
    assert record["status"] == "partial"
    assert record["grade"]["grade"] == "C"
    assert record["trial_conclusion"]["conclusion"] == "undetermined"
    assert set(record["reason_codes"]) >= {
        "TERMINAL_GUARDRAIL_BY_DEFAULT",
        "TERMINAL_GUARDRAIL_EXPLICIT_CASH",
        "TERMINAL_GUARDRAIL_TERMINAL_SHARE",
    }


def test_r3_maotai_cash_uses_end_cce_only(trial):
    record = next(item for item in trial["records"] if item["code"] == "600519")
    inputs = record["inputs"]
    assert inputs["cash_equivalents_end"] == pytest.approx(126425609447.72)
    assert inputs["non_cash_equivalents"] == pytest.approx(0.0)
    store = SnapshotStore(ROOT / "data" / "policy-trial" / "snapshots")
    snapshot = store.load(store.latest_for("EQUITY:600519.SH"))
    balance = snapshot.fact("fin.2025-12-31.cash_equivalents_end")
    component = snapshot.fact("fin.2025-12-31.cash_equivalents_component")
    assert balance is not None and component is not None
    assert balance.value == pytest.approx(126425609447.72)
    assert component.value == pytest.approx(8472980000.0)
    assert snapshot.coverage["cash_mapping"]["rule"] == "END_CCE_only"


def test_r4_approximation_direction(policy):
    inputs = _inputs(
        non_cash_equivalents=60.0,
        minority_equity=0.0,
        minority_profit=0.0,
        net_profit=100.0,
    )
    result = _approximations(
        policy, inputs=inputs, model_id="equity_dcf_fcff", base_value=10.0, bridge=None
    )
    non_cash = next(item for item in result["items"] if item["name"] == "non_cash_equivalents")
    assert non_cash["direction"] == "disclose_only"
    assert result["counted_total_per_share"] == pytest.approx(0.0)
    assert result["disclosed_total_per_share"] == pytest.approx(0.6)

    # 少数股东隐含值高于账面 → 计入；低于/等于账面 → 只披露
    counted = _inputs(
        non_cash_equivalents=0.0,
        minority_equity=10.0,
        minority_profit=10.0,
        net_profit=110.0,
        net_profit_parent=100.0,
    )
    up = _approximations(
        policy, inputs=counted, model_id="equity_dcf_fcff", base_value=10.0, bridge=None
    )
    minority = next(item for item in up["items"] if item["name"] == "minority_interest")
    assert minority["direction"] == "counted"
    assert up["counted_total_per_share"] > 0

    equal = _inputs(
        non_cash_equivalents=0.0,
        minority_equity=10.0,
        minority_profit=0.990099,
        net_profit=100.990099,
        net_profit_parent=100.0,
    )
    flat = _approximations(
        policy, inputs=equal, model_id="equity_dcf_fcff", base_value=10.0, bridge=None
    )
    minority = next(item for item in flat["items"] if item["name"] == "minority_interest")
    assert minority["direction"] == "disclose_only"


def test_r5_minority_has_no_fifteen_percent_trigger(policy):
    # 少数股东损益占比 9.1%（< 原 15% 触发线），隐含值高于账面时仍计入
    inputs = _inputs(
        non_cash_equivalents=0.0,
        minority_equity=10.0,
        minority_profit=10.0,
        net_profit=110.0,
        net_profit_parent=100.0,
    )
    result = _approximations(
        policy, inputs=inputs, model_id="equity_dcf_fcff", base_value=10.0, bridge=None
    )
    minority = next(item for item in result["items"] if item["name"] == "minority_interest")
    assert minority["minority_profit_share"] == pytest.approx(10 / 110)
    assert minority["direction"] == "counted"


def test_r6_dividend_route_bridge_and_coverage(policy):
    inputs = _inputs(dividend_3y_total=200.0, ocf_capex_3y_total=100.0)
    result = grading.evaluate_grade(
        policy,
        inputs=inputs,
        base_value=100.0,
        divergence=0.10,
        approximation_total_per_share=0.0,
        model_id="equity_dividend_discount",
    )
    coverage = next(c for c in result.conditions_b if c.name == "dividend_coverage")
    assert coverage.passed is False
    assert result.grade == "C"
    # 股息路线同样出现勾稽检查：片仔癀因 P8 勾稽超限转 partial
    payload = json.loads(TRIAL_JSON.read_text(encoding="utf-8"))
    record = next(item for item in payload["records"] if item["code"] == "600436")
    assert record["status"] == "partial"
    assert "BRIDGE_RECONCILIATION_GAP" in record["reason_codes"]


def test_r7_growth_cap_steady_growth_and_linear_interpolation(policy):
    inputs = _inputs(year1_growth=0.20, revenue_cagr5=0.12, revenue_cagr3=0.02)
    paths = growth_paths(policy, inputs)
    assert paths["base"] == pytest.approx([0.10, 0.08, 0.06, 0.04, 0.02])
    # 边界：3 年复合增速高于 5 年时取 5 年；第 1 年等于上限时不改
    inputs = _inputs(year1_growth=0.10, revenue_cagr5=0.05, revenue_cagr3=0.09)
    assert growth_paths(policy, inputs)["base"] == pytest.approx(
        [0.10, 0.0875, 0.075, 0.0625, 0.05]
    )


def test_r8_pessimistic_uses_minimum_margin(policy):
    inputs = _inputs(ebit_margin=0.20, ebit_margin_min_5y=0.10)
    discount = build_discount(policy, inputs, risk_free=0.0276)
    outlook = value_scenario(
        policy,
        model_id="equity_dcf_fcff",
        inputs=inputs,
        discount=discount,
        scenario="pessimistic",
        growth=[0.05] * 5,
        risk_free=0.0276,
    )
    assert outlook.detail["drivers"]["ebit_margin"] == pytest.approx(0.10)
    assert outlook.detail["drivers"]["ebit_margin_basis"] == "min_last_5_fy"

    dividend = _inputs(
        net_margin_base=0.20, net_margin_min_5y=0.10, net_profit_parent=100.0
    )
    payout = value_scenario(
        policy,
        model_id="equity_dividend_discount",
        inputs=dividend,
        discount=build_discount(policy, dividend, risk_free=0.0276),
        scenario="pessimistic",
        growth=[0.05] * 5,
        risk_free=0.0276,
    )
    assert payout.detail["drivers"]["eps_margin_ratio"] == pytest.approx(0.5)


def test_r9_net_debt_weights_and_net_cash_wacc(policy):
    inputs = _inputs(debt=100.0, available_cash=100.0)
    discount = build_discount(policy, inputs, risk_free=0.0276)
    assert discount.net_debt == 0.0
    assert discount.debt_weight == 0.0
    assert discount.wacc == pytest.approx(discount.cost_of_equity)
    # 边界：净债务恰好为 0 时仍按净现金处理
    inputs = _inputs(debt=100.0, available_cash=150.0)
    assert build_discount(policy, inputs, risk_free=0.0276).net_debt == 0.0


def test_r9_leverage_guardrail_uses_net_debt_and_reports_three_thresholds(trial):
    record = next(item for item in trial["records"] if item["code"] == "601668")
    guardrail = record["leverage_guardrail"]
    assert guardrail["ratio"] == pytest.approx(6230.0 / (6230.0 + 1884.0), rel=1e-2)
    assert [row["threshold"] for row in guardrail["sensitivity"]] == [0.3, 0.4, 0.5]
    assert all(row["exceeded"] for row in guardrail["sensitivity"])


def test_r10_payout_total_basis_fixes_byd_split(trial):
    record = next(item for item in trial["records"] if item["code"] == "002594")
    assert record["inputs"]["payout"] == pytest.approx(0.229, abs=0.005)
    # v1 用「每股派息 ÷（净利润 ÷ 当前股本）」得到 64.7%，本版必须不再出现
    assert record["inputs"]["payout"] < 0.30


def test_r11_base_year_anomaly_boundaries_and_restatement(policy):
    assert base_year_anomaly(policy, revenue_yoy=0.3999, profit_yoy=0.5999) == []
    assert base_year_anomaly(policy, revenue_yoy=-0.40, profit_yoy=None) == []
    assert len(base_year_anomaly(policy, revenue_yoy=-0.4001, profit_yoy=None)) == 1
    assert len(base_year_anomaly(policy, revenue_yoy=None, profit_yoy=-0.6001)) == 1

    payload = json.loads(TRIAL_JSON.read_text(encoding="utf-8"))
    record = next(item for item in payload["records"] if item["code"] == "000858")
    assert record["status"] == "partial"
    assert "BASE_YEAR_ANOMALY" in record["reason_codes"]
    store = SnapshotStore(ROOT / "data" / "policy-trial" / "snapshots")
    snapshot = store.load(store.latest_for("EQUITY:000858.SZ"))
    revenue = snapshot.fact("fin.2025-09-30.revenue")
    assert revenue.restated is True
    assert revenue.available_at == "2026-04-30"
    assert "609.45" in revenue.notes


def test_r12_report_presentation():
    text = TRIAL_MD.read_text(encoding="utf-8")
    for keyword in (
        "A/B/C 衡量的是数据完整度与模型稳定性",
        "「回避」指现价高于乐观情景价值",
        "仅研究参考，不用于结论",
        "终值占比",
        "候选买点出现条件",
        "v1 → v1.1 逐只对照",
        "起约束条件",
        "无风险利率的预期漂移",
    ):
        assert keyword in text, keyword


def test_r13_missing_window_fails_condition(trial):
    record = next(item for item in trial["records"] if item["code"] == "600941")
    assert record["inputs"]["loss_missing_10"]
    condition = next(
        c for c in record["grade"]["conditions_A"] if c["name"] == "loss_years"
    )
    assert condition["passed"] is False
    assert "窗口内缺少年份" in condition["note"]
    # B 级 7 年窗口完整，仍可到 B
    assert record["grade"]["grade"] == "B"


def test_r14_debt_cost_display_below_one_percent(policy):
    inputs = _inputs(debt=1.0, available_cash=0.0, price=100.0, shares=1000.0)
    discount = build_discount(policy, inputs, risk_free=0.0276)
    assert discount.debt_weight < 0.01
    assert discount.cost_of_debt_display == "不适用（债务可忽略）"
    # 计算仍取下限/观测值，不因显示而改变
    assert discount.cost_of_debt > 0


def test_r12_threshold_distance_alert_boundary(policy):
    def grade_at(divergence):
        return grading.evaluate_grade(
            policy,
            inputs=_inputs(),
            base_value=100.0,
            divergence=divergence,
            approximation_total_per_share=0.0,
        )

    at_ten = grade_at(0.36)  # (0.40 − 0.36) / 0.40 = 10.0%，不标临界
    assert at_ten.grade == "B"
    assert at_ten.nearest["condition"] == "cross_check_divergence"
    assert at_ten.nearest["distance"] == pytest.approx(0.10)
    assert at_ten.nearest["alert"] is False

    below_ten = grade_at(0.361)  # 9.75%，标临界
    assert below_ten.nearest["alert"] is True
    assert below_ten.nearest["label"] == "临界"
