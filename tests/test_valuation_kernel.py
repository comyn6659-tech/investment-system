"""折现内核、FCFF 桥接、股息内核、银行约束的独立数学验证。

全部为手算可核对的数值，不依赖快照或数据源：
- 单期/多期现值；
- 常数正现金流 + 零增长终值退化为永续年金现值；
- 折现率上升不得提高价值；
- 权益桥接的符号、单位与重复计数；
- 每 10 股 → 每股转换、特别分配不永续、超额分配与增长一致性；
- 银行资本/拨备约束与资本一致性派息上限；
- 非法终值边界与非有限数值被拒绝。
"""

from __future__ import annotations

import math

import pytest

from engine.financials.annual import AnnualFinancials
from engine.financials.bridge import build_equity_bridge
from engine.financials.dividends import DividendRecord, build_dividend_book
from engine.valuation.bank import (
    BankDividendDrivers,
    BankRiskEvidence,
    require_risk_evidence,
    value_bank_dividend,
)
from engine.valuation.ddm import DividendDrivers, value_dividend
from engine.valuation.discounting import (
    TerminalValue,
    annuity_present_value,
    discount_stream,
    growing_perpetuity_present_value,
    perpetuity_present_value,
    present_value,
)
from engine.valuation.errors import ValuationError
from engine.valuation.fcff import (
    EquityBridge,
    FcffDrivers,
    terminal_from_last_year,
    project_fcff,
    value_fcff,
)


# --- 折现数学 ---------------------------------------------------------------
def test_single_period_present_value_matches_hand_calculation():
    assert present_value([100.0], 0.10) == pytest.approx(90.9090909090909, rel=1e-12)


def test_multi_period_present_value_matches_hand_calculation():
    expected = 100 / 1.1 + 100 / 1.1**2 + 100 / 1.1**3
    assert present_value([100.0, 100.0, 100.0], 0.10) == pytest.approx(expected, rel=1e-12)


def test_annuity_matches_manual_sum():
    manual = sum(100 / 1.07**t for t in range(1, 6))
    assert annuity_present_value(100.0, 0.07, 5) == pytest.approx(manual, rel=1e-12)


def test_perpetuity_is_limit_of_zero_growth_terminal():
    """常数现金流 + 零增长终值必须退化为永续年金现值 CF/r。"""
    cash_flow, rate, periods = 100.0, 0.10, 5
    terminal = TerminalValue(
        method="zero_growth_perpetuity",
        growth=0.0,
        next_period_cash_flow=cash_flow,
        basis="测试：零增长终值",
        terminal_year=periods,
    )
    stream = discount_stream(
        [cash_flow] * periods, rate, terminal=terminal, last_period_year=periods
    )
    assert stream.total_present_value == pytest.approx(
        perpetuity_present_value(cash_flow, rate)
    )
    assert stream.terminal_present_value > 0


def test_growing_perpetuity_matches_formula():
    # CF1=100, r=10%, g=2% → 100/(0.10-0.02)=1250
    assert growing_perpetuity_present_value(100.0, 0.10, 0.02) == pytest.approx(1250.0)


def test_higher_discount_rate_never_increases_value():
    cash_flows = [80.0, 90.0, 100.0, 110.0]
    values = [present_value(cash_flows, rate) for rate in (0.05, 0.08, 0.10, 0.12)]
    assert values == sorted(values, reverse=True)
    assert all(later < earlier for earlier, later in zip(values, values[1:]))


def test_higher_discount_rate_never_increases_value_with_terminal():
    drivers = FcffDrivers(
        base_year=2025,
        base_revenue=1000.0,
        revenue_growth=(0.05,) * 5,
        ebit_margin=(0.2,) * 5,
        tax_rate=(0.25,) * 5,
        da_pct_revenue=(0.03,) * 5,
        capex_pct_revenue=(0.04,) * 5,
        nwc_pct_incremental_revenue=0.1,
    )
    years = project_fcff(drivers)
    bridge = EquityBridge(debt=0.0, cash=0.0, shares=100.0)
    values = []
    for rate in (0.06, 0.08, 0.10, 0.12):
        terminal = terminal_from_last_year(years, growth=0.02, basis="测试用终值")
        values.append(
            value_fcff(years, discount_rate=rate, terminal=terminal, bridge=bridge).value_per_share
        )
    assert all(later < earlier for earlier, later in zip(values, values[1:]))


