"""稳定收息型规则（income-pool-v1-draft）的边界、缺数据、治理复用与所有制测试。

只使用内存合成公司与固定快照，不访问网络；阈值全部从政策文件读取。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from engine.income.evaluator import evaluate_company, evaluate_pool
from engine.income.metrics import compute_metrics
from engine.quality.metrics import MetricValue
from engine.quality.models import (
    CONDITION_FAIL,
    CONDITION_PASS,
    CONDITION_PENDING,
    CONDITION_UNTESTED,
    OUT_OF_SCOPE_FINANCIAL,
    OUT_OF_SCOPE_ST,
    STATUS_IN_POOL,
    STATUS_NOT_IN_POOL,
    STATUS_OUT_OF_SCOPE,
    Company,
    FinancialYear,
    GovernancePenalty,
)
from engine.quality.policy import load_policy
from engine.quality.snapshot import load_snapshot_dir

ROOT = Path(__file__).resolve().parent.parent
INCOME_POLICY = ROOT / "data" / "policies" / "income-pool-v1-draft.json"
QUALITY_V12 = ROOT / "data" / "policies" / "quality-pool-v1.2-draft.json"
INCOME_DIR = ROOT / "data" / "income-pool"
INCOME_REPORT_MD = ROOT / "docs" / "phase4g-income-pool-report.md"


@pytest.fixture(scope="module")
def policy():
    return load_policy(INCOME_POLICY)


@pytest.fixture(scope="module")
def policy_v12():
    return load_policy(QUALITY_V12)


def _year(
    year: int,
    *,
    netprofit: float = 8.0,
    equity: float = 100.0,
    ocf: float = 3.2,
    capex: float = 0.0,
    total_profit: float = 8.0,
    interest_expense: float = 0.0,
    interest_debt: float = 0.0,
    monetary_funds: float = 10.0,
    depreciation: float = 0.0,
    amortization: float = 0.0,
) -> FinancialYear:
    return FinancialYear(
        fiscal_year=year,
        parent_netprofit=netprofit,
        revenue=100.0,
        parent_equity=equity,
        operating_cashflow=ocf,
        capex=capex,
        total_profit=total_profit,
        interest_expense=interest_expense,
        depreciation=depreciation,
        amortization=amortization,
        interest_debt=interest_debt,
        monetary_funds=monetary_funds,
    )


def _company(**overrides) -> Company:
    years = {2015: _year(2015)}
    for year in range(2016, 2026):
        years[year] = _year(year)
    years.update(overrides.pop("years_override", {}))
    for year in overrides.pop("remove_years", ()):
        years.pop(year, None)
    payload = dict(
        code="600000",
        name="测试公司",
        exchange="SH",
        board="主板",
        industry="白酒",
        listing_date="2010-01-01",
        is_st=False,
        financial_excluded=False,
        financial_exclusion_reason=None,
        expected_years=tuple(range(2016, 2026)),
        years=years,
        dividends={year: 3.2 for year in range(2016, 2026)},
        audit_opinions={year: "标准无保留意见" for year in range(2021, 2026)},
        audit_available=True,
        penalties=(),
        penalty_available=True,
        controlling_shareholder="测试控股股东",
        controlling_pledge_ratio=0.0,
        pledge_report_date="2026-09-30",
        pledge_available=True,
        governance_in_scope=True,
        dividend_available_years=tuple(range(2016, 2026)),
        ownership="民企",
    )
    payload.update(overrides)
    return Company(**payload)


def _condition(result, condition_id: str):
    return next(item for item in result.conditions if item.condition_id == condition_id)


def test_policy_versioned_and_every_parameter_has_basis(policy):
    assert policy.policy_id == "income-pool-v1-draft"
    assert policy.approved is False
    assert policy.status == "draft_unapproved"
    assert len(policy.conditions()) == 11
    for name, node in policy.raw["parameters"].items():
        assert node.get("basis"), name
        assert node.get("source"), name
        assert "value" in node, name


def test_default_income_company_passes_all_conditions(policy):
    result = evaluate_company(_company(), policy)
    assert result.status == STATUS_IN_POOL
    assert result.failing == []
    assert result.pending == []


def test_roe_average_boundary(policy):
    equal = evaluate_company(_company(), policy)
    assert _condition(equal, "roe_average").status == CONDITION_PASS
    below = evaluate_company(
        _company(years_override={year: _year(year, netprofit=7.99) for year in range(2016, 2026)}),
        policy,
    )
    assert _condition(below, "roe_average").status == CONDITION_FAIL


def test_roe_min_boundary(policy):
    years = {year: _year(year, netprofit=8.34) for year in range(2016, 2026)}
    years[2020] = _year(2020, netprofit=5.0)
    equal = evaluate_company(_company(years_override=years), policy)
    assert _condition(equal, "roe_min").status == CONDITION_PASS
    years[2020] = _year(2020, netprofit=4.99)
    below = evaluate_company(_company(years_override=years), policy)
    assert _condition(below, "roe_min").status == CONDITION_FAIL


def test_loss_years_zero_required(policy):
    years = {year: _year(year) for year in range(2016, 2026)}
    years[2019] = _year(2019, netprofit=-1.0)
    result = evaluate_company(_company(years_override=years), policy)
    assert _condition(result, "loss_years").status == CONDITION_FAIL


def test_profit_decline_years_boundary(policy):
    years = {year: _year(year) for year in range(2016, 2026)}
    years[2017] = _year(2017, netprofit=5.0)  # 8 -> 5 = -37.5%
    years[2018] = _year(2018, netprofit=8.0)
    one = evaluate_company(_company(years_override=years), policy)
    assert _condition(one, "profit_decline_years").status == CONDITION_PASS
    years[2019] = _year(2019, netprofit=5.0)
    years[2020] = _year(2020, netprofit=8.0)
    two = evaluate_company(_company(years_override=years), policy)
    assert _condition(two, "profit_decline_years").status == CONDITION_FAIL


def test_profit_decline_missing_year_and_field(policy):
    result = evaluate_company(_company(remove_years=(2017,)), policy)
    assert _condition(result, "profit_decline_years").status == CONDITION_FAIL
    years = {year: _year(year) for year in range(2016, 2026)}
    years[2017] = _year(2017, netprofit=None)
    pending = evaluate_company(_company(years_override=years), policy)
    assert _condition(pending, "profit_decline_years").status == CONDITION_PENDING


def test_dividend_every_year_boundary_and_missing(policy):
    dividends = {year: 3.2 for year in range(2016, 2026)}
    dividends[2018] = 0.0
    missing = evaluate_company(_company(dividends=dividends), policy)
    assert _condition(missing, "dividend_every_year").status == CONDITION_FAIL
    unavailable = evaluate_company(
        _company(dividend_available_years=tuple(range(2016, 2025))), policy
    )
    assert _condition(unavailable, "dividend_every_year").status == CONDITION_PENDING


def test_dividend_payout_boundary(policy):
    equal = evaluate_company(_company(), policy)  # 16 / 40 = 40%
    assert _condition(equal, "dividend_payout").status == CONDITION_PASS
    dividends = {year: 3.19 for year in range(2016, 2026)}  # 15.95 / 40
    below = evaluate_company(_company(dividends=dividends), policy)
    assert _condition(below, "dividend_payout").status == CONDITION_FAIL


def test_fcf_to_dividend_boundary_and_zero_dividend(policy):
    equal = evaluate_company(_company(), policy)  # 16 / 16 = 1.0
    assert _condition(equal, "fcf_to_dividend").status == CONDITION_PASS
    below = evaluate_company(
        _company(years_override={year: _year(year, ocf=3.16) for year in range(2016, 2026)}),
        policy,
    )
    assert _condition(below, "fcf_to_dividend").status == CONDITION_FAIL
    zero = evaluate_company(_company(dividends={year: 0.0 for year in range(2016, 2026)}), policy)
    assert _condition(zero, "fcf_to_dividend").status == CONDITION_FAIL


def test_net_debt_to_ebitda_strict_boundary(policy):
    equal = evaluate_company(
        _company(
            years_override={
                2025: _year(2025, total_profit=10.0, interest_debt=50.0, monetary_funds=10.0)
            }
        ),
        policy,
    )
    assert _condition(equal, "net_debt_ebitda").status == CONDITION_FAIL  # 40 / 10 = 4，严格小于
    below = evaluate_company(
        _company(
            years_override={
                2025: _year(2025, total_profit=10.0, interest_debt=49.9, monetary_funds=10.0)
            }
        ),
        policy,
    )
    assert _condition(below, "net_debt_ebitda").status == CONDITION_PASS
    missing = evaluate_company(
        _company(
            years_override={
                2025: _year(2025, total_profit=None, interest_debt=50.0, monetary_funds=10.0)
            }
        ),
        policy,
    )
    assert _condition(missing, "net_debt_ebitda").status == CONDITION_PENDING


def test_audit_penalty_pledge_conditions(policy):
    opinions = {year: "标准无保留意见" for year in range(2021, 2026)}
    opinions[2024] = "保留意见"
    audit = evaluate_company(_company(audit_opinions=opinions), policy)
    assert _condition(audit, "audit_opinion").status == CONDITION_FAIL
    missing_audit = evaluate_company(_company(audit_available=False), policy)
    assert _condition(missing_audit, "audit_opinion").status == CONDITION_PENDING

    event = GovernancePenalty(
        date="2024-05-01",
        event_type="行政处罚",
        title="关于收到行政处罚决定书的公告",
        info_disclosure=True,
        counted=True,
        subject="测试公司",
        subject_role="company",
    )
    penalty = evaluate_company(_company(penalties=(event,)), policy)
    assert _condition(penalty, "no_penalty").status == CONDITION_FAIL
    unknown = GovernancePenalty(
        date="2024-05-01",
        event_type="行政处罚",
        title="关于收到行政处罚决定书的公告",
        info_disclosure=True,
        counted=False,
        subject_role="unknown",
    )
    pending = evaluate_company(
        _company(penalties=(unknown,), penalty_subject_unknown=True), policy
    )
    assert _condition(pending, "no_penalty").status == CONDITION_PENDING

    pledge_equal = evaluate_company(_company(controlling_pledge_ratio=0.5), policy)
    assert _condition(pledge_equal, "pledge_ratio").status == CONDITION_PASS
    pledge_above = evaluate_company(_company(controlling_pledge_ratio=0.5000001), policy)
    assert _condition(pledge_above, "pledge_ratio").status == CONDITION_FAIL
    pledge_missing = evaluate_company(_company(pledge_available=False), policy)
    assert _condition(pledge_missing, "pledge_ratio").status == CONDITION_PENDING


def test_income_scope_out_of_scope(policy):
    financial = evaluate_company(
        _company(financial_excluded=True, financial_exclusion_reason="银行"), policy
    )
    assert financial.status == STATUS_OUT_OF_SCOPE
    assert financial.metrics.get("out_of_scope_reason") == OUT_OF_SCOPE_FINANCIAL
    st = evaluate_company(_company(is_st=True), policy)
    assert st.status == STATUS_OUT_OF_SCOPE
    assert st.metrics.get("out_of_scope_reason") == OUT_OF_SCOPE_ST


def test_income_governance_conditions_reuse_v12(policy, policy_v12):
    for cid in ("audit_opinion", "no_penalty", "pledge_ratio"):
        income = policy.condition(cid)
        quality = policy_v12.condition(cid)
        for key in ("metric", "operator", "parameter", "unit", "distance"):
            assert income[key] == quality[key], (cid, key)
        assert policy.parameter(income["parameter"])["value"] == policy_v12.parameter(
            quality["parameter"]
        )["value"]

    company = _company()
    income_result = evaluate_company(company, policy)
    from engine.quality.evaluator import evaluate_company as quality_evaluate

    quality_result = quality_evaluate(company, policy_v12)
    for cid in ("audit_opinion", "no_penalty", "pledge_ratio"):
        income_condition = _condition(income_result, cid)
        quality_condition = _condition(quality_result, cid)
        assert income_condition.status == quality_condition.status
        assert income_condition.value == quality_condition.value


def test_ownership_does_not_change_judgement(policy):
    state_owned = evaluate_company(_company(ownership="央企"), policy)
    private = evaluate_company(_company(ownership="民企"), policy)
    assert state_owned.status == private.status
    assert [(item.condition_id, item.status) for item in state_owned.conditions] == [
        (item.condition_id, item.status) for item in private.conditions
    ]


def test_metrics_are_metric_values(policy):
    metrics = compute_metrics(_company(), policy)
    for key in (
        "roe_average",
        "roe_min",
        "loss_years",
        "profit_decline_years",
        "dividend_missing_years",
        "dividend_payout",
        "fcf_to_dividend",
        "net_debt_to_ebitda",
        "audit_nonstandard_years",
        "penalty_count",
        "pledge_ratio",
    ):
        assert isinstance(metrics[key], MetricValue), key


def test_income_snapshot_and_report_and_ledger():
    manifest = json.loads((INCOME_DIR / "manifest.json").read_text(encoding="utf-8"))
    snapshot, companies, _stats = load_snapshot_dir(INCOME_DIR / manifest["current"])
    by_code = {company.code: company for company in companies}
    assert manifest["snapshot_id"].startswith("qps-")
    assert any(table.get("kind") == "ownership" for table in snapshot["tables"])
    assert any(table.get("kind") == "governance_audit" for table in snapshot["tables"])
    assert by_code["600519"].dividend_available_years[0] == 2016
    assert by_code["600519"].dividend_available_years[-1] == 2025
    assert by_code["600519"].ownership in ("央企", "地方国企", "民企", "外资", "其他")

    replay, _companies, _stats = load_snapshot_dir(INCOME_DIR / manifest["current"])
    policy = load_policy(INCOME_POLICY)
    first = evaluate_pool(_companies, policy, as_of=replay["as_of"], snapshot_id=replay["snapshot_id"])
    second = evaluate_pool(companies, policy, as_of=snapshot["as_of"], snapshot_id=snapshot["snapshot_id"])
    assert first.counts == second.counts
    assert [(item.code, item.status) for item in first.results] == [
        (item.code, item.status) for item in second.results
    ]

    if not INCOME_REPORT_MD.exists():
        pytest.skip("收息型报告尚未生成")
    report = json.loads((INCOME_DIR / "report.json").read_text(encoding="utf-8"))
    assert report["meta"]["policy_id"] == "income-pool-v1"
    assert report["meta"]["snapshot_id"] == manifest["snapshot_id"]
    assert report["dividend_coverage"]["periods_available"] == 10
    assert {row["year"] for row in report["dividend_coverage"]["years"]} == set(range(2016, 2021))
    assert report["both_pools"]
    assert report["ownership_distribution"]["quality_total"] > 0
    assert report["ownership_distribution"]["income_total"] == report["counts"]["in_pool"]
    assert len(report["in_pool"]) == report["counts"]["in_pool"]
    for key in ("audit", "penalty", "pledge"):
        assert report["governance_coverage"][key]["ratio"] == 1.0

    ledger = ROOT / "data" / "ledger" / "income-pool.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 2
    assert rows[0]["policy_id"] == "income-pool-v1-draft"
    assert rows[1]["policy_id"] == "income-pool-v1"
    assert rows[1]["snapshot_id"] == manifest["snapshot_id"]
    assert rows[1]["counts"]["in_pool"] == report["counts"]["in_pool"]
    assert rows[1]["pool_hash"] == rows[0]["pool_hash"]
    assert rows[1]["pool_hash"].startswith("iph-")


def test_quality_pool_unchanged():
    manifest = json.loads((ROOT / "data" / "quality-pool" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["snapshot_id"] == "qps-3ebbd24be612bf7e"
    report = json.loads((ROOT / "data" / "quality-pool" / "report.json").read_text(encoding="utf-8"))
    assert report["meta"]["policy_id"] == "quality-pool-v1.2"
    assert report["counts"]["in_pool"] == 138
    ledger = [
        json.loads(line)
        for line in (ROOT / "data" / "ledger" / "quality-pool.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(ledger) == 4
    assert ledger[-1]["policy_id"] == "quality-pool-v1.2"
    assert ledger[-1]["pool_hash"] == "qph-fc548bf288b854fe"


def test_income_report_has_required_sections_and_no_old_wording():
    if not INCOME_REPORT_MD.exists():
        pytest.skip("收息型报告尚未生成")
    text = INCOME_REPORT_MD.read_text(encoding="utf-8")
    for heading in (
        "治理三项覆盖率",
        "分红数据覆盖",
        "同时属于两个池的公司",
        "所有制分布",
        "入池名单",
        "只差一条",
        "漏斗",
        "敏感性",
        "固定 30 只测试集成绩单",
        "行业分布",
    ):
        assert heading in text
    assert not re.search(r"\d% ≥ [\d.]+%，未通过", text)
    assert "观测值 " in text and "，要求 " in text


def test_missing_window_year_fails_financial_conditions(policy):
    result = evaluate_company(_company(remove_years=(2018,)), policy)
    for cid in ("roe_average", "roe_min", "loss_years", "profit_decline_years"):
        assert _condition(result, cid).status == CONDITION_FAIL, cid


def test_missing_recent_year_fails_dividend_and_fcf(policy):
    result = evaluate_company(_company(remove_years=(2023,)), policy)
    assert _condition(result, "dividend_payout").status == CONDITION_FAIL
    assert _condition(result, "fcf_to_dividend").status == CONDITION_FAIL


def test_missing_dividend_data_pends_dividend_and_fcf(policy):
    result = evaluate_company(
        _company(dividend_available_years=tuple(range(2016, 2025))), policy
    )
    for cid in ("dividend_every_year", "dividend_payout", "fcf_to_dividend"):
        assert _condition(result, cid).status == CONDITION_PENDING, cid
