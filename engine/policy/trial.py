"""个股政策 v1 草案的试运行：把 30 只固定测试集跑成结构化结果。

边界（施工单 A.2）：
- 本模块只产出试运行报告的数据，不修改 CLI、不写入正式结果、不改 model_status；
- 结论只写在 `trial_conclusion` 字段，且明确标注「预演」；
- 所有阈值来自政策文件，代码不定义业务数字。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.data.snapshot import Snapshot, SnapshotStore
from engine.evidence.applicability import decide
from engine.evidence.models import BusinessEvidence, EvidenceBook
from engine.financials.annual import load_annual_history, load_dividend_records
from engine.financials.bridge import build_fcff_bridge
from engine.financials.dividends import build_dividend_book
from engine.contracts import reason_codes as rc
from engine.policy import grading, valuation
from engine.policy.equity import EquityPolicy
from engine.policy.valuation import CompanyInputs, PolicyModelError
from engine.valuation.runner import compute_selection_metrics, price_fact

# 政策试运行的主模型 → 交叉检查模型
_ALT_MODEL = {
    "equity_dcf_fcff": "equity_dividend_discount",
    "equity_dividend_discount": "equity_dcf_fcff",
    "bank_dividend_discount": None,
}

_BANK_REQUIRED_FACTS = ("bank.cet1_ratio", "bank.npl_ratio", "bank.provision_coverage")


def _usable_number(snapshot: Snapshot | None, fact_id: str) -> float | None:
    if snapshot is None:
        return None
    fact = snapshot.fact(fact_id)
    if fact is None or not fact.is_usable_number:
        return None
    return float(fact.value)


def _bank_evidence_ok(snapshot: Snapshot) -> bool:
    return all(_usable_number(snapshot, fact_id) is not None for fact_id in _BANK_REQUIRED_FACTS)


def _approximations(
    policy: EquityPolicy,
    *,
    inputs: CompanyInputs,
    model_id: str,
    base_value: float | None,
    bridge: Any | None,
) -> dict[str, Any]:
    """逐项量化近似影响；无法量化的项单独列出（并因此不能给 A/B）。"""
    items: list[dict[str, Any]] = []
    unquantified: list[str] = []
    shares = inputs.shares

    # 1) 受限资金未披露：用「货币资金 − 期末现金等价物」估算
    if model_id != "equity_dcf_fcff":
        items.append(
            {
                "name": "restricted_cash",
                "label": "受限资金未披露",
                "quantified": True,
                "impact_per_share": 0.0,
                "note": "股息路线不使用可抵扣现金，该近似项对每股价值无影响（影响按 0 计）",
            }
        )
    elif inputs.restricted_cash_estimate is None:
        unquantified.append("restricted_cash")
        items.append(
            {
                "name": "restricted_cash",
                "label": "受限资金未披露",
                "quantified": False,
                "impact_per_share": None,
                "note": "缺货币资金或期末现金等价物，无法估算受限资金",
            }
        )
    else:
        impact = inputs.restricted_cash_estimate / shares
        items.append(
            {
                "name": "restricted_cash",
                "label": "受限资金未披露",
                "quantified": True,
                "estimated_amount": inputs.restricted_cash_estimate,
                "impact_per_share": impact,
                "note": (
                    "估算 = 货币资金 − 现金流量表期末现金及现金等价物余额；"
                    "已从可抵扣现金中扣除，影响按该金额/股本计"
                ),
            }
        )

    # 2) 少数股东损益占比过高：按与母公司相同市盈率定价 vs 账面值
    minority_node = policy.node("quality.minority_interest")
    threshold = float(minority_node["approximation_if_profit_share_above"])
    minority_profit = inputs.minority_profit
    profit = inputs.net_profit
    share = (
        minority_profit / profit
        if minority_profit is not None and profit not in (None, 0) and profit > 0
        else None
    )
    if model_id == "equity_dcf_fcff" and share is not None and share > threshold:
        if base_value is None or inputs.net_profit_parent in (None, 0) or inputs.net_profit_parent <= 0:
            unquantified.append("minority_interest")
            items.append(
                {
                    "name": "minority_interest",
                    "label": "少数股东权益（损益占比过高）",
                    "quantified": False,
                    "impact_per_share": None,
                    "note": f"少数股东损益占合并净利润 {share:.1%}，但缺少正归母净利润，无法量化",
                }
            )
        else:
            equity_before_minority = base_value * shares + inputs.minority_equity
            multiple = equity_before_minority / inputs.net_profit_parent
            implied = (minority_profit or 0.0) * multiple
            impact = abs(implied - inputs.minority_equity) / shares
            items.append(
                {
                    "name": "minority_interest",
                    "label": "少数股东权益（损益占比过高）",
                    "quantified": True,
                    "minority_profit_share": share,
                    "book_value": inputs.minority_equity,
                    "implied_value": implied,
                    "impact_per_share": impact,
                    "note": (
                        "影响 = |少数股东损益 × 母公司隐含市盈率 − 账面少数股东权益| / 股本"
                    ),
                }
            )

    # 3) 金融子公司：吸收存款/发放贷款合计超过总资产阈值
    subsidiary_node = policy.node("quality.financial_subsidiary")
    subsidiary_threshold = float(subsidiary_node["approximation_if_above"])
    loans = inputs.loan_advance
    deposits = inputs.accept_deposit_interbank
    assets = inputs.total_assets
    ratio = (
        (loans + deposits) / assets
        if loans is not None and deposits is not None and assets not in (None, 0) and assets > 0
        else None
    )
    if model_id == "equity_dcf_fcff" and ratio is not None and ratio > subsidiary_threshold:
        gap = bridge.reconciliation_gap if bridge is not None else None
        fcff = bridge.fcff if bridge is not None else None
        if gap is None or fcff in (None, 0) or fcff <= 0 or base_value is None:
            unquantified.append("financial_subsidiary")
            items.append(
                {
                    "name": "financial_subsidiary",
                    "label": "金融子公司（现金流口径近似）",
                    "quantified": False,
                    "impact_per_share": None,
                    "note": (
                        f"发放贷款及垫款 + 吸收存款及同业存放占总资产 {ratio:.1%}，"
                        "但缺少可用的勾稽差额或正 FCFF，无法量化"
                    ),
                }
            )
        else:
            relative = abs(gap) / abs(fcff)
            impact = relative * base_value
            items.append(
                {
                    "name": "financial_subsidiary",
                    "label": "金融子公司（现金流口径近似）",
                    "quantified": True,
                    "asset_share": ratio,
                    "bridge_gap": gap,
                    "fcff": fcff,
                    "impact_per_share": impact,
                    "note": (
                        "影响 = |FCFF 与经营现金流勾稽差额| / |FCFF| × 基准每股价值；"
                        "差额是金融子公司现金流混入合并现金流的信号"
                    ),
                }
            )

    # 4) 少数股东权益为负：桥接按 0 处理，差额单列为近似项
    if inputs.minority_equity < 0:
        items.append(
            {
                "name": "negative_minority_equity",
                "label": "少数股东权益为负",
                "quantified": True,
                "reported_value": inputs.minority_equity,
                "impact_per_share": abs(inputs.minority_equity) / shares,
                "note": "子公司累计亏损超过少数股东投入；桥接按 0 处理，差额计为近似影响",
            }
        )

    quantified = [item for item in items if item["quantified"]]
    total = sum(item["impact_per_share"] for item in quantified) if quantified else 0.0
    return {
        "items": items,
        "quantified_count": len(quantified),
        "unquantified": unquantified,
        "total_impact_per_share": total if not unquantified else None,
        "note": (
            "合计取各项绝对影响之和（保守口径）；存在无法量化项时合计不成立，"
            "按政策不能给 A/B 级"
        ),
    }


def _sensitivity(
    policy: EquityPolicy,
    *,
    model_id: str,
    inputs: CompanyInputs,
    discount: valuation.DiscountBuild,
    base_path: list[float],
    risk_free: float,
    base_value: float | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rate = discount.wacc if model_id == "equity_dcf_fcff" else discount.cost_of_equity
    for delta in (
        -policy.value("sensitivity.discount_rate_delta"),
        policy.value("sensitivity.discount_rate_delta"),
    ):
        try:
            outcome = valuation.value_scenario(
                policy,
                model_id=model_id,
                inputs=inputs,
                discount=discount,
                scenario="base",
                growth=base_path,
                risk_free=risk_free,
                discount_rate_override=rate + delta,
            )
            rows.append(
                {
                    "variable": "discount_rate",
                    "delta": delta,
                    "value_per_share": outcome.value_per_share,
                    "change_pct": (
                        outcome.value_per_share / base_value - 1.0 if base_value else None
                    ),
                }
            )
        except PolicyModelError as exc:
            rows.append(
                {
                    "variable": "discount_rate",
                    "delta": delta,
                    "value_per_share": None,
                    "change_pct": None,
                    "note": str(exc),
                }
            )

    base_terminal = valuation.terminal_spec(
        policy,
        inputs,
        scenario="base",
        risk_free=risk_free,
        cost_of_equity=discount.cost_of_equity,
    )
    for delta in (
        -policy.value("sensitivity.terminal_growth_delta"),
        policy.value("sensitivity.terminal_growth_delta"),
    ):
        target = max(0.0, base_terminal.growth + delta)
        try:
            outcome = valuation.value_scenario(
                policy,
                model_id=model_id,
                inputs=inputs,
                discount=discount,
                scenario="base",
                growth=base_path,
                risk_free=risk_free,
                terminal_growth_override=target,
            )
            rows.append(
                {
                    "variable": "terminal_growth",
                    "delta": delta,
                    "effective_growth": outcome.terminal.growth,
                    "value_per_share": outcome.value_per_share,
                    "change_pct": (
                        outcome.value_per_share / base_value - 1.0 if base_value else None
                    ),
                }
            )
        except PolicyModelError as exc:
            rows.append(
                {
                    "variable": "terminal_growth",
                    "delta": delta,
                    "value_per_share": None,
                    "change_pct": None,
                    "note": str(exc),
                }
            )
    return rows


def _base_record(
    *,
    code: str,
    instrument_id: str,
    name: str | None,
    industry: str | None,
    status: str,
    status_reason: str,
    as_of: str | None,
) -> dict[str, Any]:
    return {
        "code": code,
        "instrument_id": instrument_id,
        "name": name,
        "industry": industry,
        "status": status,
        "status_reason": status_reason,
        "as_of": as_of,
        "trial_conclusion": {
            "conclusion": "undetermined",
            "reason": f"状态 {status}：{status_reason}",
        },
        "grade": {"grade": "C", "unmet_conditions": ["route_not_evaluable"]},
        "reason_codes": [],
    }


def evaluate_company(
    policy: EquityPolicy,
    *,
    snapshot: Snapshot | None,
    evidence: BusinessEvidence | None,
    macro: dict[str, Any],
    code: str,
) -> dict[str, Any]:
    """单只标的的政策试运行结果（数据不足也返回如实状态）。"""
    if snapshot is None:
        return _base_record(
            code=code,
            instrument_id=f"EQUITY:{code}",
            name=evidence.display_name if evidence else None,
            industry=evidence.industry_clue if evidence else None,
            status="insufficient_data",
            status_reason="没有固定快照",
            as_of=None,
        )

    history = load_annual_history(snapshot)
    book = build_dividend_book(load_dividend_records(snapshot), as_of=snapshot.as_of)
    metrics = compute_selection_metrics(snapshot, history, book)
    applicability = decide(evidence, metrics)
    price = price_fact(snapshot)
    shares = _usable_number(snapshot, "profile.total_shares")

    name = evidence.display_name if evidence is not None else None
    industry = evidence.industry_clue if evidence is not None else None
    record: dict[str, Any] = {
        "code": code,
        "instrument_id": snapshot.instrument_id,
        "name": name,
        "industry": industry,
        "as_of": snapshot.as_of,
        "snapshot_id": snapshot.snapshot_id,
        "route": {
            "family": applicability.family,
            "status": applicability.status,
            "primary_model": applicability.primary_model,
            "cross_check_model": applicability.cross_check_model,
            "reasons": list(applicability.reasons),
            "rejected": [r.to_dict() for r in applicability.rejected],
        },
        "price": price["value"] if price else None,
        "price_as_of": price["as_of"] if price else None,
        "shares": shares,
        "reason_codes": [],
        "evidence_id": evidence.evidence_id if evidence is not None else None,
        "snapshot": {
            "facts": len(snapshot.facts),
            "annual_years": [year.fiscal_year for year in history.years],
            "price_source": (snapshot.coverage.get("price") or {}).get("source_id"),
            "statement_source_ids": (
                snapshot.coverage.get("financial_statements") or {}
            ).get("source_ids"),
            "share_source": (snapshot.coverage.get("share_count") or {}).get("source_id"),
            "company_profile_source": (snapshot.coverage.get("company_profile") or {}).get("source_id"),
        },
    }

    if applicability.status == "insufficient_evidence":
        record.update(
            _base_record(
                code=code,
                instrument_id=snapshot.instrument_id,
                name=name,
                industry=industry,
                status="insufficient_data",
                status_reason="缺少结构化业务证据，无法判定路线",
                as_of=snapshot.as_of,
            )
        )
        return record
    if applicability.status == "family_not_implemented":
        record.update(
            _base_record(
                code=code,
                instrument_id=snapshot.instrument_id,
                name=name,
                industry=industry,
                status="unsupported",
                status_reason=f"企业族 {applicability.family} 首版未实现路线：{applicability.applicable_model_category}",
                as_of=snapshot.as_of,
            )
        )
        record["route"]["applicable_model_category"] = applicability.applicable_model_category
        return record

    model_id = applicability.primary_model
    if model_id is None:
        record.update(
            _base_record(
                code=code,
                instrument_id=snapshot.instrument_id,
                name=name,
                industry=industry,
                status="insufficient_data",
                status_reason="适用性判定未给出主模型",
                as_of=snapshot.as_of,
            )
        )
        return record

    if model_id == "bank_dividend_discount" and not _bank_evidence_ok(snapshot):
        record.update(
            _base_record(
                code=code,
                instrument_id=snapshot.instrument_id,
                name=name,
                industry=industry,
                status="insufficient_data",
                status_reason=(
                    "银行路线缺少核心一级资本充足率、不良率、拨备覆盖率或监管下限证据，"
                    "按政策不给正式结论"
                ),
                as_of=snapshot.as_of,
            )
        )
        return record

    if price is None or price["value"] <= 0:
        record.update(
            _base_record(
                code=code,
                instrument_id=snapshot.instrument_id,
                name=name,
                industry=industry,
                status="insufficient_data",
                status_reason="缺少已收盘价格，无法做现价比较",
                as_of=snapshot.as_of,
            )
        )
        return record
    if shares is None or shares <= 0:
        record.update(
            _base_record(
                code=code,
                instrument_id=snapshot.instrument_id,
                name=name,
                industry=industry,
                status="insufficient_data",
                status_reason="缺少当前总股本",
                as_of=snapshot.as_of,
            )
        )
        return record
    if history.latest is None:
        record.update(
            _base_record(
                code=code,
                instrument_id=snapshot.instrument_id,
                name=name,
                industry=industry,
                status="insufficient_data",
                status_reason="没有年报数据",
                as_of=snapshot.as_of,
            )
        )
        return record

    base_year = history.latest.fiscal_year
    rf = valuation.normalized_risk_free(policy, macro)
    record["risk_free"] = rf

    try:
        inputs = valuation.extract_inputs(
            instrument_id=snapshot.instrument_id,
            snapshot=snapshot,
            history=history,
            book=book,
            shares=shares,
            price=price["value"],
            base_year=base_year,
            policy=policy,
        )
    except PolicyModelError as exc:
        record.update(
            _base_record(
                code=code,
                instrument_id=snapshot.instrument_id,
                name=name,
                industry=industry,
                status="insufficient_data",
                status_reason=f"财务比率不可用：{exc}",
                as_of=snapshot.as_of,
            )
        )
        return record

    record["base_year"] = base_year
    prior_year = history.year(base_year - 1)
    prior_revenue = prior_year.number("revenue") if prior_year is not None else None
    prior_profit = (
        prior_year.number("net_profit_parent") if prior_year is not None else None
    )
    revenue_yoy = (
        inputs.revenue / prior_revenue - 1.0
        if prior_revenue not in (None, 0) and prior_revenue > 0
        else None
    )
    profit_yoy = (
        inputs.net_profit_parent / prior_profit - 1.0
        if prior_profit not in (None, 0) and prior_profit > 0 and inputs.net_profit_parent is not None
        else None
    )
    record["base_year_change"] = {
        "fiscal_year": base_year,
        "revenue": inputs.revenue,
        "prior_revenue": prior_revenue,
        "revenue_yoy": revenue_yoy,
        "net_profit_parent": inputs.net_profit_parent,
        "prior_net_profit_parent": prior_profit,
        "profit_yoy": profit_yoy,
    }
    record["inputs"] = {
        "revenue": inputs.revenue,
        "ebit_margin": inputs.ebit_margin,
        "tax_rate": inputs.tax_rate,
        "da_pct": inputs.da_pct,
        "capex_pct": inputs.capex_pct,
        "nwc_intensity": inputs.nwc_intensity,
        "payout": inputs.payout,
        "payout_years": list(inputs.payout_years),
        "roe_latest": inputs.roe_latest,
        "roe_5y": inputs.roe_5y,
        "revenue_cagr5": inputs.revenue_cagr5,
        "year1_growth": inputs.year1_growth,
        "year1_basis": inputs.year1_basis,
        "debt": inputs.debt,
        "debt_components": inputs.debt_components,
        "missing_debt_components": list(inputs.missing_debt_components),
        "average_debt": inputs.average_debt,
        "interest_expense": inputs.interest_expense,
        "monetary_funds": inputs.monetary_funds,
        "cash_equivalents_end": inputs.cash_equivalents_end,
        "restricted_cash_estimate": inputs.restricted_cash_estimate,
        "operating_cash_need": inputs.operating_cash_need,
        "available_cash": inputs.available_cash,
        "minority_equity": inputs.minority_equity,
        "net_profit": inputs.net_profit,
        "net_profit_parent": inputs.net_profit_parent,
        "minority_profit": inputs.minority_profit,
        "loan_advance": inputs.loan_advance,
        "accept_deposit_interbank": inputs.accept_deposit_interbank,
        "total_assets": inputs.total_assets,
        "ocf_5y": inputs.ocf_5y,
        "profit_5y": inputs.profit_5y,
        "loss_years": inputs.loss_years,
        "loss_years_7": inputs.loss_years_7,
        "available_years": inputs.available_years,
        "ebitda": inputs.ebitda,
        "notes": list(inputs.notes),
    }

    discount = valuation.build_discount(policy, inputs, risk_free=rf["normalized"])
    record["discount"] = discount.to_dict()
    leverage = valuation.leverage_guardrail(
        policy, valuation.leverage_ratio(discount.debt, discount.market_cap)
    )
    record["leverage_guardrail"] = leverage

    try:
        scenarios = valuation.value_all_scenarios(
            policy,
            model_id=model_id,
            inputs=inputs,
            discount=discount,
            risk_free=rf["normalized"],
        )
    except PolicyModelError as exc:
        record.update(
            _base_record(
                code=code,
                instrument_id=snapshot.instrument_id,
                name=name,
                industry=industry,
                status="insufficient_data",
                status_reason=f"模型拒绝计算：{exc}",
                as_of=snapshot.as_of,
            )
        )
        return record

    paths = valuation.growth_paths(policy, inputs)
    values = {name: scenario.value_per_share for name, scenario in scenarios.items()}
    record["growth"] = {
        "path_pessimistic": paths["pessimistic"],
        "path_base": paths["base"],
        "path_optimistic": paths["optimistic"],
        "steady_growth": paths["base"][1] if len(paths["base"]) > 1 else None,
        "revenue_cagr5": inputs.revenue_cagr5,
        "year1_growth": inputs.year1_growth,
        "year1_basis": inputs.year1_basis,
    }
    record["values"] = values
    record["terminal"] = {name: scenario.terminal.to_dict() for name, scenario in scenarios.items()}
    record["scenario_detail"] = {name: scenario.detail for name, scenario in scenarios.items()}
    record["terminal_share"] = {
        name: scenario.terminal_share for name, scenario in scenarios.items()
    }

    # 现金流勾稽（P8）
    bridge = None
    status = "complete"
    status_reason = "政策模型完整执行"
    if model_id == "equity_dcf_fcff":
        try:
            bridge = build_fcff_bridge(
                history,
                base_year,
                tolerance=policy.value("quality.bridge_tolerance"),
            )
            record["bridge"] = bridge.to_dict()
            if bridge.reconciled is False:
                status = "partial"
                status_reason = (
                    f"FCFF 与经营现金流勾稽差额占经营现金流 {bridge.reconciliation_relative:.1%}，"
                    f"超过政策容差 {policy.value('quality.bridge_tolerance'):.0%}：结果按 partial 输出"
                )
        except Exception as exc:  # noqa: BLE001 - 勾稽不可完成也不能假装完整
            status = "partial"
            status_reason = f"现金流勾稽无法完成：{type(exc).__name__}: {exc}"
            record["bridge"] = {"error": str(exc)}

    # 交叉检查
    alt_model = _ALT_MODEL.get(model_id)
    cross_check: dict[str, Any] | None = None
    divergence: float | None = None
    if alt_model is not None:
        try:
            alt = valuation.value_scenario(
                policy,
                model_id=alt_model,
                inputs=inputs,
                discount=discount,
                scenario="base",
                growth=paths["base"],
                risk_free=rf["normalized"],
            )
            divergence = alt.value_per_share / values["base"] - 1.0
            cross_check = {
                "model_id": alt_model,
                "value_per_share": alt.value_per_share,
                "divergence": divergence,
                "note": "两条路线共用同一份财务事实与同一政策折现率；差异来自现金流与分配口径",
            }
        except PolicyModelError as exc:
            cross_check = {
                "model_id": alt_model,
                "value_per_share": None,
                "divergence": None,
                "note": f"交叉检查无法计算：{exc}",
            }
    record["cross_check"] = cross_check

    approximations = _approximations(
        policy,
        inputs=inputs,
        model_id=model_id,
        base_value=values["base"],
        bridge=bridge,
    )
    record["approximations"] = approximations

    grade = grading.evaluate_grade(
        policy,
        inputs=inputs,
        base_value=values["base"],
        divergence=divergence,
        approximation_total_per_share=approximations["total_impact_per_share"],
        approximation_unquantified=tuple(approximations["unquantified"]),
        family=applicability.family,
        bank_evidence_ok=_bank_evidence_ok(snapshot),
    )
    if leverage["exceeded"]:
        grade = grading.force_grade_c(
            grade,
            "leverage_guardrail",
            note=(
                f"账面有息负债占比 {leverage['ratio']:.1%} 超过杠杆护栏 "
                f"{leverage['limit']:.0%}：研究价值仅供研究参考，不用于结论"
            ),
        )
        if status == "complete":
            status = "partial"
        status_reason = (
            f"账面有息负债占比 {leverage['ratio']:.1%} 超过杠杆护栏 "
            f"{leverage['limit']:.0%}（M4）：研究价值仅供研究参考，不给正式结论"
        )
        record["reason_codes"] = [rc.LEVERAGE_GUARDRAIL_EXCEEDED]
    record["grade"] = grade.to_dict()

    conclusion = grading.conclude(
        policy,
        grade=grade.grade,
        status=status,
        price=price["value"],
        values=values,
        margin=grade.margin,
    )
    conclusion["is_trial_only"] = True
    conclusion["label"] = "预演结论（未批准政策，不构成正式结论）"
    record["trial_conclusion"] = conclusion

    record["margin_sensitivity"] = grading.margin_sensitivity(
        policy,
        grade=grade.grade,
        status=status,
        price=price["value"],
        values=values,
    )
    record["sensitivity"] = _sensitivity(
        policy,
        model_id=model_id,
        inputs=inputs,
        discount=discount,
        base_path=paths["base"],
        risk_free=rf["normalized"],
        base_value=values["base"],
    )
    record["status"] = status
    record["status_reason"] = status_reason
    return record


def _conclusion_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "candidate_buy_point": 0,
        "wait": 0,
        "avoid": 0,
        "undetermined": 0,
    }
    for record in records:
        conclusion = (record.get("trial_conclusion") or {}).get("conclusion")
        if conclusion in counts:
            counts[conclusion] += 1
    return counts


def _grade_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"A": 0, "B": 0, "C": 0}
    for record in records:
        grade = (record.get("grade") or {}).get("grade")
        if grade in counts:
            counts[grade] += 1
    return counts


def _status_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        status = record.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def _family_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        family = (record.get("route") or {}).get("family") or "unknown"
        counts[family] = counts.get(family, 0) + 1
    return dict(sorted(counts.items()))


def _margin_grid_counts(
    policy: EquityPolicy, records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    grid = policy.node("grade.margin_sensitivity")
    margins: list[float] = []
    for key in ("A", "B"):
        for item in grid[key]:
            value = float(item)
            if value not in margins:
                margins.append(value)
    rows: list[dict[str, Any]] = []
    for margin in sorted(margins):
        counts = {
            "candidate_buy_point": 0,
            "wait": 0,
            "avoid": 0,
            "undetermined": 0,
        }
        candidate_codes: list[str] = []
        for record in records:
            grade = (record.get("grade") or {}).get("grade")
            status = record.get("status")
            values = record.get("values")
            price = record.get("price")
            if grade not in ("A", "B") or status != "complete" or not values or price is None:
                counts["undetermined"] += 1
                continue
            result = grading.conclude(
                policy,
                grade=grade,
                status=status,
                price=price,
                values=values,
                margin=margin,
            )
            counts[result["conclusion"]] += 1
            if result["conclusion"] == "candidate_buy_point":
                candidate_codes.append(record["code"])
        rows.append(
            {
                "margin_of_safety": margin,
                **counts,
                "candidate_codes": candidate_codes,
            }
        )
    return rows


def run_trial(
    policy: EquityPolicy,
    *,
    snapshot_dir: str | Path,
    evidence_dir: str | Path,
    macro_path: str | Path,
    codes: tuple[str, ...],
    generated_at: str,
) -> dict[str, Any]:
    store = SnapshotStore(Path(snapshot_dir))
    evidence_book = EvidenceBook(Path(evidence_dir))
    macro = valuation.load_macro_series(macro_path)

    records: list[dict[str, Any]] = []
    for code in codes:
        venue = "SH" if code.startswith(("6", "9")) else "SZ"
        instrument_id = f"EQUITY:{code}.{venue}"
        latest = store.latest_for(instrument_id)
        snapshot = store.load(latest) if latest else None
        evidence = evidence_book.get(instrument_id)
        record = evaluate_company(
            policy,
            snapshot=snapshot,
            evidence=evidence,
            macro=macro,
            code=code,
        )
        records.append(record)

    rf = valuation.normalized_risk_free(policy, macro)
    summary = {
        "generated_at": generated_at,
        "policy": {
            "policy_id": policy.policy_id,
            "policy_digest": policy.policy_digest,
            "status": policy.status,
            "approved": policy.approved,
            "version_date": policy.version_date,
            "path": policy.path,
        },
        "risk_free": rf,
        "risk_free_series": {
            "series_id": macro.get("series_id"),
            "source": macro.get("source"),
            "month_end_start": macro.get("month_end_start"),
            "month_end_end": macro.get("month_end_end"),
            "latest": macro.get("latest"),
            "months": len(macro.get("month_end_observations") or []),
        },
        "test_set_size": len(codes),
        "reporting_thresholds": {
            "base_year_revenue_yoy_abs": policy.value(
                "reporting.base_year_anomaly.revenue_yoy_abs"
            ),
            "base_year_profit_yoy_abs": policy.value(
                "reporting.base_year_anomaly.profit_yoy_abs"
            ),
        },
        "designer_decisions": list(policy.data.get("designer_decisions_2026_11_12") or []),
        "distribution": {
            "conclusions": _conclusion_counts(records),
            "grades": _grade_counts(records),
            "status": _status_counts(records),
            "families": _family_counts(records),
        },
        "margin_sensitivity": _margin_grid_counts(policy, records),
        "records": records,
        "guardrails": [
            "本报告是个股政策 v1 草案的试运行结果，不是投资建议，也不构成正式结论。",
            "所有结论只写在「预演结论」字段；CLI 输出仍为 research / undetermined。",
            "政策未批准：批准权在股东（决策②），批准前系统不输出正式结论。",
            "测试集是工程样本，不是推荐列表；候选买点数量不作为验收条件。",
        ],
    }
    return summary


__all__ = ["evaluate_company", "run_trial"]