def test_negative_cash_flow_is_preserved_not_clipped():
    value = present_value([-50.0, 100.0], 0.10)
    assert value == pytest.approx(-50 / 1.1 + 100 / 1.1**2)


def test_terminal_requires_rate_strictly_above_growth():
    with pytest.raises(ValuationError) as excinfo:
        growing_perpetuity_present_value(100.0, 0.02, 0.02)
    assert excinfo.value.code == "RATE_NOT_GREATER_THAN_GROWTH"
    with pytest.raises(ValuationError):
        growing_perpetuity_present_value(100.0, 0.01, 0.02)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_cash_flow_is_rejected(bad):
    with pytest.raises(ValuationError) as excinfo:
        present_value([bad], 0.10)
    assert excinfo.value.code == "CASH_FLOW_NOT_FINITE"


def test_non_finite_rate_is_rejected():
    with pytest.raises(ValuationError) as excinfo:
        present_value([100.0], float("nan"))
    assert excinfo.value.code == "RATE_NOT_FINITE"


def test_negative_terminal_cash_flow_cannot_be_perpetuated():
    drivers = FcffDrivers(
        base_year=2025,
        base_revenue=1000.0,
        revenue_growth=(-0.2,) * 3,
        ebit_margin=(-0.05,) * 3,
        tax_rate=(0.25,) * 3,
        da_pct_revenue=(0.01,) * 3,
        capex_pct_revenue=(0.02,) * 3,
        nwc_pct_incremental_revenue=0.0,
    )
    years = project_fcff(drivers)
    assert years[-1].fcff < 0
    with pytest.raises(ValuationError) as excinfo:
        terminal_from_last_year(years, growth=0.02, basis="测试用终值")
    assert excinfo.value.code == "TERMINAL_BASIS_MISSING"


# --- 权益桥接 ---------------------------------------------------------------
def test_equity_bridge_signs_and_per_share():
    bridge = EquityBridge(
        debt=300.0, cash=100.0, non_operating_assets=20.0, minority_interest=50.0, shares=100.0
    )
    assert bridge.equity_value(1000.0) == pytest.approx(1000 - 300 + 100 + 20 - 50)
    assert bridge.value_per_share(1000.0) == pytest.approx(7.70)


def test_adding_debt_cannot_increase_equity_value():
    base = EquityBridge(debt=300.0, cash=100.0, shares=100.0)
    more = EquityBridge(debt=400.0, cash=100.0, shares=100.0)
    assert more.equity_value(1000.0) < base.equity_value(1000.0)
    assert base.equity_value(1000.0) - more.equity_value(1000.0) == pytest.approx(100.0)


def test_adding_cash_increases_equity_value_by_same_amount():
    base = EquityBridge(debt=300.0, cash=100.0, shares=100.0)
    more = EquityBridge(debt=300.0, cash=150.0, shares=100.0)
    assert more.equity_value(1000.0) - base.equity_value(1000.0) == pytest.approx(50.0)


def test_share_count_changes_per_share_value_inversely():
    one = EquityBridge(shares=100.0)
    two = EquityBridge(shares=200.0)
    assert two.value_per_share(1000.0) == pytest.approx(one.value_per_share(1000.0) / 2)


def test_bridge_has_no_net_debt_input_so_it_cannot_be_double_counted():
    with pytest.raises(TypeError):
        EquityBridge(net_debt=200.0, shares=100.0)  # type: ignore[call-arg]
    assert EquityBridge(debt=300.0, cash=100.0, shares=1.0).net_debt == pytest.approx(200.0)


def test_negative_bridge_item_is_rejected():
    with pytest.raises(ValuationError) as excinfo:
        EquityBridge(debt=-1.0, shares=1.0)
    assert excinfo.value.code == "BRIDGE_ITEM_INVALID"


