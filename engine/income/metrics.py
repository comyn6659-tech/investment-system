"""稳定收息型指标：只做算术与缺数据标注，不判断是否入池。

与好公司池指标独立：收息型看利润稳定、逐年分红与分红现金流覆盖；
所有阈值从 `income-pool-v1-draft` 政策读取，代码不另设业务数字。
"""

from __future__ import annotations

from statistics import fmean
from typing import Any

from engine.quality.metrics import (
    AUDIT_MISSING,
    EBITDA_MISSING,
    EBITDA_NONPOSITIVE,
    MISSING_ANNUAL_REPORT,
    MISSING_FIELD,
    NEGATIVE_EQUITY,
    NEGATIVE_PROFIT_SUM,
    NET_DEBT_MISSING,
    PENALTY_MISSING,
    PENALTY_SUBJECT_UNKNOWN,
    PLEDGE_MISSING,
)
from engine.quality.models import Company, MetricValue
from engine.quality.policy import QualityPolicy

MISSING_DIVIDEND_DATA = "missing_dividend_data"
ZERO_DIVIDEND_TOTAL = "zero_dividend_total"

# 缺年报/会计口径不适用/零分红属于事实，条件记不通过；取不到的数据记待定。
FAIL_REASONS = frozenset(
    {
        MISSING_ANNUAL_REPORT,
        NEGATIVE_EQUITY,
        NEGATIVE_PROFIT_SUM,
        EBITDA_NONPOSITIVE,
        ZERO_DIVIDEND_TOTAL,
    }
)


def _add(missing: list[str], reason: str) -> None:
    if reason not in missing:
        missing.append(reason)


def _average_equity(company: Company, year: int) -> tuple[float | None, str | None]:
    current = company.years.get(year)
    previous = company.years.get(year - 1)
    if current is None:
        return None, MISSING_ANNUAL_REPORT
    if previous is None:
        return None, MISSING_FIELD
    if current.parent_equity is None or previous.parent_equity is None:
        return None, MISSING_FIELD
    average = (current.parent_equity + previous.parent_equity) / 2
    if average <= 0:
        return None, NEGATIVE_EQUITY
    return average, None


def _roe_rows(company: Company) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for year in company.window_years:
        current = company.years.get(year)
        if current is None:
            _add(missing, MISSING_ANNUAL_REPORT)
            continue
        if current.parent_netprofit is None:
            _add(missing, MISSING_FIELD)
            continue
        average, reason = _average_equity(company, year)
        if reason:
            _add(missing, reason)
            continue
        rows.append(
            {
                "fiscal_year": year,
                "parent_netprofit": current.parent_netprofit,
                "average_parent_equity": average,
                "roe": current.parent_netprofit / average,
            }
        )
    return rows, missing


def _dividend_value(company: Company, year: int) -> float:
    return float(company.dividends.get(year) or 0.0)


