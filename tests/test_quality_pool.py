"""好公司池规则（quality-pool-v1-draft）的边界与缺数据测试。

这些测试只使用内存中的合成公司，不访问网络；政策阈值全部从政策文件读取。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from engine.quality.build import aggregate_dividends, is_st_name
from engine.quality.evaluator import evaluate_company, evaluate_pool
from engine.quality.models import (
    CONDITION_FAIL,
    CONDITION_PASS,
    CONDITION_PENDING,
    CONDITION_UNTESTED,
    OUT_OF_SCOPE_FINANCIAL,
    OUT_OF_SCOPE_SHORT_HISTORY,
    OUT_OF_SCOPE_ST,
    STATUS_IN_POOL,
    STATUS_NOT_IN_POOL,
    STATUS_OUT_OF_SCOPE,
    STATUS_PENDING,
    Company,
    FinancialYear,
)
from engine.quality.policy import PolicyError, load_policy
from engine.quality.snapshot import SnapshotError, build_snapshot_dir, load_snapshot_dir
from engine.quality.tables import write_table

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "data" / "policies" / "quality-pool-v1-draft.json"


@pytest.fixture(scope="module")
def policy():
    return load_policy(POLICY_PATH)


def _year(
    year: int,
    *,
    netprofit: float = 15.0,
    equity: float = 100.0,
    ocf: float = 15.0,
    capex: float = 0.0,
    total_profit: float = 15.0,
    interest_expense: float = 0.0,
    depreciation: float = 0.0,
    amortization: float = 0.0,
    interest_debt: float = 0.0,
    monetary_funds: float = 10.0,
) -> FinancialYear:
    return FinancialYear(
        fiscal_year=year,
        parent_netprofit=netprofit,
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
        dividends={year: 3.0 for year in range(2021, 2026)},
        pledge_company_ratio=None,
    )
    payload.update(overrides)
    return Company(**payload)


def _condition(result, condition_id: str):
    return next(item for item in result.conditions if item.condition_id == condition_id)


def test_policy_is_draft_and_all_parameters_have_basis(policy):
    assert policy.approved is False
    assert policy.status == "draft_unapproved"
    for name, node in policy.raw["parameters"].items():
        assert node.get("basis"), name
        assert node.get("source"), name
        assert "value" in node, name


def test_policy_rejects_approved_true(tmp_path):
    raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    raw["approved"] = True
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PolicyError):
        load_policy(path)


def test_default_company_is_in_pool_with_untested_conditions(policy):
    result = evaluate_company(_company(), policy)
    assert result.status == STATUS_IN_POOL
    assert len(result.untested) == 3
    assert result.failing == []
    assert result.pending == []
    assert all(item.status == CONDITION_PASS for item in result.conditions if item.status in (CONDITION_PASS, CONDITION_FAIL))


def test_roe_average_exact_threshold_passes(policy):
    result = evaluate_company(_company(), policy)
    condition = _condition(result, "roe_average")
    assert condition.value == pytest.approx(0.15)
    assert condition.status == CONDITION_PASS


def test_roe_average_just_below_threshold_fails(policy):
    years = {year: _year(year, netprofit=14.99) for year in range(2016, 2026)}
    result = evaluate_company(_company(years_override=years), policy)
    condition = _condition(result, "roe_average")
    assert condition.status == CONDITION_FAIL
    assert condition.distance_abs < 0


def test_roe_consistency_boundary(policy):
    years = {year: _year(year) for year in range(2016, 2026)}
    for year in range(2016, 2018):
        years[year] = _year(year, netprofit=5.0)
    result = evaluate_company(_company(years_override=years), policy)
    condition = _condition(result, "roe_consistency")
    assert condition.value == pytest.approx(0.8)
    assert condition.status == CONDITION_PASS

    years[2018] = _year(2018, netprofit=5.0)
    result = evaluate_company(_company(years_override=years), policy)
    assert _condition(result, "roe_consistency").status == CONDITION_FAIL


def test_roe_recent_boundary(policy):
    years = {year: _year(year, netprofit=12.0) for year in range(2023, 2026)}
    result = evaluate_company(_company(years_override=years), policy)
    condition = _condition(result, "roe_recent")
    assert condition.value == pytest.approx(0.12)
    assert condition.status == CONDITION_PASS

    years[2025] = _year(2025, netprofit=11.99)
    result = evaluate_company(_company(years_override=years), policy)
    assert _condition(result, "roe_recent").status == CONDITION_FAIL


def test_cash_conversion_boundary(policy):
    years = {year: _year(year, netprofit=20.0, ocf=16.0) for year in range(2016, 2026)}
    result = evaluate_company(_company(years_override=years), policy)
    condition = _condition(result, "cash_conversion")
    assert condition.value == pytest.approx(0.8)
    assert condition.status == CONDITION_PASS

    years[2025] = _year(2025, netprofit=20.0, ocf=15.99)
    result = evaluate_company(_company(years_override=years), policy)
    assert _condition(result, "cash_conversion").status == CONDITION_FAIL


def test_free_cash_flow_must_be_strictly_positive(policy):
    years = {year: _year(year, ocf=10.0, capex=10.0) for year in range(2016, 2026)}
    result = evaluate_company(_company(years_override=years), policy)
    condition = _condition(result, "fcf_positive")
    assert condition.value == pytest.approx(0.0)
    assert condition.status == CONDITION_FAIL


def test_loss_year_fails(policy):
    years = {year: _year(year) for year in range(2016, 2026)}
    years[2019] = _year(2019, netprofit=-1.0)
    result = evaluate_company(_company(years_override=years), policy)
    condition = _condition(result, "loss_years")
    assert condition.value == 1
    assert condition.status == CONDITION_FAIL


def test_net_debt_to_ebitda_strict_boundary(policy):
    years = {year: _year(year) for year in range(2016, 2026)}
    years[2025] = _year(2025, interest_debt=30.0, monetary_funds=10.0, total_profit=10.0)
    result = evaluate_company(_company(years_override=years), policy)
    condition = _condition(result, "net_debt_ebitda")
    assert condition.value == pytest.approx(2.0)
    assert condition.status == CONDITION_FAIL

    years[2025] = _year(2025, interest_debt=29.99, monetary_funds=10.0, total_profit=10.0)
    result = evaluate_company(_company(years_override=years), policy)
    assert _condition(result, "net_debt_ebitda").status == CONDITION_PASS


def test_dividend_payout_boundary(policy):
    result = evaluate_company(_company(), policy)
    condition = _condition(result, "dividend_payout")
    assert condition.value == pytest.approx(0.2)
    assert condition.status == CONDITION_PASS

    result = evaluate_company(
        _company(dividends={year: 2.99 for year in range(2021, 2026)}), policy
    )
    assert _condition(result, "dividend_payout").status == CONDITION_FAIL


def test_missing_year_does_not_default_pass(policy):
    years = {year: _year(year) for year in range(2016, 2026)}
    result = evaluate_company(_company(years_override=years, remove_years=(2020,)), policy)
    assert result.status in (STATUS_PENDING, STATUS_NOT_IN_POOL)
    assert result.status != STATUS_IN_POOL
    assert _condition(result, "roe_average").status in (CONDITION_FAIL, CONDITION_PENDING)


def test_missing_field_does_not_default_pass(policy):
    years = {year: _year(year) for year in range(2016, 2026)}
    years[2025] = replace(years[2025], operating_cashflow=None)
    result = evaluate_company(_company(years_override=years), policy)
    assert result.status != STATUS_IN_POOL
    assert _condition(result, "cash_conversion").status == CONDITION_PENDING


def test_financial_company_is_out_of_scope(policy):
    result = evaluate_company(
        _company(industry="银行", financial_excluded=True, financial_exclusion_reason="银行"),
        policy,
    )
    assert result.status == STATUS_OUT_OF_SCOPE
    assert result.metrics["out_of_scope_reason"] == OUT_OF_SCOPE_FINANCIAL


def test_st_company_is_out_of_scope(policy):
    result = evaluate_company(_company(is_st=True), policy)
    assert result.status == STATUS_OUT_OF_SCOPE
    assert result.metrics["out_of_scope_reason"] == OUT_OF_SCOPE_ST


def test_short_history_is_out_of_scope(policy):
    result = evaluate_company(_company(expected_years=tuple(range(2020, 2026))), policy)
    assert result.status == STATUS_OUT_OF_SCOPE
    assert result.metrics["out_of_scope_reason"] == OUT_OF_SCOPE_SHORT_HISTORY


def test_financial_condition_is_untested_not_passed(policy):
    result = evaluate_company(_company(), policy)
    audit = _condition(result, "audit_opinion")
    assert audit.status == CONDITION_UNTESTED
    assert audit.value is None
    assert "检验" in (audit.reason or "")


def test_near_miss_and_funnel(policy):
    good = _company()
    near = _company(code="600001", name="差一点", dividends={year: 1.0 for year in range(2021, 2026)})
    bad = _company(code="600002", name="差很多", years_override={2020: _year(2020, netprofit=-1.0)})
    run = evaluate_pool([good, near, bad], policy, as_of="2026-11-12", snapshot_id="qps-test")
    assert run.counts[STATUS_IN_POOL] == 1
    assert run.counts[STATUS_NOT_IN_POOL] == 2
    assert len(run.near_misses) == 1
    assert run.near_misses[0]["code"] == "600001"
    assert run.sensitivity["pledge_ratio_max"]["applicable"] is False
    values = [item["in_pool"] for item in run.sensitivity["roe_average_min"]["values"]]
    assert values == [1, 1, 0]


def test_snapshot_directory_round_trip_and_tamper_detection(tmp_path, policy):
    universe = tmp_path / "universe.csv.gz"
    write_table(
        universe,
        [{"code": "600000", "name": "测试公司", "exchange": "SH", "board": "主板", "listing_date": "2010-01-01", "industry_csrc": ""}],
        ["code", "name", "exchange", "board", "listing_date", "industry_csrc"],
        metadata={"kind": "universe", "source_id": "test", "publisher": "test", "interface": "test", "url": "test"},
    )
    paths = [universe]
    for year in range(2015, 2026):
        path = tmp_path / f"balance-{year}.csv.gz"
        write_table(
            path,
            [{"SECUCODE": "600000.SH", "TOTAL_PARENT_EQUITY": 100.0, "MONETARYFUNDS": 10.0}],
            ["SECUCODE", "TOTAL_PARENT_EQUITY", "MONETARYFUNDS"],
            metadata={"kind": "balance", "report_date": f"{year}-12-31", "source_id": "test", "publisher": "test", "interface": "test", "url": "test"},
        )
        paths.append(path)
        if year >= 2016:
            income = tmp_path / f"income-{year}.csv.gz"
            write_table(
                income,
                [{"SECUCODE": "600000.SH", "PARENT_NETPROFIT": 15.0, "TOTAL_PROFIT": 15.0, "FE_INTEREST_EXPENSE": 0.0, "FINANCE_EXPENSE": 0.0}],
                ["SECUCODE", "PARENT_NETPROFIT", "TOTAL_PROFIT", "FE_INTEREST_EXPENSE", "FINANCE_EXPENSE"],
                metadata={"kind": "income", "report_date": f"{year}-12-31", "source_id": "test", "publisher": "test", "interface": "test", "url": "test"},
            )
            cashflow = tmp_path / f"cashflow-{year}.csv.gz"
            write_table(
                cashflow,
                [{"SECUCODE": "600000.SH", "NETCASH_OPERATE": 15.0, "CONSTRUCT_LONG_ASSET": 0.0}],
                ["SECUCODE", "NETCASH_OPERATE", "CONSTRUCT_LONG_ASSET"],
                metadata={"kind": "cashflow", "report_date": f"{year}-12-31", "source_id": "test", "publisher": "test", "interface": "test", "url": "test"},
            )
            paths.extend([income, cashflow])
    dividends = tmp_path / "dividends-2025-12-31.csv.gz"
    write_table(
        dividends,
        [
            {"SECUCODE": "600000.SH", "REPORT_DATE": f"{year}-12-31", "PRETAX_BONUS_RMB": 0.3, "TOTAL_SHARES": 100.0, "ASSIGN_PROGRESS": "实施分配", "IMPL_PLAN_PROFILE": "年度", "NOTICE_DATE": f"{year + 1}-04-01"}
            for year in range(2021, 2026)
        ],
        ["SECUCODE", "REPORT_DATE", "PRETAX_BONUS_RMB", "TOTAL_SHARES", "ASSIGN_PROGRESS", "IMPL_PLAN_PROFILE", "NOTICE_DATE"],
        metadata={"kind": "dividends", "report_date": "2025-12-31", "source_id": "test", "publisher": "test", "interface": "test", "url": "test"},
    )
    paths.append(dividends)

    target, manifest = build_snapshot_dir(
        paths,
        parent=tmp_path / "snapshots",
        as_of="2026-11-12",
        window={"start_fy": 2016, "end_fy": 2025, "min_years": 7},
        scope={"exclude_financial_industries": ["银行"]},
        sources=[{"id": "test"}],
        untested_conditions=[],
    )
    loaded_manifest, companies, _stats = load_snapshot_dir(target)
    assert loaded_manifest["snapshot_id"] == manifest["snapshot_id"]
    assert len(companies) == 1
    result = evaluate_company(companies[0], policy)
    assert result.status == STATUS_IN_POOL

    # 篡改任意一张表，清单哈希校验必须失败。
    table = next(target.glob("balance-2020.csv.gz"))
    table.write_bytes(table.read_bytes() + b"tamper")
    with pytest.raises(SnapshotError):
        load_snapshot_dir(target)


def test_dividend_aggregation_sums_interim_and_annual():
    rows = [
        {"SECUCODE": "600000.SH", "REPORT_DATE": "2025-06-30", "PRETAX_BONUS_RMB": 1.0, "TOTAL_SHARES": 100, "ASSIGN_PROGRESS": "实施分配", "IMPL_PLAN_PROFILE": "中期", "NOTICE_DATE": "2025-08-01"},
        {"SECUCODE": "600000.SH", "REPORT_DATE": "2025-12-31", "PRETAX_BONUS_RMB": 2.0, "TOTAL_SHARES": 100, "ASSIGN_PROGRESS": "实施分配", "IMPL_PLAN_PROFILE": "年度", "NOTICE_DATE": "2026-04-01"},
    ]
    result = aggregate_dividends(rows)
    assert result["600000"][2025] == pytest.approx(30.0)


def test_dividend_aggregation_ignores_cancelled():
    rows = [
        {"SECUCODE": "600000.SH", "REPORT_DATE": "2025-12-31", "PRETAX_BONUS_RMB": 2.0, "TOTAL_SHARES": 100, "ASSIGN_PROGRESS": "取消分配", "IMPL_PLAN_PROFILE": "年度", "NOTICE_DATE": "2026-04-01"},
    ]
    assert aggregate_dividends(rows) == {}


def test_is_st_name():
    assert is_st_name("*ST海航")
    assert is_st_name("ST中珠")
    assert not is_st_name("贵州茅台")


def test_pool_ledger_append_skip_conflict_and_tamper(tmp_path, monkeypatch, policy):
    import tools.run_quality_pool as runner

    ledger = tmp_path / "quality-pool.jsonl"
    monkeypatch.setattr(runner, "LEDGER", ledger)
    good = _company()
    run = evaluate_pool([good], policy, as_of="2026-11-12", snapshot_id="qps-test")
    assert runner._append_ledger(run, dry_run=False) == 0
    assert runner._append_ledger(run, dry_run=False) == 0
    assert runner.check_ledger() == 0

    lines = ledger.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["pool_codes"] = []
    lines[0] = json.dumps(row, ensure_ascii=False)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert runner.check_ledger() == 1


def test_same_snapshot_replays_identically(policy):
    companies = [_company(), _company(code="600001", name="第二家")]
    first = evaluate_pool(companies, policy, as_of="2026-11-12", snapshot_id="qps-test")
    second = evaluate_pool(companies, policy, as_of="2026-11-12", snapshot_id="qps-test")
    assert [item.to_dict() for item in first.results] == [item.to_dict() for item in second.results]


def test_official_valuation_ledger_hash_and_tamper(tmp_path, monkeypatch):
    import tools.update_official_valuation_ledger as ledger_tool

    path = tmp_path / "official.jsonl"
    monkeypatch.setattr(ledger_tool, "LEDGER", path)
    record = {
        "date": "2026-11-11",
        "index_code": "000300",
        "pe": 13.14,
        "pb": None,
        "recorded_at": "2026-11-12T20:00:00+08:00",
    }
    record["record_id"] = ledger_tool._record_id(record)
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    assert ledger_tool.check() == 0

    record["pe"] = 99.0
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    assert ledger_tool.check() == 1


def _raw_row(code: str, year: int) -> dict:
    return {
        "SECUCODE": f"{code}.SH",
        "SECURITY_CODE": code,
        "TOTAL_PARENT_EQUITY": 100.0,
        "PARENT_NETPROFIT": 15.0,
        "NETCASH_OPERATE": 15.0,
        "CONSTRUCT_LONG_ASSET": 0.0,
        "TOTAL_PROFIT": 15.0,
        "FE_INTEREST_EXPENSE": 0.0,
        "MONETARYFUNDS": 10.0,
        "SHORT_LOAN": 0.0,
    }


def test_build_companies_scope_and_coverage():
    from engine.quality.build import build_companies

    universe = [
        {"code": "600000", "name": "正常公司", "exchange": "SH", "board": "主板", "listing_date": "2010-01-01"},
        {"code": "600001", "name": "*ST风险", "exchange": "SH", "board": "主板", "listing_date": "2010-01-01"},
        {"code": "600002", "name": "某银行", "exchange": "SH", "board": "主板", "listing_date": "2010-01-01"},
        {"code": "600003", "name": "新公司", "exchange": "SH", "board": "主板", "listing_date": "2020-01-01"},
    ]
    statements = {"balance": {}, "income": {}, "cashflow": {}}
    for year in range(2015, 2026):
        statements["balance"][year] = {}
        if year >= 2016:
            statements["income"][year] = {}
            statements["cashflow"][year] = {}
    for code in ("600000", "600001", "600002"):
        for year in range(2015, 2026):
            row = _raw_row(code, year)
            statements["balance"][year][code] = row
            if year >= 2016:
                statements["income"][year][code] = row
                statements["cashflow"][year][code] = row
    for year in range(2020, 2026):
        row = _raw_row("600003", year)
        statements["balance"][year]["600003"] = row
        statements["income"][year]["600003"] = row
        statements["cashflow"][year]["600003"] = row

    companies, stats = build_companies(
        universe,
        statements,
        [],
        {},
        {"600002": "银行"},
        financial_industries={"银行", "保险", "证券", "多元金融"},
        window_start=2016,
        window_end=2025,
        min_years=7,
    )
    assert stats["universe"] == 4
    assert stats["st"] == 1
    assert stats["financial"] == 1
    assert stats["short_history"] == 1
    assert stats["evaluated"] == 1
    assert stats["complete"] == 1
    assert stats["coverage"] == 1.0
    by_code = {company.code: company for company in companies}
    assert by_code["600001"].is_st is True
    assert by_code["600002"].financial_excluded is True
    assert by_code["600003"].expected_years == tuple(range(2020, 2026))