def test_zero_or_negative_share_count_is_rejected():
    for shares in (0.0, -5.0):
        with pytest.raises(ValuationError) as excinfo:
            EquityBridge(shares=shares)
        assert excinfo.value.code == "SHARE_COUNT_INVALID"


def _annual(**numbers) -> AnnualFinancials:
    from engine.data.facts import Fact

    values = {}
    for name, value in numbers.items():
        if value is None:
            continue
        values[name] = Fact.known(
            fact_id=f"fin.2025-12-31.{name}",
            metric=name,
            value=value,
            unit="CNY",
            currency="CNY",
            period="2025-12-31",
        )
    return AnnualFinancials(fiscal_year=2025, period_end="2025-12-31", values=values)


def _policy(**overrides):
    """构造逐项桥接政策：只给需要的项，其余项必须来自报表。"""
    from engine.financials.bridge import BridgeItemPolicy

    return {
        name: BridgeItemPolicy(item=name, treat_absent_as_zero=True, basis=basis)
        for name, basis in overrides.items()
    }


def test_bridge_policy_covers_debt_and_structural_items_but_not_minority():
    annual = _annual(
        cash=500.0,
        lease_liab=10.0,
        minority_equity=20.0,
        interest_expense=1.0,
    )
    built = build_equity_bridge(
        annual,
        shares=100.0,
        policy=_policy(
            debt_components="报表未列示短期借款等科目",
            non_operating_assets="未拆分非经营性项目，留在 EV 内",
            preferred_equity="报表无其他权益工具",
        ),
    )
    assert built.bridge.debt == pytest.approx(10.0)
    assert built.bridge.minority_interest == pytest.approx(20.0)
    statuses = {t.item: t.status for t in built.treatments}
    assert statuses["debt_components"] == "sourced_partial", "部分科目有来源、部分按声明视为零"
    assert statuses["minority_equity"] == "sourced"
    assert statuses["cash"] == "sourced"


def test_missing_minority_equity_cannot_be_treated_as_zero():
    """S1：缺少数数股东权益时必须拒绝，不能默认零后给出更高的每股价值。"""
    annual = _annual(cash=500.0, lease_liab=10.0, interest_expense=1.0)
    policy = _policy(
        debt_components="报表未列示借款科目",
        non_operating_assets="x",
        preferred_equity="x",
    )
    with pytest.raises(ValuationError) as excinfo:
        build_equity_bridge(annual, shares=100.0, policy=policy)
    assert excinfo.value.code == "MODEL_INPUT_MISSING"
    assert "少数股东权益" in excinfo.value.message


def test_minority_equity_assertion_requires_a_basis():
    from engine.financials.bridge import BridgeItemPolicy

    with pytest.raises(ValuationError) as excinfo:
        BridgeItemPolicy(item="minority_equity", treat_absent_as_zero=True, basis="  ")
    assert excinfo.value.code == "BRIDGE_ITEM_INVALID"


def test_known_zero_minority_equity_still_computes():
    from engine.data.facts import Fact

    annual = _annual(cash=500.0, lease_liab=10.0, interest_expense=1.0)
    annual.values["minority_equity"] = Fact.known_zero(
        fact_id="fin.2025-12-31.minority_equity",
        metric="minority_equity",
        unit="CNY",
        currency="CNY",
        period="2025-12-31",
    )
    built = build_equity_bridge(
        annual,
        shares=100.0,
        policy=_policy(debt_components="x", non_operating_assets="x", preferred_equity="x"),
    )
    assert built.bridge.minority_interest == 0.0
    statuses = {t.item: t.status for t in built.treatments}
    assert statuses["minority_equity"] == "sourced_zero"


