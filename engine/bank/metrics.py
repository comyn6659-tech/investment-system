"""银行指标：ROE、不良率、拨备覆盖率、核心一级资本充足率、分红。

数据来自快照中的 `bank_indicators` 表（东方财富 F10 银行主要指标）与既有财务/分红表。
代码只做算术与缺数据标注，阈值全部从 `bank-pool-v1-draft` 读取。
"""

from __future__ import annotations

from statistics import fmean
from typing import Any, Iterable, Mapping

from engine.quality.metrics import (
    AUDIT_MISSING,
    MISSING_ANNUAL_REPORT,
    MISSING_FIELD,
    NEGATIVE_PROFIT_SUM,
    PENALTY_MISSING,
    PENALTY_SUBJECT_UNKNOWN,
    PLEDGE_MISSING,
)
from engine.quality.models import Company, MetricValue
from engine.quality.policy import QualityPolicy

MISSING_BANK_METRIC = "missing_bank_metric"
MISSING_NPL_COMPARISON = "missing_npl_comparison"
MISSING_DIVIDEND_DATA = "missing_dividend_data"

FAIL_REASONS = frozenset({MISSING_ANNUAL_REPORT, NEGATIVE_PROFIT_SUM})


def _add(missing: list[str], reason: str) -> None:
    if reason not in missing:
        missing.append(reason)


def _number(row: Mapping[str, Any] | None, key: str) -> float | None:
    if row is None:
        return None
    value = row.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(row: Mapping[str, Any]) -> str:
    return str(row.get("report_date") or "")[:10]


def _reports(company: Company) -> list[dict]:
    rows = [dict(item) for item in company.bank_reports]
    rows.sort(key=_date)
    return rows


def _annual_rows(company: Company) -> dict[int, dict]:
    output: dict[int, dict] = {}
    for row in _reports(company):
        if str(row.get("report_type") or "") != "年报":
            continue
        day = _date(row)
        if len(day) >= 4 and day[4:10] == "-12-31":
            output[int(day[:4])] = row
    return output


def _latest(company: Company) -> dict | None:
    rows = _reports(company)
    return rows[-1] if rows else None


def _latest_with(company: Company, key: str) -> dict | None:
    rows = [row for row in _reports(company) if _number(row, key) is not None]
    return rows[-1] if rows else None


def _five_years_before(day: str) -> str | None:
    parts = day.split("-")
    if len(parts) != 3:
        return None
    try:
        year, month, day_of_month = (int(part) for part in parts)
    except ValueError:
        return None
    return f"{year - 5:04d}-{month:02d}-{day_of_month:02d}"


def _row_on(company: Company, day: str) -> dict | None:
    for row in _reports(company):
        if _date(row) == day:
            return row
    return None


def _dividend_value(company: Company, year: int) -> float:
    return float(company.dividends.get(year) or 0.0)


