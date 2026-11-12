"""好公司池指标计算：只做算术与缺数据标注，不判断是否入池。

每个指标返回 `MetricValue`：
- `value` 可计算时给出数值；
- `missing` 记录缺什么（年报缺失、字段缺失、净资产为负等）；
- 评估层据此区分「不通过」与「待定」，绝不把缺失当 0 或行业均值。
"""

from __future__ import annotations

from statistics import fmean
from typing import Any, Iterable

from engine.quality.models import Company, FinancialYear, MetricValue
from engine.quality.policy import QualityPolicy

MISSING_ANNUAL_REPORT = "missing_annual_report"
MISSING_FIELD = "missing_field"
NEGATIVE_EQUITY = "negative_average_equity"
NEGATIVE_PROFIT_SUM = "negative_profit_sum"
EBITDA_MISSING = "ebitda_missing"
EBITDA_NONPOSITIVE = "ebitda_nonpositive"
NET_DEBT_MISSING = "net_debt_missing"
AUDIT_MISSING = "missing_audit_opinion"
PENALTY_MISSING = "missing_penalty_data"
PENALTY_SUBJECT_UNKNOWN = "penalty_subject_unknown"
PLEDGE_MISSING = "missing_pledge_data"

# 这些原因属于公司事实/口径不适用，条件应记为「不通过」，而不是「待定」。
FAIL_REASONS = frozenset(
    {MISSING_ANNUAL_REPORT, NEGATIVE_EQUITY, NEGATIVE_PROFIT_SUM, EBITDA_NONPOSITIVE}
)


def _year(company: Company, year: int) -> FinancialYear | None:
    return company.years.get(year)


def _add(missing: list[str], reason: str) -> None:
    if reason not in missing:
        missing.append(reason)


def _average_equity(company: Company, year: int) -> tuple[float | None, str | None]:
    current = _year(company, year)
    previous = _year(company, year - 1)
    if current is None:
        return None, MISSING_ANNUAL_REPORT
    if previous is None:
        # 上一年资产负债表只用于期初数；缺它无法算平均归母净资产。
        return None, MISSING_FIELD
    if current.parent_equity is None or previous.parent_equity is None:
        return None, MISSING_FIELD
    average = (current.parent_equity + previous.parent_equity) / 2
    if average <= 0:
        return None, NEGATIVE_EQUITY
    return average, None