def test_conflicting_minority_equity_is_rejected():
    from engine.data.facts import Fact

    annual = _annual(cash=500.0, lease_liab=10.0, interest_expense=1.0)
    annual.values["minority_equity"] = Fact.conflict(
        fact_id="fin.2025-12-31.minority_equity",
        metric="minority_equity",
        values=[10.0, 20.0],
        period="2025-12-31",
    )
    with pytest.raises(ValuationError) as excinfo:
        build_equity_bridge(
            annual,
            shares=100.0,
            policy=_policy(debt_components="x", non_operating_assets="x", preferred_equity="x"),
        )
    assert excinfo.value.code == "MODEL_INPUT_MISSING"


def test_there_is_no_blanket_absent_as_zero_switch_left():
    """S1：不允许再用一个全局开关把所有桥接项授权归零。"""
    annual = _annual(cash=500.0, lease_liab=10.0, minority_equity=1.0, interest_expense=1.0)
    with pytest.raises(TypeError):
        build_equity_bridge(  # type: ignore[call-arg]
            annual, shares=100.0, absent_as_zero=True
        )


def test_bridge_policy_rejects_unknown_items():
    from engine.financials.bridge import parse_bridge_policy

    with pytest.raises(ValuationError) as excinfo:
        parse_bridge_policy({"everything": {"treat_absent_as_zero": True, "basis": "x"}})
    assert excinfo.value.code == "BRIDGE_ITEM_INVALID"


def test_missing_preferred_equity_is_refused_without_a_declaration():
    annual = _annual(cash=500.0, lease_liab=10.0, minority_equity=1.0, interest_expense=1.0)
    with pytest.raises(ValuationError) as excinfo:
        build_equity_bridge(annual, shares=100.0, policy=_policy(debt_components="x"))
    assert excinfo.value.code == "MODEL_INPUT_MISSING"


def test_debt_absent_but_interest_paid_without_lease_is_a_conflict():
    annual = _annual(cash=100.0, lease_liab=None, interest_expense=5.0, minority_equity=1.0)
    with pytest.raises(ValuationError) as excinfo:
        build_equity_bridge(
            annual,
            shares=10.0,
            policy=_policy(debt_components="x", non_operating_assets="x", preferred_equity="x"),
        )
    assert excinfo.value.code == "BRIDGE_ITEM_INVALID"


# --- 股息内核 ---------------------------------------------------------------
def _ddm(**overrides) -> DividendDrivers:
    base = dict(
        base_year=2025,
        base_eps=10.0,
        eps_growth=(0.0, 0.0, 0.0),
        payout=(1.0, 1.0, 1.0),
        cost_of_equity=0.10,
        terminal_growth=0.0,
        roe=None,
    )
    base.update(overrides)
    return DividendDrivers(**base)


def test_dividend_present_value_matches_hand_calculation():
    valuation = value_dividend(_ddm())
    expected_explicit = 10 / 1.1 + 10 / 1.1**2 + 10 / 1.1**3
    expected_terminal = 10 / 0.10 / 1.1**3
    assert valuation.value_per_share == pytest.approx(expected_explicit + expected_terminal)


def test_dividend_zero_growth_terminal_reduces_to_perpetuity():
    valuation = value_dividend(_ddm())
    assert valuation.value_per_share == pytest.approx(perpetuity_present_value(10.0, 0.10))


def test_dividend_model_has_no_debt_input():
    """已直接折现股东分配，结构上不存在「再扣一次净债务」的入口。"""
    with pytest.raises(TypeError):
        DividendDrivers(  # type: ignore[call-arg]
            base_year=2025,
            base_eps=10.0,
            eps_growth=(0.0,),
            payout=(1.0,),
            cost_of_equity=0.10,
            terminal_growth=None,
            net_debt=500.0,
        )


def test_payout_above_earnings_blocks_positive_terminal_growth():
    drivers = _ddm(payout=(1.2, 1.2, 1.2), terminal_growth=0.02)
    with pytest.raises(ValuationError) as excinfo:
        value_dividend(drivers)
    assert excinfo.value.code == "GROWTH_NOT_FUNDED_BY_RETENTION"


