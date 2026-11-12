"""从原始报表行构造好公司池快照：只做字段映射、财年归集与覆盖统计。

输入是 `tools/build_quality_pool_data.py` 落盘的原始 JSONL；
输出是 `Company` 列表与可行性统计。这里不做任何入池判断。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

from engine.quality.models import Company, FinancialYear

ST_PREFIXES = ("ST", "*ST", "SST", "S*ST")


def is_st_name(name: str) -> bool:
    compact = name.replace(" ", "").upper()
    return compact.startswith(ST_PREFIXES)


def aggregate_dividends(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[int, float]]:
    """按公司/财年归集现金分红；同一方案以最新公告为准，取消/终止不计。"""
    plans: dict[tuple[str, int, str], tuple[str, float]] = {}
    for row in rows:
        code = str(row.get("SECUCODE") or "")[:6]
        if not code:
            continue
        period = str(row.get("REPORT_DATE") or "")[:10]
        if len(period) < 4:
            continue
        fiscal_year = int(period[:4])
        try:
            per_10 = float(row.get("PRETAX_BONUS_RMB"))
            shares = float(row.get("TOTAL_SHARES"))
        except (TypeError, ValueError):
            continue
        progress = str(row.get("ASSIGN_PROGRESS") or "")
        if "取消" in progress or "终止" in progress:
            continue
        profile = str(row.get("IMPL_PLAN_PROFILE") or per_10)
        key = (code, fiscal_year, profile)
        notice = str(row.get("NOTICE_DATE") or "")
        amount = per_10 / 10.0 * shares
        previous = plans.get(key)
        if previous is None or notice >= previous[0]:
            plans[key] = (notice, amount)

    totals: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for (code, fiscal_year, _), (_, amount) in plans.items():
        totals[code][fiscal_year] += amount
    return {code: dict(years) for code, years in totals.items()}


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_optional(row: Mapping[str, Any] | None, keys: Iterable[str]) -> float | None:
    if row is None:
        return None
    values = [_float(row.get(key)) for key in keys]
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present)


def _equity(row: Mapping[str, Any] | None) -> float | None:
    if row is None:
        return None
    return _float(row.get("TOTAL_PARENT_EQUITY"))


def _year_complete(
    balance: Mapping[str, Any] | None,
    income: Mapping[str, Any] | None,
    cashflow: Mapping[str, Any] | None,
) -> bool:
    if balance is None or income is None or cashflow is None:
        return False
    if _equity(balance) is None:
        return False
    if _float(income.get("PARENT_NETPROFIT")) is None:
        return False
    if _float(cashflow.get("NETCASH_OPERATE")) is None:
        return False
    if _float(cashflow.get("CONSTRUCT_LONG_ASSET")) is None:
        return False
    return True


def _financial_year(
    year: int,
    balance: Mapping[str, Any] | None,
    income: Mapping[str, Any] | None,
    cashflow: Mapping[str, Any] | None,
) -> FinancialYear:
    interest_expense = None
    if income is not None:
        interest_expense = _float(income.get("FE_INTEREST_EXPENSE"))
        if interest_expense is None:
            interest_expense = _float(income.get("FINANCE_EXPENSE"))
    depreciation = _sum_optional(cashflow, ("FA_IR_DEPR", "OILGAS_BIOLOGY_DEPR"))
    amortization = _sum_optional(
        cashflow, ("IA_AMORTIZE", "LPE_AMORTIZE", "USERIGHT_ASSET_AMORTIZE")
    )
    interest_debt = _sum_optional(
        balance,
        ("SHORT_LOAN", "NONCURRENT_LIAB_1YEAR", "LONG_LOAN", "BOND_PAYABLE", "LEASE_LIAB"),
    )
    if interest_debt is None and balance is not None:
        interest_debt = 0.0
    return FinancialYear(
        fiscal_year=year,
        parent_netprofit=_float(income.get("PARENT_NETPROFIT")) if income else None,
        revenue=_float(income.get("TOTAL_OPERATE_INCOME")) if income else None,
        parent_equity=_equity(balance),
        operating_cashflow=_float(cashflow.get("NETCASH_OPERATE")) if cashflow else None,
        capex=_float(cashflow.get("CONSTRUCT_LONG_ASSET")) if cashflow else None,
        total_profit=_float(income.get("TOTAL_PROFIT")) if income else None,
        interest_expense=interest_expense,
        depreciation=depreciation,
        amortization=amortization,
        interest_debt=interest_debt,
        monetary_funds=_float(balance.get("MONETARYFUNDS")) if balance else None,
    )


def _listing_year(listing_date: str | None, income_years: Mapping[int, Any]) -> int | None:
    if listing_date and len(str(listing_date)) >= 4 and str(listing_date)[:4].isdigit():
        return int(str(listing_date)[:4])
    if income_years:
        return min(income_years)
    return None


def build_companies(
    universe_rows: Iterable[Mapping[str, Any]],
    statements: Mapping[str, Mapping[int, Mapping[str, Mapping[str, Any]]]],
    dividend_rows: Iterable[Mapping[str, Any]],
    pledge_by_code: Mapping[str, float],
    industry_by_code: Mapping[str, str],
    *,
    financial_industries: set[str],
    window_start: int,
    window_end: int,
    min_years: int,
) -> tuple[list[Company], dict[str, Any]]:
    balance_by_year = statements.get("balance") or {}
    income_by_year = statements.get("income") or {}
    cashflow_by_year = statements.get("cashflow") or {}
    dividends = aggregate_dividends(dividend_rows)

    companies: list[Company] = []
    stats = {
        "universe": 0,
        "by_exchange": Counter(),
        "st": 0,
        "financial": 0,
        "short_history": 0,
        "evaluated": 0,
        "complete": 0,
        "incomplete": [],
        "by_year": {},
    }
    for row in universe_rows:
        code = str(row["code"]).zfill(6)
        stats["universe"] += 1
        stats["by_exchange"][row.get("exchange")] += 1
        name = str(row.get("name") or "")
        is_st = is_st_name(name)
        industry = industry_by_code.get(code) or row.get("industry_csrc") or None
        financial = bool(
            (industry and any(item in industry for item in financial_industries))
            or str(row.get("industry_csrc") or "").startswith("J")
        )
        if is_st:
            stats["st"] += 1
        if financial:
            stats["financial"] += 1

        income_years = {
            year: income_by_year.get(year, {}).get(code)
            for year in range(window_start, window_end + 1)
        }
        listing_year = _listing_year(row.get("listing_date"), {y: v for y, v in income_years.items() if v})
        start = max(window_start, listing_year or window_start)
        expected = tuple(range(start, window_end + 1)) if start <= window_end else ()
        if not financial and not is_st and len(expected) < min_years:
            stats["short_history"] += 1

        years: dict[int, FinancialYear] = {}
        prior = balance_by_year.get(window_start - 1, {}).get(code)
        if prior is not None and _equity(prior) is not None:
            years[window_start - 1] = FinancialYear(
                fiscal_year=window_start - 1, parent_equity=_equity(prior)
            )
        for year in range(window_start, window_end + 1):
            balance = balance_by_year.get(year, {}).get(code)
            income = income_by_year.get(year, {}).get(code)
            cashflow = cashflow_by_year.get(year, {}).get(code)
            if balance is None and income is None and cashflow is None:
                continue
            years[year] = _financial_year(year, balance, income, cashflow)

        incomplete_years = []
        for year in expected:
            balance = balance_by_year.get(year, {}).get(code)
            income = income_by_year.get(year, {}).get(code)
            cashflow = cashflow_by_year.get(year, {}).get(code)
            if not _year_complete(balance, income, cashflow):
                incomplete_years.append(year)
        if expected and expected[0] == window_start:
            if window_start - 1 not in years:
                incomplete_years.append(window_start - 1)

        if not financial and not is_st and len(expected) >= min_years:
            stats["evaluated"] += 1
            if not incomplete_years:
                stats["complete"] += 1
            elif len(stats["incomplete"]) < 50:
                stats["incomplete"].append({"code": code, "name": name, "years": incomplete_years})

        companies.append(
            Company(
                code=code,
                name=name,
                exchange=str(row.get("exchange") or ""),
                board=str(row.get("board") or ""),
                industry=industry,
                listing_date=row.get("listing_date"),
                is_st=is_st,
                financial_excluded=financial,
                financial_exclusion_reason=industry if financial else None,
                expected_years=expected,
                years=years,
                dividends={year: dividends.get(code, {}).get(year, 0.0) for year in range(2021, 2026)},
                pledge_company_ratio=pledge_by_code.get(code),
            )
        )

    # 逐年覆盖率：分母是“期望包含该年”的评价公司。
    for year in range(window_start, window_end + 1):
        expected_companies = [
            company
            for company in companies
            if not company.financial_excluded and not company.is_st and year in company.expected_years
        ]
        complete = 0
        for company in expected_companies:
            balance = balance_by_year.get(year, {}).get(company.code)
            income = income_by_year.get(year, {}).get(company.code)
            cashflow = cashflow_by_year.get(year, {}).get(company.code)
            if _year_complete(balance, income, cashflow):
                complete += 1
        stats["by_year"][year] = {
            "expected": len(expected_companies),
            "complete": complete,
            "coverage": complete / len(expected_companies) if expected_companies else None,
        }

    stats["by_exchange"] = dict(stats["by_exchange"])
    stats["coverage"] = (
        stats["complete"] / stats["evaluated"] if stats["evaluated"] else None
    )
    companies.sort(key=lambda item: item.code)
    return companies, stats