def compute_metrics(company: Company, policy: QualityPolicy) -> dict[str, Any]:
    years = list(company.window_years)
    roe_rows: list[dict[str, Any]] = []
    roe_missing: list[str] = []
    for year in years:
        current = _year(company, year)
        if current is None:
            _add(roe_missing, MISSING_ANNUAL_REPORT)
            continue
        if current.parent_netprofit is None:
            _add(roe_missing, MISSING_FIELD)
            continue
        average, reason = _average_equity(company, year)
        if reason:
            _add(roe_missing, reason)
            continue
        assert average is not None
        roe_rows.append(
            {
                "fiscal_year": year,
                "parent_netprofit": current.parent_netprofit,
                "average_parent_equity": average,
                "roe": current.parent_netprofit / average,
            }
        )

    roe_values = [row["roe"] for row in roe_rows]
    roe_average = MetricValue(
        value=fmean(roe_values) if len(roe_rows) == len(years) and roe_values else None,
        missing=tuple(roe_missing) if len(roe_rows) != len(years) else (),
        note=f"{len(roe_rows)}/{len(years)} 个财年可计算",
    )

    threshold = float(policy.value("roe_year_threshold"))
    roe_above_share: MetricValue
    if len(roe_rows) != len(years) or not roe_values:
        roe_above_share = MetricValue(
            None, tuple(roe_missing), f"{len(roe_rows)}/{len(years)} 个财年可计算"
        )
    else:
        roe_above_share = MetricValue(
            sum(1 for value in roe_values if value >= threshold) / len(roe_values),
            note=f"分母为窗口 {len(roe_values)} 年",
        )

    recent_n = int(policy.value("roe_recent_years"))
    recent_years = years[-recent_n:]
    recent_rows = [row for row in roe_rows if row["fiscal_year"] in recent_years]
    recent_missing = list(roe_missing)
    roe_recent = MetricValue(
        value=(
            fmean(row["roe"] for row in recent_rows)
            if len(recent_rows) == len(recent_years)
            else None
        ),
        missing=tuple(recent_missing) if len(recent_rows) != len(recent_years) else (),
        note=f"最近 {recent_n} 个财年",
    )

    cash_n = int(policy.value("cash_conversion_years"))
    cash_years = years[-cash_n:]
    cash_np = 0.0
    cash_ocf = 0.0
    cash_missing: list[str] = []
    cash_available = True
    for year in cash_years:
        item = _year(company, year)
        if item is None:
            _add(cash_missing, MISSING_ANNUAL_REPORT)
            cash_available = False
            continue
        if item.parent_netprofit is None or item.operating_cashflow is None:
            _add(cash_missing, MISSING_FIELD)
            cash_available = False
            continue
        cash_np += item.parent_netprofit
        cash_ocf += item.operating_cashflow
    if not cash_available:
        cash_conversion = MetricValue(None, tuple(cash_missing), "最近 5 年")
    elif cash_np <= 0:
        cash_conversion = MetricValue(None, (NEGATIVE_PROFIT_SUM,), "归母净利润合计 ≤ 0，比值无意义")
    else:
        cash_conversion = MetricValue(cash_ocf / cash_np, note="经营现金流合计 ÷ 归母净利润合计")

    fcf_n = int(policy.value("fcf_years"))
    fcf_years = years[-fcf_n:]
    fcf_total = 0.0
    fcf_missing: list[str] = []
    fcf_available = True
    for year in fcf_years:
        item = _year(company, year)
        if item is None:
            _add(fcf_missing, MISSING_ANNUAL_REPORT)
            fcf_available = False
            continue
        if item.operating_cashflow is None or item.capex is None:
            _add(fcf_missing, MISSING_FIELD)
            fcf_available = False
            continue
        fcf_total += item.operating_cashflow - item.capex
    fcf = MetricValue(
        fcf_total if fcf_available else None,
        tuple(fcf_missing) if not fcf_available else (),
        "经营现金流 − 资本开支，最近 5 年合计",
    )

    loss_years: MetricValue
    loss_count = 0
    loss_missing: list[str] = []
    loss_available = True
    for year in years:
        item = _year(company, year)
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
        loss_count if loss_available else None, tuple(loss_missing) if not loss_available else ()
    )

    latest = years[-1]
    latest_item = _year(company, latest)
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

    dividend_n = int(policy.value("dividend_years"))
    dividend_years = years[-dividend_n:]
    dividend_total = 0.0
    dividend_np = 0.0
    dividend_missing: list[str] = []
    dividend_available = True
    for year in dividend_years:
        item = _year(company, year)
        if item is None or item.parent_netprofit is None:
            _add(dividend_missing, MISSING_ANNUAL_REPORT if item is None else MISSING_FIELD)
            dividend_available = False
            continue
        dividend_np += item.parent_netprofit
        dividend_total += company.dividends.get(year) or 0.0
    if not dividend_available:
        dividend_payout = MetricValue(None, tuple(dividend_missing), "最近 5 年")
    elif dividend_np <= 0:
        dividend_payout = MetricValue(None, (NEGATIVE_PROFIT_SUM,), "归母净利润合计 ≤ 0，分红率无意义")
    else:
        dividend_payout = MetricValue(dividend_total / dividend_np, note="现金分红合计 ÷ 归母净利润合计")

    audit_n = int(policy.value("audit_lookback_years"))
    audit_years = years[-audit_n:]
    audit_missing = [
        year
        for year in audit_years
        if company.audit_opinions.get(year) in (None, "")
    ]
    if not company.audit_available or audit_missing:
        audit_metric = MetricValue(
            None,
            (AUDIT_MISSING,),
            f"缺审计意见年份：{audit_missing}" if audit_missing else "审计意见数据不可得",
        )
    else:
        nonstandard = [
            year
            for year in audit_years
            if str(company.audit_opinions.get(year)) != "标准无保留意见"
        ]
        audit_metric = MetricValue(
            len(nonstandard),
            note="非标准年份：" + ("、".join(str(y) for y in nonstandard) if nonstandard else "无"),
        )

    counted = [item for item in company.penalties if item.counted]
    if not company.penalty_available:
        penalty_metric = MetricValue(None, (PENALTY_MISSING,), "处罚/立案公告检索未完成")
    elif counted:
        penalty_metric = MetricValue(
            len(counted),
            note="；".join(item.title for item in counted[:3]),
        )
    elif company.penalty_subject_unknown:
        penalty_metric = MetricValue(
            None,
            (PENALTY_SUBJECT_UNKNOWN,),
            "存在公告但无法判断主体身份，按待定处理",
        )
    else:
        penalty_metric = MetricValue(0, note="无计入的立案/处罚/公开谴责（其他主体只记录）")

    if not company.pledge_available:
        pledge_metric = MetricValue(None, (PLEDGE_MISSING,), company.pledge_note or "控股股东质押数据不可得")
    elif company.controlling_pledge_ratio is None:
        pledge_metric = MetricValue(None, (PLEDGE_MISSING,), company.pledge_note or "控股股东质押比例缺失")
    else:
        pledge_metric = MetricValue(
            company.controlling_pledge_ratio,
            note=company.pledge_report_date or "最新定期报告",
        )

    return {
        "roe_years": roe_rows,
        "roe_average": roe_average,
        "roe_above_share": roe_above_share,
        "roe_recent_average": roe_recent,
        "cash_conversion": cash_conversion,
        "fcf_total": fcf,
        "loss_years": loss_years,
        "net_debt_to_ebitda": net_debt_ratio,
        "dividend_payout": dividend_payout,
        "audit_nonstandard_years": audit_metric,
        "penalty_count": penalty_metric,
        "pledge_ratio": pledge_metric,
        "dividend_total": dividend_total if dividend_available else None,
        "dividend_profit_total": dividend_np if dividend_available else None,
        "cash_flow_total": cash_ocf if cash_available else None,
        "cash_profit_total": cash_np if cash_available else None,
    }


def metric_value(metrics: dict[str, Any], name: str) -> MetricValue:
    value = metrics.get(name)
    if not isinstance(value, MetricValue):
        raise KeyError(f"指标不存在：{name}")
    return value


def has_fail_reason(metric: MetricValue) -> bool:
    return any(reason in FAIL_REASONS for reason in metric.missing)
