"""银行规则（bank-pool-v1-draft）边界、范围、治理复用与预演测试。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from engine.bank.evaluator import evaluate_company
from engine.bank.metrics import compute_metrics
from engine.quality.metrics import MetricValue
from engine.quality.models import (
    CONDITION_FAIL,
    CONDITION_PASS,
    CONDITION_PENDING,
    OUT_OF_SCOPE_SHORT_HISTORY,
    OUT_OF_SCOPE_ST,
    STATUS_IN_POOL,
    STATUS_NOT_IN_POOL,
    STATUS_OUT_OF_SCOPE,
    STATUS_PENDING,
    Company,
    FinancialYear,
    GovernancePenalty,
)
from engine.quality.policy import load_policy
from engine.quality.snapshot import load_snapshot_dir

ROOT = Path(__file__).resolve().parent.parent
BANK_POLICY = ROOT / "data" / "policies" / "bank-pool-v1-draft.json"
QUALITY_V12 = ROOT / "data" / "policies" / "quality-pool-v1.2-draft.json"
BANK_DIR = ROOT / "data" / "bank-pool"
BANK_REPORT = ROOT / "docs" / "phase4h-bank-pool-report.md"
REHEARSAL = ROOT / "docs" / "phase4h-bank-valuation-rehearsal.md"


@pytest.fixture(scope="module")
def policy():
    return load_policy(BANK_POLICY)


@pytest.fixture(scope="module")
def policy_v12():
    return load_policy(QUALITY_V12)


def _year(year: int, *, netprofit: float = 8.0, equity: float = 100.0) -> FinancialYear:
    return FinancialYear(
        fiscal_year=year,
        parent_netprofit=netprofit,
        revenue=100.0,
        parent_equity=equity,
        operating_cashflow=10.0,
        capex=0.0,
        total_profit=10.0,
        interest_expense=0.0,
        interest_debt=0.0,
        monetary_funds=10.0,
    )


def _bank_row(
    date: str,
    *,
    report_type: str = "年报",
    roe: float = 10.0,
    profit: float = 8.0,
    npl: float = 1.0,
    provision: float = 250.0,
    core: float = 10.0,
    nim: float = 2.0,
) -> dict:
    return {
        "report_date": date,
        "report_type": report_type,
        "roe_weighted": roe,
        "parent_netprofit": profit,
        "npl_ratio": npl,
        "provision_coverage": provision,
        "core_tier1_ratio": core,
        "net_interest_margin": nim,
    }


def _bank_company(**overrides) -> Company:
    years = {2015: _year(2015)}
    for year in range(2016, 2026):
        years[year] = _year(year)
    years.update(overrides.pop("years_override", {}))
    for year in overrides.pop("remove_years", ()):
        years.pop(year, None)
    reports = overrides.pop("bank_reports", None)
    if reports is None:
        reports = [_bank_row(f"{year}-12-31") for year in range(2016, 2026)]
        reports += [
            _bank_row("2021-09-30", report_type="三季报", npl=0.8),
            _bank_row("2026-09-30", report_type="三季报", npl=1.0),
        ]
    payload = dict(
        code="600036",
        name="测试银行",
        exchange="SH",
        board="主板",
        industry="银行Ⅱ",
        listing_date="2010-01-01",
        is_st=False,
        financial_excluded=True,
        financial_exclusion_reason="银行Ⅱ",
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
        ownership="央企",
        bank_reports=tuple(reports),
    )
    payload.update(overrides)
    return Company(**payload)


def _condition(result, condition_id: str):
    return next(item for item in result.conditions if item.condition_id == condition_id)


def test_policy_versioned_and_all_parameters_have_basis(policy):
    assert policy.policy_id == "bank-pool-v1-draft"
    assert policy.approved is False
    assert policy.status == "draft_unapproved"
    assert len(policy.conditions()) == 12
    for name, node in policy.raw["parameters"].items():
        assert node.get("basis"), name
        assert node.get("source"), name
        assert "value" in node, name


def test_default_bank_passes_all_conditions(policy):
    result = evaluate_company(_bank_company(), policy)
    assert result.status == STATUS_IN_POOL
    assert result.failing == []
    assert result.pending == []


def test_roe_boundaries(policy):
    years = {year: _year(year) for year in range(2016, 2026)}
    reports = [_bank_row(f"{year}-12-31", roe=10.0) for year in range(2016, 2026)]
    reports += [_bank_row("2021-09-30", report_type="三季报", npl=0.8), _bank_row("2026-09-30", report_type="三季报")]
    equal = evaluate_company(_bank_company(years_override=years, bank_reports=reports), policy)
    assert _condition(equal, "roe_average").status == CONDITION_PASS
    reports = [_bank_row(f"{year}-12-31", roe=9.99) for year in range(2016, 2026)]
    reports += [_bank_row("2021-09-30", report_type="三季报", npl=0.8), _bank_row("2026-09-30", report_type="三季报")]
    below = evaluate_company(_bank_company(years_override=years, bank_reports=reports), policy)
    assert _condition(below, "roe_average").status == CONDITION_FAIL


def test_roe_recent_boundary(policy):
    reports = [_bank_row(f"{year}-12-31", roe=10.0) for year in range(2016, 2023)]
    reports += [_bank_row(f"{year}-12-31", roe=9.0) for year in range(2023, 2026)]
    reports += [_bank_row("2021-09-30", report_type="三季报", npl=0.8), _bank_row("2026-09-30", report_type="三季报")]
    equal = evaluate_company(_bank_company(bank_reports=reports), policy)
    assert _condition(equal, "roe_recent").status == CONDITION_PASS
    reports = [_bank_row(f"{year}-12-31", roe=10.0) for year in range(2016, 2023)]
    reports += [_bank_row(f"{year}-12-31", roe=8.99) for year in range(2023, 2026)]
    reports += [_bank_row("2021-09-30", report_type="三季报", npl=0.8), _bank_row("2026-09-30", report_type="三季报")]
    below = evaluate_company(_bank_company(bank_reports=reports), policy)
    assert _condition(below, "roe_recent").status == CONDITION_FAIL


def test_loss_npl_provision_core_boundaries(policy):
    reports = [_bank_row(f"{year}-12-31") for year in range(2016, 2026)]
    reports[3]["parent_netprofit"] = -1.0
    reports += [_bank_row("2021-09-30", report_type="三季报", npl=0.8), _bank_row("2026-09-30", report_type="三季报")]
    assert _condition(evaluate_company(_bank_company(bank_reports=reports), policy), "loss_years").status == CONDITION_FAIL

    reports = [_bank_row(f"{year}-12-31") for year in range(2016, 2026)]
    reports += [
        _bank_row("2021-09-30", report_type="三季报", npl=0.8),
        _bank_row("2026-09-30", report_type="三季报", npl=1.5),
    ]
    equal = evaluate_company(_bank_company(bank_reports=reports), policy)
    assert _condition(equal, "npl_ratio").status == CONDITION_PASS
    reports[-1] = _bank_row("2026-09-30", report_type="三季报", npl=1.51)
    assert _condition(evaluate_company(_bank_company(bank_reports=reports), policy), "npl_ratio").status == CONDITION_FAIL

    reports = [_bank_row(f"{year}-12-31") for year in range(2016, 2026)]
    reports += [
        _bank_row("2021-09-30", report_type="三季报", npl=0.9),
        _bank_row("2026-09-30", report_type="三季报", npl=1.2),
    ]
    equal = evaluate_company(_bank_company(bank_reports=reports), policy)
    assert _condition(equal, "npl_change").status == CONDITION_PASS
    reports[-1] = _bank_row("2026-09-30", report_type="三季报", npl=1.21)
    assert _condition(evaluate_company(_bank_company(bank_reports=reports), policy), "npl_change").status == CONDITION_FAIL

    reports = [_bank_row(f"{year}-12-31") for year in range(2016, 2026)]
    reports += [
        _bank_row("2021-09-30", report_type="三季报", npl=0.8),
        _bank_row("2026-09-30", report_type="三季报", provision=200.0),
    ]
    equal = evaluate_company(_bank_company(bank_reports=reports), policy)
    assert _condition(equal, "provision_coverage").status == CONDITION_PASS
    reports[-1] = _bank_row("2026-09-30", report_type="三季报", provision=199.99)
    assert _condition(evaluate_company(_bank_company(bank_reports=reports), policy), "provision_coverage").status == CONDITION_FAIL

    reports = [_bank_row(f"{year}-12-31") for year in range(2016, 2026)]
    reports += [
        _bank_row("2021-09-30", report_type="三季报", npl=0.8),
        _bank_row("2026-09-30", report_type="三季报", core=9.0),
    ]
    equal = evaluate_company(_bank_company(bank_reports=reports), policy)
    assert _condition(equal, "core_tier1").status == CONDITION_PASS
    reports[-1] = _bank_row("2026-09-30", report_type="三季报", core=8.99)
    assert _condition(evaluate_company(_bank_company(bank_reports=reports), policy), "core_tier1").status == CONDITION_FAIL


def test_dividend_boundaries_and_missing(policy):
    dividends = {year: 3.2 for year in range(2016, 2026)}
    dividends[2018] = 0.0
    assert _condition(evaluate_company(_bank_company(dividends=dividends), policy), "dividend_every_year").status == CONDITION_FAIL
    dividends = {year: 2.0 for year in range(2016, 2026)}  # 10 / 40 = 25%
    assert _condition(evaluate_company(_bank_company(dividends=dividends), policy), "dividend_payout").status == CONDITION_PASS
    dividends = {year: 1.99 for year in range(2016, 2026)}
    assert _condition(evaluate_company(_bank_company(dividends=dividends), policy), "dividend_payout").status == CONDITION_FAIL
    unavailable = evaluate_company(
        _bank_company(dividend_available_years=tuple(range(2016, 2025))), policy
    )
    assert _condition(unavailable, "dividend_every_year").status == CONDITION_PENDING


def test_missing_bank_metric_and_annual_report(policy):
    pending = evaluate_company(_bank_company(bank_reports=[]), policy)
    assert pending.status == STATUS_PENDING
    assert _condition(pending, "provision_coverage").status == CONDITION_PENDING
    missing_year = evaluate_company(_bank_company(remove_years=(2020,)), policy)
    assert _condition(missing_year, "roe_average").status == CONDITION_FAIL


def test_audit_penalty_pledge_conditions(policy):
    opinions = {year: "标准无保留意见" for year in range(2021, 2026)}
    opinions[2024] = "保留意见"
    assert _condition(evaluate_company(_bank_company(audit_opinions=opinions), policy), "audit_opinion").status == CONDITION_FAIL
    event = GovernancePenalty(
        date="2024-05-01",
        event_type="行政处罚",
        title="关于收到行政处罚决定书的公告",
        info_disclosure=True,
        counted=True,
        subject="测试银行",
        subject_role="company",
    )
    assert _condition(evaluate_company(_bank_company(penalties=(event,)), policy), "no_penalty").status == CONDITION_FAIL
    assert _condition(evaluate_company(_bank_company(controlling_pledge_ratio=0.5), policy), "pledge_ratio").status == CONDITION_PASS
    assert _condition(evaluate_company(_bank_company(controlling_pledge_ratio=0.5000001), policy), "pledge_ratio").status == CONDITION_FAIL


def test_scope_only_banks(policy):
    non_bank = evaluate_company(
        _bank_company(industry="白酒", financial_excluded=False, financial_exclusion_reason=None), policy
    )
    assert non_bank.status == STATUS_OUT_OF_SCOPE
    st = evaluate_company(_bank_company(is_st=True), policy)
    assert st.status == STATUS_OUT_OF_SCOPE
    assert st.metrics.get("out_of_scope_reason") == OUT_OF_SCOPE_ST
    short = evaluate_company(
        _bank_company(expected_years=tuple(range(2021, 2026))), policy
    )
    assert short.status == STATUS_OUT_OF_SCOPE
    assert short.metrics.get("out_of_scope_reason") == OUT_OF_SCOPE_SHORT_HISTORY


def test_bank_governance_reuses_v12(policy, policy_v12):
    for cid in ("audit_opinion", "no_penalty", "pledge_ratio"):
        income = policy.condition(cid)
        quality = policy_v12.condition(cid)
        for key in ("metric", "operator", "parameter", "unit", "distance"):
            assert income[key] == quality[key], (cid, key)
        assert policy.parameter(income["parameter"])["value"] == policy_v12.parameter(
            quality["parameter"]
        )["value"]


def test_ownership_does_not_change_judgement(policy):
    state = evaluate_company(_bank_company(ownership="央企"), policy)
    private = evaluate_company(_bank_company(ownership="民企"), policy)
    assert state.status == private.status
    assert [(c.condition_id, c.status) for c in state.conditions] == [
        (c.condition_id, c.status) for c in private.conditions
    ]


def test_bank_metrics_are_metric_values(policy):
    metrics = compute_metrics(_bank_company(), policy)
    for key in (
        "roe_average",
        "roe_recent_average",
        "loss_years",
        "npl_ratio",
        "npl_change_5y",
        "provision_coverage",
        "core_tier1_ratio",
        "dividend_missing_years",
        "dividend_payout",
        "audit_nonstandard_years",
        "penalty_count",
        "pledge_ratio",
    ):
        assert isinstance(metrics[key], MetricValue), key


def test_bank_snapshot_and_report():
    manifest = json.loads((BANK_DIR / "manifest.json").read_text(encoding="utf-8"))
    snapshot, companies, _stats = load_snapshot_dir(BANK_DIR / manifest["current"])
    by_code = {company.code: company for company in companies}
    assert manifest["snapshot_id"] == snapshot["snapshot_id"]
    assert len(snapshot["tables"]) == 71
    assert len(by_code["600036"].bank_reports) > 10
    row_2025 = next(
        row
        for row in by_code["600036"].bank_reports
        if str(row.get("report_date"))[:10] == "2025-12-31"
    )
    assert float(row_2025["roe_weighted"]) == 13.44
    assert float(row_2025["npl_ratio"]) == 0.94
    assert float(row_2025["provision_coverage"]) == 391.79
    assert float(row_2025["core_tier1_ratio"]) == 14.16
    assert by_code["600036"].dividend_available_years[0] == 2016
    assert by_code["600036"].ownership in ("央企", "地方国企", "民企", "外资", "其他")

    replay, _companies, _stats = load_snapshot_dir(BANK_DIR / manifest["current"])
    from engine.bank.evaluator import evaluate_pool

    policy = load_policy(BANK_POLICY)
    first = evaluate_pool(companies, policy, as_of=snapshot["as_of"], snapshot_id=snapshot["snapshot_id"])
    second = evaluate_pool(_companies, policy, as_of=replay["as_of"], snapshot_id=replay["snapshot_id"])
    assert first.counts == second.counts
    assert [(item.code, item.status) for item in first.results] == [
        (item.code, item.status) for item in second.results
    ]

    if not BANK_REPORT.exists():
        pytest.skip("银行报告尚未生成")
    report = json.loads((BANK_DIR / "report.json").read_text(encoding="utf-8"))
    assert report["meta"]["policy_id"] == "bank-pool-v1-draft"
    assert report["meta"]["snapshot_id"] == manifest["snapshot_id"]
    assert report["counts"]["in_pool"] == 11
    assert report["counts"]["pending"] == 0
    assert len(report["in_pool"]) == 11
    assert len(report["near_misses"]) == 13
    for key in ("roe", "npl", "npl_change", "provision", "core_tier1", "dividend", "audit", "penalty", "pledge"):
        assert report["data_coverage"][key]["ratio"] == 1.0
    assert report["ownership_distribution"]["total"] == 11
    assert report["valuation_rehearsal_summary"]["total"] == 36

    text = BANK_REPORT.read_text(encoding="utf-8")
    for heading in ("首页", "入池名单", "只差一条", "漏斗", "敏感性", "两家测试集银行", "所有制分布", "估值预演摘要"):
        assert heading in text
    assert not re.search(r"\d% ≥ [\d.]+%，未通过", text)


def test_bank_valuation_rehearsal_report():
    if not REHEARSAL.exists():
        pytest.skip("预演报告尚未生成")
    payload = json.loads((BANK_DIR / "valuation-rehearsal.json").read_text(encoding="utf-8"))
    assert payload["meta"]["policy"] == "equity-policy-v1.1"
    assert payload["meta"]["bank_check"] == "bank-check-v1-draft"
    assert len(payload["banks"]) == 36
    assert payload["summary"]["grade_b"] + payload["summary"]["grade_c"] + payload["summary"]["insufficient"] == 36
    for row in payload["banks"]:
        assert row["route"] == "bank_dividend_discount"
        assert row["status"] in ("complete", "insufficient_data")
        assert row["grade"] in ("B", "C")
        assert row["rehearsal_conclusion"]
        if row["status"] == "complete":
            assert set(row["values"]) == {"pessimistic", "base", "optimistic"}
            assert all(value > 0 for value in row["values"].values())
            assert len(row["sensitivity"]) == 3
    text = REHEARSAL.read_text(encoding="utf-8")
    assert "不写入正式结论账本" in text
    assert "安全边际门槛价" in text
    assert "股权成本 ±0.5pp" in text


def test_bank_ledger_and_other_ledgers_unchanged():
    rows = [
        json.loads(line)
        for line in (ROOT / "data" / "ledger" / "bank-pool.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    assert rows[0]["policy_id"] == "bank-pool-v1-draft"
    assert rows[0]["counts"]["in_pool"] == 11
    assert rows[0]["pool_hash"] == "bph-13b62d2bcfb016c0"

    quality = json.loads((ROOT / "data" / "quality-pool" / "report.json").read_text(encoding="utf-8"))
    assert quality["counts"]["in_pool"] == 138
    income = json.loads((ROOT / "data" / "income-pool" / "report.json").read_text(encoding="utf-8"))
    assert income["counts"]["in_pool"] == 142
    conclusions = [
        json.loads(line)
        for line in (ROOT / "data" / "ledger" / "equity-conclusions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(conclusions) == 7
    assert not any(row.get("code") == "600036" for row in conclusions)


def test_cli_bank_conclusion_unchanged():
    result = subprocess.run(
        [sys.executable, "-m", "engine.cli", "--json", "600036"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-500:]
    payload = json.loads(result.stdout)
    assert payload["analysis_status"] == "insufficient_data"
    assert payload.get("valuation") is None