def compute_metrics(company: Company, policy: QualityPolicy) -> dict[str, Any]:
    years = list(company.window_years)
    rows, roe_missing = _roe_rows(company)
    complete_roe = len(rows) == len(years) and bool(rows)

    roe_average = MetricValue(
        value=fmean(row["roe"] for row in rows) if complete_roe else None,
        missing=tuple(roe_missing) if not complete_roe else (),
        note=f"{len(rows)}/{len(years)} 个财年可计算",
    )
    roe_min = MetricValue(
        value=min(row["roe"] for row in rows) if complete_roe else None,
        missing=tuple(roe_missing) if not complete_roe else (),
        note=f"{len(rows)}/{len(years)} 个财年可计算",
    )

    loss_count = 0
    loss_missing: list[str] = []
    loss_available = True
    for year in years:
        item = company.years.get(year)
        if item is None:
            _add(loss_missing, MISSING_ANNUAL_REPORT)
            loss_available = False
            continue
        if item.parent_netprofit is None:
            _add(loss_missing, MISSING_FIELD)
            loss_available = False
            continue
        if item.parent_netprofit < 0:
            loss_count += 1
    loss_years = MetricValue(
        loss_count if loss_available else None,
        tuple(loss_missing) if not loss_available else (),
    )

    threshold = float(policy.value("profit_decline_threshold"))
    decline_count = 0
    decline_missing: list[str] = []
    decline_available = True
    for index in range(1, len(years)):
        current = company.years.get(years[index])
        previous = company.years.get(years[index - 1])
        if current is None or previous is None:
            _add(decline_missing, MISSING_ANNUAL_REPORT)
            decline_available = False
            continue
        if current.parent_netprofit is None or previous.parent_netprofit is None:
            _add(decline_missing, MISSING_FIELD)
            decline_available = False
            continue
        prior = previous.parent_netprofit
        current_value = current.parent_netprofit
        if prior <= 0:
            _add(decline_missing, MISSING_FIELD)
            decline_available = False
            continue
        if (current_value - prior) / abs(prior) < -threshold:
            decline_count += 1
    profit_decline_years = MetricValue(
        decline_count if decline_available else None,
        tuple(decline_missing) if not decline_available else (),
        note="只比较窗口内有上一年数据的财年",
    )

    available = set(company.dividend_available_years)
    dividend_missing_count = 0
    dividend_missing_reasons: list[str] = []
    dividend_missing_available = True
    for year in years:
        if available and year not in available:
            _add(dividend_missing_reasons, MISSING_DIVIDEND_DATA)
            dividend_missing_available = False
            continue
        if _dividend_value(company, year) <= 0:
            dividend_missing_count += 1
    dividend_missing_years = MetricValue(
        dividend_missing_count if dividend_missing_available else None,
        tuple(dividend_missing_reasons) if not dividend_missing_available else (),
    )

    dividend_n = int(policy.value("dividend_years"))
    dividend_window = years[-dividend_n:]
    dividend_total = 0.0
    dividend_profit = 0.0
    dividend_missing: list[str] = []
    dividend_ok = True
    for year in dividend_window:
        if available and year not in available:
            _add(dividend_missing, MISSING_DIVIDEND_DATA)
            dividend_ok = False
            continue
        item = company.years.get(year)
        if item is None or item.parent_netprofit is None:
            _add(dividend_missing, MISSING_ANNUAL_REPORT if item is None else MISSING_FIELD)
            dividend_ok = False
            continue
        dividend_total += _dividend_value(company, year)
        dividend_profit += item.parent_netprofit
    if not dividend_ok:
        dividend_payout = MetricValue(None, tuple(dividend_missing), "最近 5 年")
    elif dividend_profit <= 0:
        dividend_payout = MetricValue(None, (NEGATIVE_PROFIT_SUM,), "归母净利润合计 ≤ 0，分红率无意义")
    else:
        dividend_payout = MetricValue(
            dividend_total / dividend_profit, note="现金分红合计 ÷ 归母净利润合计"
        )

    fcf_n = int(policy.value("fcf_years"))
    fcf_window = years[-fcf_n:]
    fcf_total = 0.0
    fcf_missing: list[str] = []
    fcf_ok = True
    for year in fcf_window:
        if available and year not in available:
            _add(fcf_missing, MISSING_DIVIDEND_DATA)
            fcf_ok = False
            continue
        item = company.years.get(year)
        if item is None:
            _add(fcf_missing, MISSING_ANNUAL_REPORT)
            fcf_ok = False
            continue
        if item.operating_cashflow is None or item.capex is None:
            _add(fcf_missing, MISSING_FIELD)
            fcf_ok = False
            continue
        fcf_total += item.operating_cashflow - item.capex
    if not fcf_ok:
        fcf_to_dividend = MetricValue(None, tuple(fcf_missing), "最近 5 年")
    elif dividend_total <= 0:
        fcf_to_dividend = MetricValue(
            None, (ZERO_DIVIDEND_TOTAL,), "最近 5 年现金分红合计 ≤ 0，比值无意义"
        )
    else:
        fcf_to_dividend = MetricValue(
            fcf_total / dividend_total, note="自由现金流合计 ÷ 现金分红合计"
        )

    latest = years[-1]
    latest_item = company.years.get(latest)
    if latest_item is None:
        net_debt_ratio = MetricValue(None, (MISSING_ANNUAL_REPORT,), f"{latest} 年报缺失")
    elif latest_item.net_debt is None:
        net_debt_ratio = MetricValue(None, (NET_DEBT_MISSING,), "有息负债或货币资金缺失")
    elif latest_item.net_debt <= 0:
        net_debt_ratio = MetricValue(0.0, note="净有息负债 ≤ 0，按 0 处理（不依赖 EBITDA）")
    else:
        ebitda = latest_item.ebitda
        if ebitda is None:
            net_debt_ratio = MetricValue(None, (EBITDA_MISSING,), "利润总额或利息费用缺失")
        elif ebitda <= 0:
            net_debt_ratio = MetricValue(None, (EBITDA_NONPOSITIVE,), "EBITDA ≤ 0")
        else:
            net_debt_ratio = MetricValue(latest_item.net_debt / ebitda, note=f"{latest} 财年")

    audit_n = int(policy.value("audit_lookback_years"))
    audit_window = years[-audit_n:]
    audit_missing_years = [
        year for year in audit_window if company.audit_opinions.get(year) in (None, "")
    ]
    if not company.audit_available or audit_missing_years:
        audit_metric = MetricValue(
            None,
            (AUDIT_MISSING,),
            f"缺审计意见年份：{audit_missing_years}" if audit_missing_years else "审计意见数据不可得",
        )
    else:
        nonstandard = [
            year
            for year in audit_window
            if str(company.audit_opinions.get(year)) != "标准无保留意见"
        ]
        audit_metric = MetricValue(
            len(nonstandard),
            note="非标准年份：" + ("、".join(str(year) for year in nonstandard) if nonstandard else "无"),
        )

    counted = [item for item in company.penalties if item.counted]
    if not company.penalty_available:
        penalty_metric = MetricValue(None, (PENALTY_MISSING,), "处罚/立案公告检索未完成")
    elif counted:
        penalty_metric = MetricValue(len(counted), note="；".join(item.title for item in counted[:3]))
    elif company.penalty_subject_unknown:
        penalty_metric = MetricValue(
            None, (PENALTY_SUBJECT_UNKNOWN,), "存在公告但无法判断主体身份，按待定处理"
        )
    else:
        penalty_metric = MetricValue(0, note="无计入的立案/处罚/公开谴责（其他主体只记录）")

    if not company.pledge_available:
        pledge_metric = MetricValue(None, (PLEDGE_MISSING,), company.pledge_note or "质押数据不可得")
    elif company.controlling_pledge_ratio is None:
        pledge_metric = MetricValue(None, (PLEDGE_MISSING,), company.pledge_note or "质押比例缺失")
    else:
        pledge_metric = MetricValue(
            company.controlling_pledge_ratio, note=company.pledge_report_date or "最新定期报告"
        )

    return {
        "roe_rows": rows,
        "roe_average": roe_average,
        "roe_min": roe_min,
        "loss_years": loss_years,
        "profit_decline_years": profit_decline_years,
        "dividend_missing_years": dividend_missing_years,
        "dividend_payout": dividend_payout,
        "fcf_to_dividend": fcf_to_dividend,
        "net_debt_to_ebitda": net_debt_ratio,
        "audit_nonstandard_years": audit_metric,
        "penalty_count": penalty_metric,
        "pledge_ratio": pledge_metric,
        "dividend_total": dividend_total if dividend_ok else None,
        "dividend_profit_total": dividend_profit if dividend_ok else None,
        "fcf_total": fcf_total if fcf_ok else None,
    }


def metric_value(metrics: dict[str, Any], name: str) -> MetricValue:
    value = metrics.get(name)
    if not isinstance(value, MetricValue):
        raise KeyError(f"指标不存在：{name}")
    return value


def has_fail_reason(metric: MetricValue) -> bool:
    return any(reason in FAIL_REASONS for reason in metric.missing)