def test_excess_payout_with_zero_growth_terminal_is_refused():
    """S2：把增长设成 0 只消除了增长需求，没有消除超额分配的资金需求。"""
    drivers = _ddm(payout=(1.2, 1.2, 1.2), terminal_growth=0.0)
    with pytest.raises(ValuationError) as excinfo:
        value_dividend(drivers)
    assert excinfo.value.code == "TERMINAL_DISTRIBUTION_UNFUNDED"


def test_full_payout_with_zero_growth_still_degenerates_to_annuity():
    drivers = _ddm(payout=(1.0, 1.0, 1.0), terminal_growth=0.0)
    valuation = value_dividend(drivers)
    assert valuation.value_per_share == pytest.approx(perpetuity_present_value(10.0, 0.10))


def test_supported_finite_excess_distribution_is_not_blanket_rejected():
    """有明确资金支持的有限期额外分配：只进明确预测期，终值用可持续分配。"""
    drivers = _ddm(payout=(1.2, 1.2, 1.2), terminal_growth=0.0)
    valuation = value_dividend(
        drivers, terminal_dps=10.0, terminal_basis="超额部分仅由已知一次性现金支持，仅限预测期"
    )
    explicit = 12 / 1.1 + 12 / 1.1**2 + 12 / 1.1**3
    terminal = 10 / 0.10 / 1.1**3
    assert valuation.value_per_share == pytest.approx(explicit + terminal)
    codes = {issue.code for issue in valuation.issues}
    assert "EXCESS_DISTRIBUTION_TERMINAL_SPLIT" in codes


def test_sustainable_terminal_dps_above_earnings_is_rejected():
    drivers = _ddm(payout=(1.2, 1.2, 1.2), terminal_growth=0.0)
    with pytest.raises(ValuationError) as excinfo:
        value_dividend(drivers, terminal_dps=11.0, terminal_basis="测试依据")
    assert excinfo.value.code == "TERMINAL_DISTRIBUTION_UNFUNDED"


def test_sustainable_terminal_dps_requires_a_basis():
    drivers = _ddm(payout=(1.2, 1.2, 1.2), terminal_growth=0.0)
    with pytest.raises(ValuationError) as excinfo:
        value_dividend(drivers, terminal_dps=10.0, terminal_basis="  ")
    assert excinfo.value.code == "TERMINAL_BASIS_MISSING"


def test_excess_distribution_without_terminal_is_allowed():
    """不启用终值时，超额分配只是有限期事实，不构成永续假设。"""
    drivers = _ddm(payout=(1.2, 1.2, 1.2), terminal_growth=None)
    valuation = value_dividend(drivers)
    assert valuation.value_per_share == pytest.approx(12 / 1.1 + 12 / 1.1**2 + 12 / 1.1**3)


def test_bank_route_inherits_terminal_sustainability_check():
    """银行路线复用同一股息内核：超额分配同样不能自动永续。"""
    with pytest.raises(ValuationError) as excinfo:
        value_bank_dividend(
            _bank_drivers(payout=(1.5, 1.5)),
            _risk(),
        )
    assert excinfo.value.code == "TERMINAL_DISTRIBUTION_UNFUNDED"


def test_terminal_growth_cannot_exceed_sustainable_growth():
    drivers = _ddm(payout=(0.3, 0.3, 0.3), terminal_growth=0.08, roe=0.10)
    with pytest.raises(ValuationError) as excinfo:
        value_dividend(drivers)
    assert excinfo.value.code == "GROWTH_NOT_FUNDED_BY_RETENTION"


def test_loss_making_basis_is_rejected_by_dividend_model():
    with pytest.raises(ValuationError) as excinfo:
        DividendDrivers(
            base_year=2025,
            base_eps=-1.0,
            eps_growth=(0.0,),
            payout=(0.5,),
            cost_of_equity=0.10,
            terminal_growth=None,
        )
    assert excinfo.value.code == "MODEL_INPUT_MISSING"


# --- 分红归集 ---------------------------------------------------------------
def _record(kind, per_10, fiscal_label, ex_date, status="实施") -> DividendRecord:
    return DividendRecord(
        fact_id=f"div.{fiscal_label}.{kind}",
        fiscal_year=None,
        fiscal_label=fiscal_label,
        kind=kind,
        per_10=per_10,
        ex_date=ex_date,
        announce_date="2025-01-01",
        status=status,
    )


