"""政策驱动的估值输入与三档情景计算。

本模块只实现政策 JSON 里的规则，不设置任何业务数字：
- 折现率：规范化无风险利率 + 股权风险溢价；WACC 用市值权重；
- 预测：5 年，第 1 年用已披露累计同比，第 2–5 年受 5 年复合增速与 10% 上限约束；
- 终值：g 受 2.5% 与无风险利率上限、ROE×留存率约束；再投资率 = g / 新投资回报率，
  终值现金流 = 末期 NOPAT（或 EPS）× (1+g) × (1 − g/ROI)；
- 三档情景共用同一折现率，只改变增速与终值参数。

政策未规定、由实现方机械补齐的输入（3 年算术平均等）在政策文件
`mechanical_implementation` 中登记，并随试运行报告披露。
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from engine.financials.annual import AnnualFinancials, FinancialHistory
from engine.financials.bridge import build_fcff_bridge
from engine.financials.dividends import DividendBook, par_value, year_shares
from engine.policy.equity import EquityPolicy
from engine.valuation.ddm import DividendDrivers, project_dividends
from engine.valuation.discounting import TerminalValue, discount_stream
from engine.valuation.fcff import EquityBridge, FcffDrivers, project_fcff, value_fcff

SCENARIO_NAMES = ("pessimistic", "base", "optimistic")


class PolicyModelError(RuntimeError):
    """政策模型拒绝计算：必须说明缺什么，不给数字。"""


# --- 宏观 -------------------------------------------------------------------
def load_macro_series(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise PolicyModelError(f"找不到无风险利率序列: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "macro-series/1":
        raise PolicyModelError(f"未知的宏观序列 schema: {data.get('schema_version')!r}")
    return data


def normalized_risk_free(policy: EquityPolicy, macro: dict[str, Any]) -> dict[str, Any]:
    """max(最新值, 近 N 个月月末均值)，返回小数形式的年化收益率。"""
    months = int(policy.node("discount_rate.risk_free")["lookback_months"])
    observations = list(macro.get("month_end_observations") or [])
    if len(observations) < months:
        raise PolicyModelError(
            f"月末观测 {len(observations)} 个，少于政策要求的 {months} 个"
        )
    window = observations[-months:]
    mean_pct = statistics.fmean(float(o["value_pct"]) for o in window)
    latest_pct = float(macro["latest"]["value_pct"])
    chosen_pct = max(latest_pct, mean_pct)
    return {
        "rule": policy.node("discount_rate.risk_free")["rule"],
        "series_id": macro.get("series_id"),
        "series_file": policy.node("discount_rate.risk_free")["series_file"],
        "latest": {"date": macro["latest"]["date"], "pct": latest_pct},
        "month_end_mean": {
            "start": window[0]["date"],
            "end": window[-1]["date"],
            "months": len(window),
            "pct": mean_pct,
        },
        "normalized_pct": chosen_pct,
        "normalized": chosen_pct / 100.0,
        "chosen_because": "latest" if latest_pct >= mean_pct else "month_end_mean",
    }


# --- 财务比率 ---------------------------------------------------------------
def _annual(history: FinancialHistory, fiscal_year: int) -> AnnualFinancials | None:
    return history.year(fiscal_year)


def _num(annual: AnnualFinancials | None, name: str) -> float | None:
    if annual is None:
        return None
    return annual.number(name)


def _mean(values: list[float]) -> float | None:
    usable = [v for v in values if v is not None]
    return statistics.fmean(usable) if usable else None


def _average_over_years(
    history: FinancialHistory,
    base_year: int,
    window: int,
    fn: Callable[[AnnualFinancials], float | None],
) -> tuple[float | None, list[str]]:
    values: list[float] = []
    used: list[str] = []
    for offset in range(window):
        year = base_year - offset
        annual = _annual(history, year)
        if annual is None:
            continue
        value = fn(annual)
        if value is None:
            continue
        values.append(value)
        used.append(str(year))
    if not values:
        return None, []
    return statistics.fmean(values), used


def _ebit(annual: AnnualFinancials) -> float | None:
    pretax = annual.number("pretax_profit")
    if pretax is None:
        return None
    finance = annual.number("finance_expense")
    return pretax + (finance if finance is not None else 0.0)


def _da(annual: AnnualFinancials) -> float:
    parts = (
        "depreciation",
        "amortization_intangible",
        "amortization_lt_prepaid",
        "amortization_right_of_use",
    )
    return sum(annual.number(name) or 0.0 for name in parts)


def _receivables(annual: AnnualFinancials) -> float | None:
    value = annual.number("receivables_notes")
    if value is None:
        value = annual.number("receivables")
    return value


def _payables(annual: AnnualFinancials) -> float | None:
    value = annual.number("payables_notes")
    if value is None:
        value = annual.number("payables")
    return value


@dataclass(frozen=True)
class CompanyInputs:
    instrument_id: str
    base_year: int
    shares: float
    price: float
    revenue: float
    ebit_margin: float
    ebit_margin_years: tuple[str, ...]
    tax_rate: float
    tax_rate_years: tuple[str, ...]
    da_pct: float
    capex_pct: float
    nwc_intensity: float
    payout: float
    payout_years: tuple[int, ...]
    roe_latest: float | None
    roe_5y: float | None
    revenue_cagr5: float | None
    year1_growth: float
    year1_basis: str
    debt: float
    debt_components: dict[str, float]
    missing_debt_components: tuple[str, ...]
    average_debt: float
    interest_expense: float | None
    monetary_funds: float | None
    cash_equivalents_end: float | None
    cash_equivalents_basis: str | None
    non_cash_equivalents: float | None
    operating_cash_need: float
    available_cash: float
    minority_equity: float
    net_profit: float | None
    net_profit_parent: float | None
    minority_profit: float | None
    loan_advance: float | None
    accept_deposit_interbank: float | None
    total_assets: float | None
    ocf_5y: float
    profit_5y: float
    loss_years: int
    available_years: int
    ebitda: float | None
    notes: tuple[str, ...] = field(default_factory=tuple)
    loss_years_7: int = 0
    revenue_cagr3: float | None = None
    ebit_margin_min_5y: float | None = None
    net_margin_base: float | None = None
    net_margin_min_5y: float | None = None
    cash_equivalents_component: float | None = None
    loss_missing_10: tuple[int, ...] = ()
    loss_missing_7: tuple[int, ...] = ()
    ocf_missing_years: tuple[int, ...] = ()
    dividend_3y_total: float | None = None
    ocf_capex_3y_total: float | None = None
    dividend_missing_years: tuple[int, ...] = ()
    payout_missing_years: tuple[int, ...] = ()
    nwc_missing: bool = False
    interest_missing: bool = False


def _payout_stats(
    book: DividendBook | None,
    history: FinancialHistory,
    shares: float,
    base_year: int,
    window: int,
) -> tuple[float, tuple[int, ...], tuple[int, ...], list[str]]:
    """某财年派息率 = 每股派息 ÷ 该财年基本每股收益。

    这等价于「现金分红总额 ÷ 归母净利润」：基本每股收益 = 归母净利润 ÷
    该财年加权股数，所以每股派息 ÷ EPS 与用当年股数还原总额后相除相同，
    不会像「每股派息 ÷（归母净利润 ÷ 当前股本）」那样在送转后失真（R10）。
    """
    if book is None:
        return 0.0, (), (), ["没有分红账本：派息率按 0 处理"]
    notes: list[str] = []
    par = par_value(history, base_year)
    if par is None:
        notes.append("无法由股本与每股收益推出面值：派息率退回「每股派息 ÷ 基本每股收益」口径")
    by_year = {entry.fiscal_year: entry for entry in book.years}
    pays: list[float] = []
    years: list[int] = []
    missing: list[int] = []
    for offset in range(window):
        year = base_year - offset
        entry = by_year.get(year)
        annual = _annual(history, year)
        if entry is None or annual is None or not entry.has_annual_final or entry.ordinary_per_share <= 0:
            missing.append(year)
            continue
        profit = annual.number("net_profit_parent")
        shares_year = year_shares(annual, par)
        if profit is None or profit <= 0 or shares_year is None or shares_year <= 0:
            missing.append(year)
            continue
        pays.append(entry.ordinary_per_share * shares_year / profit)
        years.append(year)
    if not pays:
        notes.append("近 3 个财年没有可用的年度现金分红：派息率按 0 处理")
        return 0.0, (), tuple(missing), notes
    if missing:
        notes.append(
            "派息率窗口缺少年份（该年无年度分红、无归母净利润或基本每股收益）："
            + "、".join(str(y) for y in missing)
        )
    return statistics.fmean(pays), tuple(years), tuple(missing), notes


def extract_inputs(
    *,
    instrument_id: str,
    snapshot: Any,
    history: FinancialHistory,
    book: DividendBook | None,
    shares: float,
    price: float,
    base_year: int,
    policy: EquityPolicy,
) -> CompanyInputs:
    """把财务事实整理成政策需要的比率；缺哪项就报出哪项，不做默认填充。"""
    base = _annual(history, base_year)
    if base is None:
        raise PolicyModelError(f"没有 {base_year} 年年报，无法作为基准财年")
    revenue = base.number("revenue")
    if revenue is None or revenue <= 0:
        raise PolicyModelError(f"{base_year} 年营业收入不可用或非正")

    window = int(policy.value("forecast.statement_ratios_window"))
    ebit_margin, ebit_years = _average_over_years(
        history, base_year, window, lambda a: (_ebit(a) / a.number("revenue")) if a.number("revenue") else None
    )
    if ebit_margin is None:
        raise PolicyModelError("缺少 EBIT 利润率（利润总额/财务费用/营业收入）")

    tax_node = policy.node("forecast.tax_rate")
    tax_cap = float(tax_node["cap"])
    tax_floor = float(tax_node["floor"])

    def _tax(a: AnnualFinancials) -> float | None:
        pretax = a.number("pretax_profit")
        tax = a.number("income_tax")
        if pretax is None or tax is None or pretax == 0:
            return None
        return max(tax_floor, min(tax / pretax, tax_cap))

    tax_rate, tax_years = _average_over_years(history, base_year, window, _tax)
    if tax_rate is None:
        raise PolicyModelError("缺少实际税率（所得税费用/利润总额）")

    da_pct, _ = _average_over_years(
        history, base_year, window, lambda a: _da(a) / a.number("revenue") if a.number("revenue") else None
    )
    capex_pct, _ = _average_over_years(
        history,
        base_year,
        window,
        lambda a: (a.number("capex") / a.number("revenue"))
        if a.number("capex") is not None and a.number("revenue")
        else None,
    )
    if da_pct is None or capex_pct is None:
        raise PolicyModelError("缺少折旧摊销或资本开支（现金流量表）")

    def _nwc(a: AnnualFinancials) -> float | None:
        inventory = a.number("inventory")
        receivables = _receivables(a)
        payables = _payables(a)
        rev = a.number("revenue")
        if rev is None or rev <= 0:
            return None
        if inventory is None and receivables is None and payables is None:
            return None
        return ((inventory or 0.0) + (receivables or 0.0) - (payables or 0.0)) / rev

    nwc_intensity, _ = _average_over_years(history, base_year, window, _nwc)
    nwc_missing = nwc_intensity is None
    if nwc_missing:
        nwc_intensity = 0.0

    payout_window = int(policy.value("forecast.payout_window"))
    payout, payout_years, payout_missing, payout_notes = _payout_stats(
        book, history, shares, base_year, payout_window
    )

    equity = base.number("equity_parent")
    profit_parent = base.number("net_profit_parent")
    roe_latest = profit_parent / equity if profit_parent is not None and equity else None
    roe_window = int(policy.value("terminal.roe_window"))
    roe_values: list[float] = []
    for offset in range(roe_window):
        annual = _annual(history, base_year - offset)
        if annual is None:
            continue
        p = annual.number("net_profit_parent")
        e = annual.number("equity_parent")
        if p is None or e is None or e <= 0:
            continue
        roe_values.append(p / e)
    roe_5y = statistics.fmean(roe_values) if roe_values else None

    growth_window = int(policy.value("forecast.growth_window"))
    growth_window_short = int(policy.value("forecast.growth_window_short"))
    cagr5 = None
    five_years_ago = _annual(history, base_year - growth_window)
    if five_years_ago is not None:
        old_revenue = five_years_ago.number("revenue")
        if old_revenue is not None and old_revenue > 0 and revenue > 0:
            cagr5 = (revenue / old_revenue) ** (1.0 / growth_window) - 1.0
    cagr3 = None
    three_years_ago = _annual(history, base_year - growth_window_short)
    if three_years_ago is not None:
        old_revenue = three_years_ago.number("revenue")
        if old_revenue is not None and old_revenue > 0 and revenue > 0:
            cagr3 = (revenue / old_revenue) ** (1.0 / growth_window_short) - 1.0

    cap = policy.value("forecast.growth_cap")
    year1_growth, year1_basis = _year1_growth(
        history, snapshot, base_year, cap, growth_window=growth_window
    )

    # 有息负债
    debt_components: dict[str, float] = {}
    missing_debt: list[str] = []
    for name in policy.node("discount_rate.cost_of_debt")["interest_bearing_debt"]:
        value = base.number(name)
        if value is None:
            missing_debt.append(name)
            continue
        debt_components[name] = value
    debt = sum(debt_components.values())

    previous = _annual(history, base_year - 1)
    previous_debt = 0.0
    if previous is not None:
        for name in policy.node("discount_rate.cost_of_debt")["interest_bearing_debt"]:
            previous_debt += previous.number(name) or 0.0
    average_debt = (debt + previous_debt) / 2.0

    interest = base.number("interest_expense")
    if interest is None:
        interest = base.number("finance_expense")
    interest_missing = interest is None
    if interest is not None:
        interest = abs(interest)

    monetary = base.number("cash")
    # R3：期末现金余额只用 END_CCE（旧字段 cash_flow_end_cce 是同一口径的备份）。
    cash_equiv = base.number("cash_equivalents_end")
    if cash_equiv is None:
        cash_equiv = base.number("cash_flow_end_cce")
    if cash_equiv is not None and cash_equiv <= 0:
        cash_equiv = None
    component = base.number("cash_equivalents_component")
    # R4：「非现金等价物货币资金（多为定期存款）」= 货币资金 − END_CCE。
    # 只披露、不计入等级（它只会让价值变高），但仍从可抵扣现金中扣除（保守）。
    non_cash = None
    if monetary is not None and cash_equiv is not None:
        non_cash = max(0.0, monetary - cash_equiv)
    notes: list[str] = []
    operating_need = revenue * policy.value("quality.cash.operating_cash_need")
    if monetary is not None:
        available_cash = max(0.0, monetary - (non_cash or 0.0) - operating_need)
    else:
        available_cash = 0.0

    minority = base.number("minority_equity") or 0.0
    net_profit = base.number("net_profit")
    minority_profit = (
        net_profit - profit_parent
        if net_profit is not None and profit_parent is not None
        else None
    )

    ocf_window = int(policy.node("grade.A")["ocf_window_years"])
    ocf_sum = 0.0
    profit_sum = 0.0
    ocf_years = 0
    ocf_missing: list[int] = []
    for offset in range(ocf_window):
        year = base_year - offset
        annual = _annual(history, year)
        ocf = annual.number("ocf") if annual is not None else None
        p = annual.number("net_profit_parent") if annual is not None else None
        if ocf is None or p is None:
            ocf_missing.append(year)
            continue
        ocf_sum += ocf
        profit_sum += p
        ocf_years += 1

    available_years = len(history.years)
    lookback = int(policy.node("grade.A")["lookback_fiscal_years"])
    loss_years = 0
    loss_missing_10: list[int] = []
    for offset in range(lookback):
        year = base_year - offset
        annual = _annual(history, year)
        p = annual.number("net_profit_parent") if annual is not None else None
        if p is None:
            loss_missing_10.append(year)
            continue
        if p < 0:
            loss_years += 1

    seven_year_window = int(policy.node("grade.B")["lookback_fiscal_years"])
    loss_years_7 = 0
    loss_missing_7: list[int] = []
    for offset in range(seven_year_window):
        year = base_year - offset
        annual = _annual(history, year)
        p = annual.number("net_profit_parent") if annual is not None else None
        if p is None:
            loss_missing_7.append(year)
            continue
        if p < 0:
            loss_years_7 += 1

    ebit_base = _ebit(base)
    ebitda = (ebit_base + _da(base)) if ebit_base is not None else None

    # R8：悲观情景的利润率下行输入（近 5 个财年最低）。
    ebit_margin_values: list[float] = []
    net_margin_values: list[float] = []
    margin_window = int(policy.node("scenarios.pessimistic.ebit_margin")["window_years"])
    for offset in range(margin_window):
        annual = _annual(history, base_year - offset)
        if annual is None:
            continue
        rev = annual.number("revenue")
        ebit = _ebit(annual)
        profit = annual.number("net_profit_parent")
        if rev is not None and rev > 0 and ebit is not None:
            ebit_margin_values.append(ebit / rev)
        if rev is not None and rev > 0 and profit is not None:
            net_margin_values.append(profit / rev)
    ebit_margin_min_5y = min(ebit_margin_values) if ebit_margin_values else None
    net_margin_min_5y = min(net_margin_values) if net_margin_values else None
    net_margin_base = (
        profit_parent / revenue
        if profit_parent is not None and revenue > 0
        else None
    )

    # R6：股息路线的 3 年分红覆盖条件（分红总额 ≤ 经营现金流 − 资本开支）。
    coverage_window = int(policy.node("grade.dividend_coverage")["window_years"])
    par = par_value(history, base_year)
    dividend_total = 0.0
    ocf_capex_total = 0.0
    dividend_missing: list[int] = []
    for offset in range(coverage_window):
        year = base_year - offset
        annual = _annual(history, year)
        entry = book.year(year) if book is not None else None
        profit = annual.number("net_profit_parent") if annual is not None else None
        ocf = annual.number("ocf") if annual is not None else None
        capex = annual.number("capex") if annual is not None else None
        shares_year = year_shares(annual, par)
        if (
            annual is None
            or entry is None
            or not entry.has_annual_final
            or profit is None
            or profit <= 0
            or shares_year is None
            or ocf is None
            or capex is None
        ):
            dividend_missing.append(year)
            continue
        dividend_total += entry.ordinary_per_share * shares_year
        ocf_capex_total += ocf - capex
    dividend_3y_total = None if dividend_missing else dividend_total
    ocf_capex_3y_total = None if dividend_missing else ocf_capex_total

    notes.extend(payout_notes)
    if missing_debt:
        notes.append("报表未列示的有息负债科目按零处理：" + "、".join(missing_debt))
    if cagr5 is None:
        notes.append("缺 5 年前营业收入：第 2–5 年增速的复合增速上限不可用")
    if non_cash is None:
        notes.append("缺货币资金或期末现金及现金等价物：非现金等价物货币资金无法估算")
    if nwc_missing:
        notes.append("营运资本强度不可用，按 0 处理（R13，报告中列出）")
    if interest_missing:
        notes.append("利息费用缺失，债务成本取下限（R13，报告中列出）")

    return CompanyInputs(
        instrument_id=instrument_id,
        base_year=base_year,
        shares=shares,
        price=price,
        revenue=revenue,
        ebit_margin=ebit_margin,
        ebit_margin_years=tuple(ebit_years),
        tax_rate=tax_rate,
        tax_rate_years=tuple(tax_years),
        da_pct=da_pct,
        capex_pct=capex_pct,
        nwc_intensity=nwc_intensity,
        payout=payout,
        payout_years=payout_years,
        roe_latest=roe_latest,
        roe_5y=roe_5y,
        revenue_cagr5=cagr5,
        year1_growth=year1_growth,
        year1_basis=year1_basis,
        debt=debt,
        debt_components=debt_components,
        missing_debt_components=tuple(missing_debt),
        average_debt=average_debt,
        interest_expense=interest,
        monetary_funds=monetary,
        cash_equivalents_end=cash_equiv,
        cash_equivalents_basis="END_CCE" if cash_equiv is not None else None,
        non_cash_equivalents=non_cash,
        operating_cash_need=operating_need,
        available_cash=available_cash,
        minority_equity=minority,
        net_profit=net_profit,
        net_profit_parent=profit_parent,
        minority_profit=minority_profit,
        loan_advance=base.number("loan_advance"),
        accept_deposit_interbank=base.number("accept_deposit_interbank"),
        total_assets=base.number("total_assets"),
        ocf_5y=ocf_sum,
        profit_5y=profit_sum,
        loss_years=loss_years,
        available_years=available_years,
        ebitda=ebitda,
        notes=tuple(notes),
        loss_years_7=loss_years_7,
        revenue_cagr3=cagr3,
        ebit_margin_min_5y=ebit_margin_min_5y,
        net_margin_base=net_margin_base,
        net_margin_min_5y=net_margin_min_5y,
        cash_equivalents_component=component,
        loss_missing_10=tuple(loss_missing_10),
        loss_missing_7=tuple(loss_missing_7),
        ocf_missing_years=tuple(ocf_missing),
        dividend_3y_total=dividend_3y_total,
        ocf_capex_3y_total=ocf_capex_3y_total,
        dividend_missing_years=tuple(dividend_missing),
        payout_missing_years=payout_missing,
        nwc_missing=nwc_missing,
        interest_missing=interest_missing,
    )


def _year1_growth(
    history: FinancialHistory, snapshot: Any, base_year: int, cap: float, *, growth_window: int
) -> tuple[float, str]:
    """第 1 年增速：优先最近一期累计营收同比；取不到退回受上限约束的复合增速。"""
    latest_period = f"{base_year + 1}-09-30"
    base_period = f"{base_year}-09-30"
    latest = _fact_number(snapshot, latest_period, "revenue")
    base = _fact_number(snapshot, base_period, "revenue")
    if latest is not None and base is not None and base > 0:
        raw = latest / base - 1.0
        capped = min(raw, cap)
        suffix = f"；受 {cap:.0%} 上限约束" if capped < raw else ""
        return capped, (
            f"{latest_period} 累计营业收入 / {base_period} 累计营业收入 − 1" + suffix
        )
    # 退回 5 年复合增速（受上限约束）
    five_years_ago = history.year(base_year - growth_window)
    current = history.year(base_year)
    if five_years_ago is not None and current is not None:
        old = five_years_ago.number("revenue")
        new = current.number("revenue")
        if old is not None and old > 0 and new is not None and new > 0:
            cagr = (new / old) ** (1.0 / growth_window) - 1.0
            return min(cagr, cap), "未取得同期累计营收：退回受上限约束的 5 年复合增速"
    return 0.0, "未取得同期累计营收且无 5 年复合增速：第 1 年增速按 0"


def _fact_number(snapshot: Any, period: str, suffix: str) -> float | None:
    """直接读快照里的指定报告期事实（含非年报期，FinancialHistory 只保留年报）。"""
    if snapshot is None:
        return None
    fact = snapshot.fact(f"fin.{period}.{suffix}")
    if fact is None or not fact.is_usable_number:
        return None
    return float(fact.value)


# --- 折现率 -----------------------------------------------------------------
@dataclass(frozen=True)
class DiscountBuild:
    risk_free: float
    equity_risk_premium: float
    company_premium: float
    cost_of_equity: float
    cost_of_debt: float
    cost_of_debt_display: str
    tax_rate: float
    debt: float
    net_debt: float
    market_cap: float
    equity_weight: float
    debt_weight: float
    wacc: float
    interest_expense: float | None
    net_cash: bool
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_free": self.risk_free,
            "equity_risk_premium": self.equity_risk_premium,
            "company_specific_premium": self.company_premium,
            "cost_of_equity": self.cost_of_equity,
            "cost_of_debt": self.cost_of_debt,
            "cost_of_debt_display": self.cost_of_debt_display,
            "tax_rate": self.tax_rate,
            "debt": self.debt,
            "net_debt": self.net_debt,
            "market_cap": self.market_cap,
            "equity_weight": self.equity_weight,
            "debt_weight": self.debt_weight,
            "wacc": self.wacc,
            "interest_expense": self.interest_expense,
            "net_cash": self.net_cash,
            "notes": list(self.notes),
        }


def net_interest_bearing_debt(policy: EquityPolicy, inputs: CompanyInputs) -> float:
    """R9：净有息负债 = max(0, 有息负债 − 可抵扣现金)。"""
    floor = float(policy.node("grade.net_debt")["floor"])
    return max(floor, inputs.debt - inputs.available_cash)


def build_discount(
    policy: EquityPolicy, inputs: CompanyInputs, *, risk_free: float
) -> DiscountBuild:
    erp = policy.value("discount_rate.equity_risk_premium")
    company = policy.value("discount_rate.company_specific_premium")
    ke = risk_free + erp + company

    notes: list[str] = []
    if inputs.average_debt > 0 and inputs.interest_expense is not None:
        raw_kd = inputs.interest_expense / inputs.average_debt
    else:
        raw_kd = risk_free
        notes.append("平均有息负债为零或利息费用缺失：债务成本取下限（规范化无风险利率）")
    kd = max(raw_kd, risk_free)
    if kd > raw_kd:
        notes.append(
            f"观测债务成本 {raw_kd:.2%} 低于无风险利率，按政策下限取 {risk_free:.2%}"
        )

    market_cap = inputs.price * inputs.shares
    net_debt = net_interest_bearing_debt(policy, inputs)
    total = market_cap + net_debt
    if total <= 0:
        raise PolicyModelError("权益市值与净有息负债合计非正，无法计算资本权重")
    equity_weight = market_cap / total
    debt_weight = net_debt / total
    wacc = equity_weight * ke + debt_weight * kd * (1.0 - inputs.tax_rate)

    # R14：债务权重 < 1% 时只改显示，不改计算。
    display_node = policy.node("discount_rate.cost_of_debt.display")
    threshold = float(display_node["weight_threshold"])
    cost_of_debt_display = (
        str(display_node["text_below_threshold"])
        if debt_weight < threshold
        else f"{kd:.2%}"
    )
    if net_debt == 0:
        notes.append("净现金公司：债务权重为 0，WACC = 股权成本（R9）")

    return DiscountBuild(
        risk_free=risk_free,
        equity_risk_premium=erp,
        company_premium=company,
        cost_of_equity=ke,
        cost_of_debt=kd,
        cost_of_debt_display=cost_of_debt_display,
        tax_rate=inputs.tax_rate,
        debt=inputs.debt,
        net_debt=net_debt,
        market_cap=market_cap,
        equity_weight=equity_weight,
        debt_weight=debt_weight,
        wacc=wacc,
        interest_expense=inputs.interest_expense,
        net_cash=net_debt == 0,
        notes=tuple(notes),
    )


# --- 杠杆护栏 ---------------------------------------------------------------
def leverage_ratio(net_debt: float, market_cap: float) -> float:
    """净有息负债占（净有息负债 + 权益市值）的比例；合计非正时返回 0。"""
    total = float(net_debt) + float(market_cap)
    if total <= 0:
        return 0.0
    return float(net_debt) / total


def leverage_guardrail(policy: EquityPolicy, ratio: float) -> dict[str, Any]:
    """政策护栏：超过比例上限时不给正式结论（只做研究参考）。"""
    node = policy.node("quality.leverage_guardrail")
    limit = policy.value("quality.leverage_guardrail")
    return {
        "rule": node["rule"],
        "ratio": ratio,
        "limit": limit,
        "exceeded": ratio > limit,
        "note": (
            "研究价值仅供研究参考，不用于结论"
            if ratio > limit
            else "未超过杠杆护栏"
        ),
    }


# --- 增长与终值 -------------------------------------------------------------
@dataclass(frozen=True)
class TerminalSpec:
    growth: float
    new_investment_return: float
    reinvestment_rate: float
    payout_rate: float
    requested_growth: float
    capped_by: tuple[str, ...]
    consistency_cap: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "growth": self.growth,
            "new_investment_return": self.new_investment_return,
            "reinvestment_rate": self.reinvestment_rate,
            "terminal_payout_rate": self.payout_rate,
            "requested_growth": self.requested_growth,
            "capped_by": list(self.capped_by),
            "consistency_cap": self.consistency_cap,
        }


def growth_paths(policy: EquityPolicy, inputs: CompanyInputs) -> dict[str, list[float]]:
    """R7：第 1 年封顶；稳态 = min(10%, 5 年 CAGR, 3 年 CAGR)；第 2–5 年线性过渡。"""
    horizon = int(policy.value("forecast.horizon_years"))
    if horizon < 2:
        raise PolicyModelError("预测期少于 2 年，无法做增速过渡")
    cap = policy.value("forecast.growth_cap")
    if inputs.revenue_cagr5 is None:
        raise PolicyModelError("缺 5 年复合增速，无法按政策约束预测期增长")
    bounds = [inputs.revenue_cagr5, cap]
    if inputs.revenue_cagr3 is not None:
        bounds.append(inputs.revenue_cagr3)
    steady = min(bounds)
    year1 = min(inputs.year1_growth, cap)
    base_path = [
        year1 + (steady - year1) * index / (horizon - 1) for index in range(horizon)
    ]
    adjustments = {
        name: float(policy.node(f"scenarios.{name}")["growth_adjustment"])
        for name in SCENARIO_NAMES
    }
    return {
        name: [rate + adjustments[name] for rate in base_path] for name in SCENARIO_NAMES
    }


def steady_growth(policy: EquityPolicy, inputs: CompanyInputs) -> float:
    cap = policy.value("forecast.growth_cap")
    bounds = [inputs.revenue_cagr5, cap]
    if inputs.revenue_cagr3 is not None:
        bounds.append(inputs.revenue_cagr3)
    if inputs.revenue_cagr5 is None:
        raise PolicyModelError("缺 5 年复合增速，无法计算稳态增速")
    return min(bounds)


def terminal_spec(
    policy: EquityPolicy,
    inputs: CompanyInputs,
    *,
    scenario: str,
    risk_free: float,
    cost_of_equity: float,
    discount_rate: float | None = None,
    model_id: str = "equity_dcf_fcff",
    growth_override: float | None = None,
) -> TerminalSpec:
    node = policy.node(f"scenarios.{scenario}")
    requested = float(node["terminal_growth"])
    if growth_override is not None:
        requested = growth_override
    cap = policy.value("terminal.growth_cap")
    caps: list[str] = []
    growth = requested
    upper = min(cap, risk_free)
    if growth > upper:
        growth = upper
        caps.append("growth_cap")
    retention = 1.0 - inputs.payout
    consistency = inputs.roe_latest * retention if inputs.roe_latest is not None else None
    if consistency is not None and growth > consistency:
        growth = consistency
        caps.append("roe_retention")
    if growth < 0:
        growth = 0.0
        caps.append("non_negative")

    # R1：FCFF 路线基准/悲观的新投资回报率取 WACC（与折现率同口径）；
    # 股息路线取股权成本；乐观情景取近 5 年平均 ROE。
    route = "fcff" if model_id == "equity_dcf_fcff" else "dividend"
    mapping = policy.node("terminal.reinvestment")["new_investment_return"][route]
    roi_name = node.get("new_investment_return")
    if roi_name in (None, "same_as_base"):
        roi_name = mapping[scenario]
    if roi_name == "5y_average_roe":
        roi = inputs.roe_5y if inputs.roe_5y is not None else cost_of_equity
        if inputs.roe_5y is None:
            caps.append("roe_5y_missing_use_cost_of_equity")
    elif roi_name == "wacc":
        roi = discount_rate if discount_rate is not None else cost_of_equity
    else:
        roi = cost_of_equity
    if roi <= 0:
        raise PolicyModelError(f"{scenario} 情景的新投资回报率非正（{roi}）")
    if roi <= growth:
        # 新投资回报率不高于终值增长时，再投资率会超过 100%（终值分配为负）。
        # 这不是可解释的终值：退回基准情景的折现率，并记录该退化。
        roi = discount_rate if discount_rate is not None else cost_of_equity
        caps.append("roi_not_above_growth_use_discount_rate")
    reinvestment = growth / roi
    return TerminalSpec(
        growth=growth,
        new_investment_return=roi,
        reinvestment_rate=reinvestment,
        payout_rate=1.0 - reinvestment,
        requested_growth=requested,
        capped_by=tuple(caps),
        consistency_cap=consistency,
    )


# --- 单情景估值 -------------------------------------------------------------
@dataclass(frozen=True)
class ScenarioValue:
    scenario: str
    model: str
    value_per_share: float
    terminal_share: float | None
    discount_rate: float
    terminal: TerminalSpec
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "model": self.model,
            "value_per_share": self.value_per_share,
            "terminal_share_of_value": self.terminal_share,
            "discount_rate": self.discount_rate,
            "terminal": self.terminal.to_dict(),
            "detail": self.detail,
        }


def _fcff_drivers(
    policy: EquityPolicy,
    inputs: CompanyInputs,
    growth: list[float],
    *,
    ebit_margin: float | None = None,
) -> FcffDrivers:
    horizon = len(growth)
    margin = inputs.ebit_margin if ebit_margin is None else ebit_margin
    return FcffDrivers(
        base_year=inputs.base_year,
        base_revenue=inputs.revenue,
        revenue_growth=tuple(growth),
        ebit_margin=tuple([margin] * horizon),
        tax_rate=tuple([inputs.tax_rate] * horizon),
        da_pct_revenue=tuple([inputs.da_pct] * horizon),
        capex_pct_revenue=tuple([inputs.capex_pct] * horizon),
        nwc_pct_incremental_revenue=inputs.nwc_intensity,
    )


def value_scenario(
    policy: EquityPolicy,
    *,
    model_id: str,
    inputs: CompanyInputs,
    discount: DiscountBuild,
    scenario: str,
    growth: list[float],
    risk_free: float,
    discount_rate_override: float | None = None,
    terminal_growth_override: float | None = None,
) -> ScenarioValue:
    """按政策计算一个情景的每股价值（FCFF 或股息路线）。"""
    if model_id == "equity_dcf_fcff":
        rate = discount.wacc if discount_rate_override is None else discount_rate_override
    elif model_id in ("equity_dividend_discount", "bank_dividend_discount"):
        rate = discount.cost_of_equity if discount_rate_override is None else discount_rate_override
    else:
        raise PolicyModelError(f"政策试运行不支持的模型 {model_id!r}")

    terminal = terminal_spec(
        policy,
        inputs,
        scenario=scenario,
        risk_free=risk_free,
        cost_of_equity=discount.cost_of_equity,
        discount_rate=rate,
        model_id=model_id,
        growth_override=terminal_growth_override,
    )
    if rate <= terminal.growth:
        raise PolicyModelError(
            f"折现率 {rate:.4%} 不大于终值增长 {terminal.growth:.4%}，终值口径不成立"
        )

    if model_id == "equity_dcf_fcff":
        # R8：悲观情景的 EBIT 利润率取近 5 个财年最低值。
        margin = inputs.ebit_margin
        margin_basis = "3y_average"
        if scenario == "pessimistic" and inputs.ebit_margin_min_5y is not None:
            margin = inputs.ebit_margin_min_5y
            margin_basis = "min_last_5_fy"
        drivers = _fcff_drivers(policy, inputs, growth, ebit_margin=margin)
        years = project_fcff(drivers)
        nopat_last = years[-1].nopat
        terminal_cash = nopat_last * (1.0 + terminal.growth) * (1.0 - terminal.reinvestment_rate)
        if terminal_cash <= 0:
            raise PolicyModelError(
                f"末期 NOPAT={nopat_last:,.0f}，按再投资一致性计算的终值现金流非正，不能永续化"
            )
        tv = TerminalValue(
            method="zero_growth_perpetuity" if terminal.growth == 0 else "growing_perpetuity",
            growth=terminal.growth,
            next_period_cash_flow=terminal_cash,
            basis=(
                "终值现金流 = 末期 NOPAT × (1+g) × (1 − g/新投资回报率)；"
                f"新投资回报率 {terminal.new_investment_return:.2%}"
            ),
            terminal_year=years[-1].year,
        )
        bridge = EquityBridge(
            debt=inputs.debt,
            cash=inputs.available_cash,
            non_operating_assets=0.0,
            # 少数股东权益为负（子公司累计亏损超过少数股东投入）时，桥接内核不允许
            # 负值；按 0 处理，该差额作为近似项单独披露，不静默增加股权价值。
            minority_interest=max(0.0, inputs.minority_equity),
            preferred_equity=0.0,
            shares=inputs.shares,
        )
        valuation = value_fcff(years, discount_rate=rate, terminal=tv, bridge=bridge)
        detail = {
            "model": "equity_dcf_fcff",
            "drivers": {
                "revenue_growth": growth,
                "ebit_margin": margin,
                "ebit_margin_basis": margin_basis,
                "tax_rate": inputs.tax_rate,
                "da_pct_revenue": inputs.da_pct,
                "capex_pct_revenue": inputs.capex_pct,
                "nwc_pct_incremental_revenue": inputs.nwc_intensity,
            },
            "years": [y.to_dict() for y in years],
            "enterprise_value": valuation.enterprise_value,
            "equity_value": valuation.equity_value,
            "bridge": {
                "debt": inputs.debt,
                "cash": inputs.available_cash,
                "minority_interest": max(0.0, inputs.minority_equity),
                "minority_reported": inputs.minority_equity,
                "shares": inputs.shares,
            },
            "terminal_cash_flow": terminal_cash,
            "explicit_fcff_sum": sum(y.fcff for y in years),
            "explicit_fcff_negative_years": sum(1 for y in years if y.fcff <= 0),
            "discounting": valuation.stream.to_dict(),
        }
        return ScenarioValue(
            scenario=scenario,
            model=model_id,
            value_per_share=valuation.value_per_share,
            terminal_share=valuation.stream.to_dict()["terminal_share_of_total"],
            discount_rate=rate,
            terminal=terminal,
            detail=detail,
        )

    # 股息路线
    if inputs.net_profit_parent is None or inputs.net_profit_parent <= 0:
        raise PolicyModelError("基期归母净利润非正，股息路线不适用")
    base_eps = inputs.net_profit_parent / inputs.shares
    eps_margin_ratio = 1.0
    eps_margin_basis = "base"
    if (
        scenario == "pessimistic"
        and inputs.net_margin_min_5y is not None
        and inputs.net_margin_base is not None
        and inputs.net_margin_base > 0
    ):
        eps_margin_ratio = min(1.0, inputs.net_margin_min_5y / inputs.net_margin_base)
        eps_margin_basis = "min_last_5_fy"
        base_eps = base_eps * eps_margin_ratio
    horizon = len(growth)
    drivers = DividendDrivers(
        base_year=inputs.base_year,
        base_eps=base_eps,
        eps_growth=tuple(growth),
        payout=tuple([inputs.payout] * horizon),
        cost_of_equity=rate,
        terminal_growth=terminal.growth,
        roe=inputs.roe_latest,
    )
    years = project_dividends(drivers)
    eps_last = years[-1].eps
    terminal_dps = eps_last * (1.0 + terminal.growth) * (1.0 - terminal.reinvestment_rate)
    if terminal_dps <= 0:
        raise PolicyModelError("按再投资一致性计算的终值每股分配非正，不能永续化")
    tv = TerminalValue(
        method="zero_growth_perpetuity" if terminal.growth == 0 else "growing_perpetuity",
        growth=terminal.growth,
        next_period_cash_flow=terminal_dps,
        basis=(
            "终值每股分配 = 末期 EPS × (1+g) × (1 − g/新投资回报率)；"
            f"新投资回报率 {terminal.new_investment_return:.2%}"
        ),
        terminal_year=years[-1].year,
    )
    stream = discount_stream(
        [y.dps for y in years], rate, terminal=tv, last_period_year=years[-1].year
    )
    detail = {
        "model": model_id,
        "drivers": {
            "eps_growth": growth,
            "payout": inputs.payout,
            "base_eps": base_eps,
            "eps_margin_ratio": eps_margin_ratio,
            "eps_margin_basis": eps_margin_basis,
            "roe": inputs.roe_latest,
        },
        "years": [y.to_dict() for y in years],
        "terminal_dps": terminal_dps,
        "discounting": stream.to_dict(),
    }
    return ScenarioValue(
        scenario=scenario,
        model=model_id,
        value_per_share=stream.total_present_value,
        terminal_share=stream.to_dict()["terminal_share_of_total"],
        discount_rate=rate,
        terminal=terminal,
        detail=detail,
    )


def value_all_scenarios(
    policy: EquityPolicy,
    *,
    model_id: str,
    inputs: CompanyInputs,
    discount: DiscountBuild,
    risk_free: float,
) -> dict[str, ScenarioValue]:
    paths = growth_paths(policy, inputs)
    return {
        name: value_scenario(
            policy,
            model_id=model_id,
            inputs=inputs,
            discount=discount,
            scenario=name,
            growth=paths[name],
            risk_free=risk_free,
        )
        for name in SCENARIO_NAMES
    }


# --- 报告辅助 ---------------------------------------------------------------
def risk_free_drift(
    policy: EquityPolicy, macro: dict[str, Any], *, horizons: tuple[int, ...] | None = None
) -> list[dict[str, Any]]:
    """R12：收益率不变时，规范化无风险利率在 6 / 12 / 24 个月后的预期值。

    规则只取 max(最新值, 120 个月均值)。未来没有观测，因此假设月末收益率
    保持当前值：窗口向后滚动 h 个月，最旧的 h 个观测被最新的 h 个同值观测替换。
    """
    node = policy.node("discount_rate.risk_free")
    months = int(node["lookback_months"])
    if horizons is None:
        horizons = tuple(int(x) for x in node["drift_preview_months"]["values"])
    observations = list(macro.get("month_end_observations") or [])
    if len(observations) < months:
        return []
    window = observations[-months:]
    latest_pct = float(macro["latest"]["value_pct"])
    current_mean = statistics.fmean(float(o["value_pct"]) for o in window)
    rows: list[dict[str, Any]] = []
    for horizon in horizons:
        if horizon > months:
            continue
        future_mean = (current_mean * (months - horizon) + latest_pct * horizon) / months
        chosen = max(latest_pct, future_mean)
        rows.append(
            {
                "months": horizon,
                "month_end_mean_pct": future_mean,
                "normalized_pct": chosen,
                "chosen_because": "latest" if latest_pct >= future_mean else "month_end_mean",
            }
        )
    return rows


def candidate_multiple_table(
    policy: EquityPolicy, *, cost_of_equity: float
) -> list[dict[str, Any]]:
    """R12 / F9：全权益公司基准价值 ÷ 基期每股收益的倍数表。

    机械口径（报告同时披露公式）：
    - 显式期 5 年，增速 g 恒定，FCFF 转化率 p（FCFF ÷ NOPAT）；
    - 终值按 R1 取新投资回报率 = 股权成本，终值增长用政策基准 2.0%
      （再投资率 = g_terminal / Ke），因此终值 = 末期 NOPAT × (1+g_terminal) / Ke；
      显式期增速 g 与终值增速分离，避免 g ≥ Ke 时公式失效；
    - 折现率 = Ke；未分配的留存现金不计入价值（保守）。
    """
    node = policy.node("reporting.candidate_buy_point_conditions")
    payouts = [float(x) for x in node["payout_grid"]]
    growths = [float(x) for x in node["growth_grid"]]
    horizon = int(policy.value("forecast.horizon_years"))
    terminal_growth = float(policy.node("scenarios.base")["terminal_growth"])
    terminal_cap = policy.value("terminal.growth_cap")
    terminal_growth = min(terminal_growth, terminal_cap)
    rows: list[dict[str, Any]] = []
    for payout in payouts:
        row: dict[str, Any] = {"payout": payout, "multiples": {}}
        for growth in growths:
            annuity = sum(
                (1.0 + growth) ** year / (1.0 + cost_of_equity) ** year
                for year in range(1, horizon + 1)
            )
            if cost_of_equity <= terminal_growth:
                row["multiples"][growth] = None
                continue
            terminal = (
                (1.0 + growth) ** horizon
                * (1.0 + terminal_growth)
                / cost_of_equity
                / (1.0 + cost_of_equity) ** horizon
            )
            row["multiples"][growth] = payout * annuity + terminal
        rows.append(row)
    return rows


def candidate_multiple_ranges(
    policy: EquityPolicy, *, cost_of_equity: float
) -> dict[str, Any]:
    """A/B 级对应的市盈率区间：倍数 × (1 − 安全边际)。"""
    table = candidate_multiple_table(policy, cost_of_equity=cost_of_equity)
    values = [
        value
        for row in table
        for value in row["multiples"].values()
        if value is not None
    ]
    out: dict[str, Any] = {"min_multiple": min(values), "max_multiple": max(values)}
    for grade in ("A", "B"):
        margin = float(policy.node(f"grade.{grade}")["margin_of_safety"])
        out[grade] = {
            "margin_of_safety": margin,
            "pe_low": min(values) * (1.0 - margin),
            "pe_high": max(values) * (1.0 - margin),
        }
    return out


__all__ = [
    "CompanyInputs",
    "DiscountBuild",
    "PolicyModelError",
    "SCENARIO_NAMES",
    "ScenarioValue",
    "TerminalSpec",
    "build_discount",
    "candidate_multiple_ranges",
    "candidate_multiple_table",
    "extract_inputs",
    "growth_paths",
    "leverage_guardrail",
    "leverage_ratio",
    "load_macro_series",
    "net_interest_bearing_debt",
    "normalized_risk_free",
    "risk_free_drift",
    "steady_growth",
    "terminal_spec",
    "value_all_scenarios",
    "value_scenario",
]