def compute_metrics(company: Company, policy: QualityPolicy) -> dict[str, Any]:
    years = list(company.window_years)
    annual = _annual_rows(company)

    roe_rows: list[dict[str, Any]] = []
    roe_missing: list[str] = []
    for year in years:
        if year not in company.years:
            _add(roe_missing, MISSING_ANNUAL_REPORT)
            continue
        row = annual.get(year)
        if row is None:
            _add(roe_missing, MISSING_BANK_METRIC)
            continue
        roe = _number(row, "roe_weighted")
        if roe is None:
            _add(roe_missing, MISSING_BANK_METRIC)
            continue
        roe_rows.append({"fiscal_year": year, "roe": roe})
    complete_roe = len(roe_rows) == len(years) and bool(roe_rows)
    roe_average = MetricValue(
        value=fmean(row["roe"] for row in roe_rows) if complete_roe else None,
        missing=tuple(roe_missing) if not complete_roe else (),
        note=f"{len(roe_rows)}/{len(years)} 个财年可计算（加权 ROE）",
    )
    recent_n = int(policy.value("roe_recent_years"))
    recent_years = years[-recent_n:]
    recent_rows = [row for row in roe_rows if row["fiscal_year"] in recent_years]
    roe_recent = MetricValue(
        value=(
            fmean(row["roe"] for row in recent_rows)
            if len(recent_rows) == len(recent_years)
            else None
        ),
        missing=tuple(roe_missing) if len(recent_rows) != len(recent_years) else (),
        note=f"最近 {recent_n} 个财年",
    )

    loss_count = 0
    loss_missing: list[str] = []
    loss_available = True
    for year in years:
        if year not in company.years:
            _add(loss_missing, MISSING_ANNUAL_REPORT)
            loss_available = False
            continue
        row = annual.get(year)
        if row is None or _number(row, "parent_netprofit") is None:
            _add(loss_missing, MISSING_BANK_METRIC)
            loss_available = False
            continue
        if float(_number(row, "parent_netprofit")) < 0:
            loss_count += 1
    loss_years = MetricValue(
        loss_count if loss_available else None,
        tuple(loss_missing) if not loss_available else (),
    )

    latest_npl_row = _latest_with(company, "npl_ratio")
    latest_provision_row = _latest_with(company, "provision_coverage")
    latest_core_row = _latest_with(company, "core_tier1_ratio")
    latest_nim_row = _latest_with(company, "net_interest_margin")
    if latest_npl_row is None:
        npl_ratio = MetricValue(None, (MISSING_BANK_METRIC,), "无银行专项指标")
        npl_change = MetricValue(None, (MISSING_NPL_COMPARISON,), "无最新不良率")
    else:
        latest_date = _date(latest_npl_row)
        latest_npl = _number(latest_npl_row, "npl_ratio")
        npl_ratio = MetricValue(
            latest_npl,
            (),
            f"{latest_date} {latest_npl_row.get('report_type') or ''}".strip(),
        )
        prior_date = _five_years_before(latest_date)
        prior = _row_on(company, prior_date) if prior_date else None
        prior_npl = _number(prior, "npl_ratio") if prior else None
        if prior_npl is None:
            npl_change = MetricValue(
                None, (MISSING_NPL_COMPARISON,), f"缺 {prior_date} 不良率"
            )
        else:
            npl_change = MetricValue(
                latest_npl - prior_npl, note=f"{latest_date} − {prior_date}"
            )
    if latest_provision_row is None:
        provision = MetricValue(None, (MISSING_BANK_METRIC,), "无拨备覆盖率")
    else:
        provision = MetricValue(
            _number(latest_provision_row, "provision_coverage"),
            (),
            _date(latest_provision_row),
        )
    if latest_core_row is None:
        core_tier1 = MetricValue(None, (MISSING_BANK_METRIC,), "无核心一级资本充足率")
    else:
        core_tier1 = MetricValue(
            _number(latest_core_row, "core_tier1_ratio"),
            (),
            _date(latest_core_row),
        )
    nim = _number(latest_nim_row, "net_interest_margin") if latest_nim_row else None

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
        row = annual.get(year)
        if year not in company.years:
            _add(dividend_missing, MISSING_ANNUAL_REPORT)
            dividend_ok = False
            continue
        if row is None or _number(row, "parent_netprofit") is None:
            _add(dividend_missing, MISSING_BANK_METRIC)
            dividend_ok = False
            continue
        dividend_total += _dividend_value(company, year)
        dividend_profit += float(_number(row, "parent_netprofit"))
    if not dividend_ok:
        dividend_payout = MetricValue(None, tuple(dividend_missing), "最近 5 年")
    elif dividend_profit <= 0:
        dividend_payout = MetricValue(None, (NEGATIVE_PROFIT_SUM,), "归母净利润合计 ≤ 0，分红率无意义")
    else:
        dividend_payout = MetricValue(
            dividend_total / dividend_profit, note="现金分红合计 ÷ 归母净利润合计"
        )

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
        "roe_rows": roe_rows,
        "roe_average": roe_average,
        "roe_recent_average": roe_recent,
        "loss_years": loss_years,
        "npl_ratio": npl_ratio,
        "npl_change_5y": npl_change,
        "provision_coverage": provision,
        "core_tier1_ratio": core_tier1,
        "net_interest_margin": nim,
        "dividend_missing_years": dividend_missing_years,
        "dividend_payout": dividend_payout,
        "audit_nonstandard_years": audit_metric,
        "penalty_count": penalty_metric,
        "pledge_ratio": pledge_metric,
        "dividend_total": dividend_total if dividend_ok else None,
        "dividend_profit_total": dividend_profit if dividend_ok else None,
    }


def metric_value(metrics: dict[str, Any], name: str) -> MetricValue:
    value = metrics.get(name)
    if not isinstance(value, MetricValue):
        raise KeyError(f"指标不存在：{name}")
    return value


def has_fail_reason(metric: MetricValue) -> bool:
    return any(reason in FAIL_REASONS for reason in metric.missing)
