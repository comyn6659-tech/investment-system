"""好公司池规则评估：范围判定、逐条条件、入池/未入池/待定状态与报告素材。

评估只读固定快照与版本化政策，不访问网络、不写账本。
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from engine.quality.metrics import (
    FAIL_REASONS,
    MISSING_ANNUAL_REPORT,
    MISSING_FIELD,
    compute_metrics,
    has_fail_reason,
    metric_value,
)
from engine.quality.models import (
    CONDITION_FAIL,
    CONDITION_PASS,
    CONDITION_PENDING,
    CONDITION_UNTESTED,
    OUT_OF_SCOPE_FINANCIAL,
    OUT_OF_SCOPE_MARKET,
    OUT_OF_SCOPE_SHORT_HISTORY,
    OUT_OF_SCOPE_ST,
    STATUS_IN_POOL,
    STATUS_NOT_IN_POOL,
    STATUS_OUT_OF_SCOPE,
    STATUS_PENDING,
    Company,
    CompanyResult,
    ConditionResult,
    MetricValue,
    PoolRun,
)
from engine.quality.policy import QualityPolicy
from engine.quality.test_set import TEST_CODES

TZ = ZoneInfo("Asia/Singapore")


def _format(value: float | int | None, unit: str | None) -> str:
    if value is None:
        return "—"
    if unit == "ratio":
        return f"{value:.2%}"
    if unit == "currency":
        return f"{value:,.0f} 元"
    if unit == "multiple":
        return f"{value:.2f} 倍"
    if unit == "years":
        return f"{value:.0f} 年"
    return f"{value:.4f}"


def _relative(distance: float | None, threshold: float | int | None) -> float | None:
    if distance is None or threshold in (None, 0):
        return None
    return distance / abs(float(threshold))


def _metric_summary(metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "roe_average": _metric_dict(metrics.get("roe_average")),
        "roe_above_share": _metric_dict(metrics.get("roe_above_share")),
        "roe_recent_average": _metric_dict(metrics.get("roe_recent_average")),
        "cash_conversion": _metric_dict(metrics.get("cash_conversion")),
        "fcf_total": _metric_dict(metrics.get("fcf_total")),
        "loss_years": _metric_dict(metrics.get("loss_years")),
        "net_debt_to_ebitda": _metric_dict(metrics.get("net_debt_to_ebitda")),
        "dividend_payout": _metric_dict(metrics.get("dividend_payout")),
        "dividend_total": metrics.get("dividend_total"),
        "dividend_profit_total": metrics.get("dividend_profit_total"),
        "roe_years": metrics.get("roe_years") or [],
    }


def _metric_dict(metric: Any) -> dict[str, Any] | None:
    if not isinstance(metric, MetricValue):
        return None
    return {
        "value": metric.value,
        "missing": list(metric.missing),
        "note": metric.note,
    }


def _missing_text(missing: Iterable[str]) -> str:
    labels = {
        MISSING_ANNUAL_REPORT: "缺年报",
        MISSING_FIELD: "缺字段",
        "negative_average_equity": "平均归母净资产 ≤ 0",
        "negative_profit_sum": "归母净利润合计 ≤ 0",
        "ebitda_missing": "EBITDA 缺利息费用",
        "ebitda_nonpositive": "EBITDA ≤ 0",
        "net_debt_missing": "有息负债或货币资金缺失",
        "untested": "本版未检验",
    }
    return "、".join(dict.fromkeys(labels.get(item, item) for item in missing))


def _scope_reason(company: Company, policy: QualityPolicy) -> tuple[str | None, str | None]:
    markets = set((policy.raw.get("scope") or {}).get("markets") or [])
    if company.exchange not in markets:
        return OUT_OF_SCOPE_MARKET, f"交易所 {company.exchange} 不在范围内"
    if company.is_st:
        return OUT_OF_SCOPE_ST, "ST/*ST"
    if company.financial_excluded:
        return (
            OUT_OF_SCOPE_FINANCIAL,
            f"金融业（{company.financial_exclusion_reason or company.industry or '未注明'}）；指标不适用",
        )
    minimum = int(policy.value("min_fiscal_years"))
    if len(company.window_years) < minimum:
        return (
            OUT_OF_SCOPE_SHORT_HISTORY,
            f"完整年报 {len(company.window_years)} 个财年，少于 {minimum} 年",
        )
    return None, None


def _compare(value: float, operator: str, threshold: float) -> bool:
    if operator == ">=":
        return value >= threshold
    if operator == ">":
        return value > threshold
    if operator == "<":
        return value < threshold
    if operator == "<=":
        return value <= threshold
    if operator == "==":
        return value == threshold
    raise ValueError(f"不支持的比较符：{operator}")


def _evaluate_condition(
    group: Mapping[str, Any],
    condition: Mapping[str, Any],
    metrics: Mapping[str, Any],
    policy: QualityPolicy,
) -> ConditionResult:
    cid = str(condition["id"])
    label = str(condition["label"])
    unit = condition.get("unit")
    if condition.get("evaluation") == "untested":
        return ConditionResult(
            condition_id=cid,
            group_id=str(group["id"]),
            label=label,
            status=CONDITION_UNTESTED,
            unit=unit,
            reason=str(condition.get("untested_reason") or "本版未检验"),
        )

    metric = metric_value(metrics, str(condition["metric"]))
    threshold = float(policy.value(str(condition["parameter"])))
    if metric.value is None:
        status = CONDITION_FAIL if has_fail_reason(metric) else CONDITION_PENDING
        return ConditionResult(
            condition_id=cid,
            group_id=str(group["id"]),
            label=label,
            status=status,
            threshold=threshold,
            unit=unit,
            missing=metric.missing,
            reason=_missing_text(metric.missing) or metric.note or "缺数据",
        )

    value = float(metric.value)
    passed = _compare(value, str(condition["operator"]), threshold)
    if condition["distance"] == "value_minus_threshold":
        distance = value - threshold
    else:
        distance = threshold - value
    operator_text = {"<": "<", "<=": "≤", ">": ">", ">=": "≥", "==": "="}[str(condition["operator"])]
    reason = (
        f"{_format(value, unit)} {operator_text} {_format(threshold, unit)}"
        + ("，通过" if passed else "，未通过")
    )
    return ConditionResult(
        condition_id=cid,
        group_id=str(group["id"]),
        label=label,
        status=CONDITION_PASS if passed else CONDITION_FAIL,
        value=value,
        threshold=threshold,
        distance_abs=distance,
        distance_rel=_relative(distance, threshold),
        unit=unit,
        reason=reason,
    )


def evaluate_company(company: Company, policy: QualityPolicy) -> CompanyResult:
    scope, scope_detail = _scope_reason(company, policy)
    expected = tuple(company.window_years)
    available = tuple(year for year in expected if year in company.years)
    missing_years = tuple(year for year in expected if year not in company.years)
    if scope is not None:
        return CompanyResult(
            code=company.code,
            name=company.name,
            exchange=company.exchange,
            board=company.board,
            industry=company.industry,
            listing_date=company.listing_date,
            status=STATUS_OUT_OF_SCOPE,
            reason=scope_detail,
            window_years=expected,
            available_years=available,
            missing_years=missing_years,
            financial_excluded=company.financial_excluded,
            metrics={"out_of_scope_reason": scope},
        )

    metrics = compute_metrics(company, policy)
    conditions = [
        _evaluate_condition(group, condition, metrics, policy)
        for group, condition in policy.iter_conditions()
    ]
    fails = [item for item in conditions if item.status == CONDITION_FAIL]
    pendings = [item for item in conditions if item.status == CONDITION_PENDING]
    if fails:
        status = STATUS_NOT_IN_POOL
        reason = "；".join(f"{item.label}：{item.reason}" for item in fails[:3])
    elif pendings:
        status = STATUS_PENDING
        reason = "；".join(f"{item.label}：{item.reason}" for item in pendings[:3])
    else:
        status = STATUS_IN_POOL
        reason = None
    return CompanyResult(
        code=company.code,
        name=company.name,
        exchange=company.exchange,
        board=company.board,
        industry=company.industry,
        listing_date=company.listing_date,
        status=status,
        reason=reason,
        window_years=expected,
        available_years=available,
        missing_years=missing_years,
        conditions=conditions,
        metrics=_metric_summary(metrics),
    )


def _funnel(results: list[CompanyResult], policy: QualityPolicy) -> list[dict[str, Any]]:
    evaluated = [item for item in results if item.status != STATUS_OUT_OF_SCOPE]
    active = {item.code for item in evaluated}
    funnel: list[dict[str, Any]] = []
    for group, condition in policy.iter_conditions():
        cid = str(condition["id"])
        independent = sum(
            1
            for item in evaluated
            if any(cond.condition_id == cid and cond.status == CONDITION_FAIL for cond in item.conditions)
        )
        eliminated = 0
        pending = 0
        for item in evaluated:
            if item.code not in active:
                continue
            cond = next((c for c in item.conditions if c.condition_id == cid), None)
            if cond is None:
                continue
            if cond.status == CONDITION_FAIL:
                eliminated += 1
                active.discard(item.code)
            elif cond.status == CONDITION_PENDING:
                pending += 1
        funnel.append(
            {
                "condition_id": cid,
                "group_id": str(group["id"]),
                "label": str(condition["label"]),
                "untested": condition.get("evaluation") == "untested",
                "independent_fail_count": independent,
                "sequential_eliminated": eliminated,
                "sequential_pending": pending,
                "remaining_after": len(active),
            }
        )
    return funnel


def _near_misses(results: list[CompanyResult]) -> list[dict[str, Any]]:
    rows = []
    for item in results:
        if item.status != STATUS_NOT_IN_POOL:
            continue
        if len(item.failing) != 1 or item.pending:
            continue
        failure = item.failing[0]
        rows.append(
            {
                "code": item.code,
                "name": item.name,
                "industry": item.industry,
                "condition_id": failure.condition_id,
                "label": failure.label,
                "value": failure.value,
                "threshold": failure.threshold,
                "distance_abs": failure.distance_abs,
                "distance_rel": failure.distance_rel,
                "unit": failure.unit,
                "reason": failure.reason,
                "untested_count": len(item.untested),
            }
        )
    rows.sort(key=lambda row: abs(row["distance_rel"] or 0))
    return rows


def _industry_distribution(results: list[CompanyResult]) -> list[dict[str, Any]]:
    pool = Counter(item.industry or "未分类" for item in results if item.status == STATUS_IN_POOL)
    evaluated = Counter(
        item.industry or "未分类"
        for item in results
        if item.status != STATUS_OUT_OF_SCOPE
    )
    rows = [
        {"industry": industry, "in_pool": count, "evaluated": evaluated[industry]}
        for industry, count in pool.most_common()
    ]
    rows.sort(key=lambda row: (-row["in_pool"], row["industry"]))
    return rows


def _test_set(results: list[CompanyResult]) -> list[dict[str, Any]]:
    by_code = {item.code: item for item in results}
    rows = []
    for code in TEST_CODES:
        item = by_code.get(code)
        if item is None:
            rows.append({"code": code, "status": "missing_from_snapshot"})
            continue
        rows.append(
            {
                "code": item.code,
                "name": item.name,
                "board": item.board,
                "industry": item.industry,
                "status": item.status,
                "reason": item.reason,
                "conditions": [cond.to_dict() for cond in item.conditions],
                "metrics": item.metrics,
            }
        )
    return rows


def _sensitivity(
    companies: list[Company], policy: QualityPolicy
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for parameter, node in (policy.raw.get("sensitivity") or {}).items():
        conditions = [
            condition
            for _, condition in policy.iter_conditions()
            if condition.get("parameter") == parameter
        ]
        applicable = any(condition.get("evaluation") != "untested" for condition in conditions)
        rows = []
        for value in node.get("values") or []:
            run = evaluate_pool(
                companies,
                policy.with_overrides({parameter: value}),
                as_of="sensitivity",
                snapshot_id="sensitivity",
                include_sensitivity=False,
            )
            rows.append(
                {
                    "value": value,
                    "in_pool": run.counts[STATUS_IN_POOL],
                    "not_in_pool": run.counts[STATUS_NOT_IN_POOL],
                    "pending": run.counts[STATUS_PENDING],
                }
            )
        output[parameter] = {
            "applicable": applicable,
            "values": rows,
            "note": None
            if applicable
            else "该参数只对应本版未检验的条件，改变取值不改变入池数量。",
        }
    return output


def evaluate_pool(
    companies: Iterable[Company],
    policy: QualityPolicy,
    *,
    as_of: str,
    snapshot_id: str,
    include_sensitivity: bool = True,
) -> PoolRun:
    company_list = list(companies)
    results = [evaluate_company(company, policy) for company in company_list]
    counts = Counter(item.status for item in results)
    out_of_scope = Counter(
        item.metrics.get("out_of_scope_reason") for item in results if item.status == STATUS_OUT_OF_SCOPE
    )
    counts_payload = {
        "total": len(results),
        STATUS_IN_POOL: counts[STATUS_IN_POOL],
        STATUS_NOT_IN_POOL: counts[STATUS_NOT_IN_POOL],
        STATUS_PENDING: counts[STATUS_PENDING],
        STATUS_OUT_OF_SCOPE: counts[STATUS_OUT_OF_SCOPE],
        "out_of_scope_breakdown": dict(out_of_scope),
    }
    untested = [
        {
            "condition_id": condition["id"],
            "group_id": group["id"],
            "label": condition["label"],
            "reason": condition.get("untested_reason"),
        }
        for group, condition in policy.iter_conditions()
        if condition.get("evaluation") == "untested"
    ]
    return PoolRun(
        as_of=as_of,
        policy_id=policy.policy_id,
        policy_digest=policy.digest,
        snapshot_id=snapshot_id,
        generated_at=datetime.now(TZ).isoformat(timespec="seconds"),
        counts=counts_payload,
        results=results,
        untested_conditions=untested,
        funnel=_funnel(results, policy),
        near_misses=_near_misses(results),
        sensitivity=_sensitivity(company_list, policy) if include_sensitivity else {},
        industry_distribution=_industry_distribution(results),
        test_set=_test_set(results),
    )