def test_interim_and_annual_dividends_are_summed_by_fiscal_year():
    book = build_dividend_book(
        [
            _record("年度分红", 7.33, "2024年报", "2025-07-18"),
            _record("季度分红", 2.10, "2024三季报", "2025-01-24"),
        ],
        as_of="2026-11-10",
    )
    entry = book.year(2024)
    assert entry is not None
    # (7.33 + 2.10) / 10 = 0.943 元/股
    assert entry.ordinary_per_share == pytest.approx(0.943)
    assert entry.has_annual_final is True


def test_special_dividend_never_enters_ordinary_dps():
    book = build_dividend_book(
        [
            _record("年度分红", 30.876, "2023年报", "2024-06-19"),
            _record("特别分红", 19.106, None, "2023-12-20"),
        ],
        as_of="2026-11-10",
    )
    entry = book.year(2023)
    assert entry.ordinary_per_share == pytest.approx(3.0876)
    assert entry.special_per_share == pytest.approx(0.0)
    assert len(book.unlabeled_special) == 1
    assert book.unlabeled_special[0].per_share == pytest.approx(1.9106)


def test_ltm_paid_is_not_the_same_as_fiscal_year_total():
    book = build_dividend_book(
        [
            _record("年度分红", 7.0, "2023年报", "2024-07-19"),
            _record("年度分红", 7.9, "2025年报", "2026-07-17"),
        ],
        as_of="2026-11-10",
    )
    assert book.ltm_paid_per_share == pytest.approx(0.79)
    assert book.year(2023).ordinary_per_share == pytest.approx(0.70)


def test_planned_dividend_with_future_ex_date_is_excluded():
    book = build_dividend_book(
        [_record("年度分红", 5.0, "2025年报", "2026-12-01", status="预案")],
        as_of="2026-11-10",
    )
    assert book.ltm_paid_per_share is None
    assert len(book.excluded_not_implemented) == 1


def test_passed_ex_date_counts_as_implemented_even_without_status():
    """除权日已过即分配已执行：状态文本缺失不应丢掉真实支付。"""
    book = build_dividend_book(
        [_record("年度分红", 5.0, "2025年报", "2026-07-01", status="未取得")],
        as_of="2026-11-10",
    )
    assert book.ltm_paid_per_share == pytest.approx(0.5)
    entry = book.year(2025)
    assert entry is not None
    assert "ex_date_passed" in entry.components[0]["status"]


def test_non_cash_dividend_kind_is_ignored():
    book = build_dividend_book(
        [_record("股改分红", 5.91, "2006年报", "2006-05-22")], as_of="2026-11-10"
    )
    assert book.years == ()
    assert len(book.excluded_other_kinds) == 1


# --- 银行约束 ---------------------------------------------------------------
def _risk(**overrides) -> BankRiskEvidence:
    base = dict(
        cet1_ratio=0.13,
        required_cet1_ratio=0.10,
        npl_ratio=0.013,
        provision_coverage=2.0,
        required_provision_coverage=1.5,
        risk_weighted_assets=10000.0,
        rwa_growth=0.04,
    )
    base.update(overrides)
    return BankRiskEvidence(**base)


def _bank_drivers(**overrides) -> BankDividendDrivers:
    base = dict(
        base_year=2025,
        base_net_profit_parent=1000.0,
        net_profit_growth=(0.0, 0.0),
        payout=(0.5, 0.5),
        shares=100.0,
        cost_of_equity=0.10,
        terminal_growth=0.0,
    )
    base.update(overrides)
    return BankDividendDrivers(**base)


def test_bank_dividend_value_matches_hand_calculation():
    valuation = value_bank_dividend(_bank_drivers(), _risk())
    # EPS=10, payout=0.5 → DPS=5；r=10%, g=0 → 永续 50
    assert valuation.value_per_share == pytest.approx(50.0)


def test_bank_no_longer_uses_net_debt_so_value_is_not_reduced_twice():
    valuation = value_bank_dividend(_bank_drivers(), _risk())
    assert "net_debt" not in valuation.to_dict()
    assert valuation.value_per_share == pytest.approx(50.0)


def test_bank_cet1_below_requirement_is_rejected():
    with pytest.raises(ValuationError) as excinfo:
        value_bank_dividend(_bank_drivers(), _risk(cet1_ratio=0.09))
    assert excinfo.value.code == "BANK_CAPITAL_BELOW_REQUIREMENT"


def test_bank_provision_coverage_below_requirement_is_rejected():
    with pytest.raises(ValuationError) as excinfo:
        value_bank_dividend(_bank_drivers(), _risk(provision_coverage=1.2))
    assert excinfo.value.code == "BANK_PROVISION_BELOW_REQUIREMENT"


def test_bank_capital_consistent_payout_ceiling():
    risk = _risk(risk_weighted_assets=10000.0, rwa_growth=0.10, required_cet1_ratio=0.10)
    # ΔRWA=1000，所需留存=100，净利润=1000 → 上限 0.90
    assert risk.capital_consistent_payout(1000.0) == pytest.approx(0.90)


def test_bank_payout_above_capital_ceiling_is_flagged_not_silently_accepted():
    risk = _risk(risk_weighted_assets=100000.0, rwa_growth=0.20, required_cet1_ratio=0.10)
    valuation = value_bank_dividend(_bank_drivers(), risk)
    codes = {issue.code for issue in valuation.issues}
    assert "BANK_DISTRIBUTION_ABOVE_CAPITAL_CEILING" in codes


def test_bank_risk_evidence_missing_fields_are_listed():
    with pytest.raises(ValuationError) as excinfo:
        require_risk_evidence({"cet1_ratio": 0.13})
    assert excinfo.value.code == "BANK_RISK_EVIDENCE_MISSING"
    assert "npl_ratio" in excinfo.value.message


def test_bank_deteriorating_asset_quality_is_reported_as_issue():
    risk = _risk(npl_ratio_prior=0.012, cet1_ratio_prior=0.14)
    valuation = value_bank_dividend(_bank_drivers(), risk)
    codes = {issue.code for issue in valuation.issues}
    assert "BANK_ASSET_QUALITY_DETERIORATING" in codes
    assert "BANK_CAPITAL_BUFFER_NARROWING" in codes


def test_bank_loss_basis_is_rejected():
    with pytest.raises(ValuationError) as excinfo:
        value_bank_dividend(_bank_drivers(base_net_profit_parent=-5.0), _risk())
    assert excinfo.value.code == "MODEL_INPUT_MISSING"


def test_all_numbers_are_finite():
    valuation = value_bank_dividend(_bank_drivers(), _risk())
    assert math.isfinite(valuation.value_per_share)


def test_low_roe_with_positive_growth_is_refused_with_explanation():
    """低 ROE + 高派息 + 正增长：不是「一律不能估值」，而是增长没有资金来源。"""
    drivers = _ddm(payout=(0.9, 0.9, 0.9), terminal_growth=0.02, roe=0.03)
    with pytest.raises(ValuationError) as excinfo:
        value_dividend(drivers)
    assert excinfo.value.code == "GROWTH_NOT_FUNDED_BY_RETENTION"
    assert "可持续增长" in excinfo.value.message


def test_low_roe_with_zero_growth_is_still_valued_and_flagged():
    drivers = _ddm(payout=(0.9, 0.9, 0.9), terminal_growth=0.0, roe=0.03)
    valuation = value_dividend(drivers)
    assert valuation.value_per_share > 0, "低 ROE 不是拒绝估值的理由"
    codes = {issue.code for issue in valuation.issues}
    assert "DISTRIBUTION_NOT_COVERED_BY_EARNINGS" not in codes, "派息率 90% 仍在盈利以内"
    assert valuation.years[-1].retained_eps == pytest.approx(1.0)
