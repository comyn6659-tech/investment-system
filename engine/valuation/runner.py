"""研究估值的编排层：证据 → 适用性 → 假设 → 计算 → 可展示结果。

这里不实现数学（在 `valuation/`），也不读文件（假设与证据由调用方传入）。
产出是一个纯数据结构，供结果载荷与文本报告使用。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from engine.assumptions.schema import AssumptionSet, KIND_RESEARCH, STATUS_CANDIDATE
from engine.contracts import reason_codes as rc
from engine.data.facts import Fact
from engine.data.snapshot import Snapshot
from engine.evidence.applicability import (
    SELECTION_POLICY,
    ApplicabilityDecision,
    SelectionMetrics,
    decide,
)
from engine.evidence.models import BusinessEvidence
from engine.financials.annual import FinancialHistory, load_annual_history, load_dividend_records
from engine.financials.bridge import build_equity_bridge, build_fcff_bridge
from engine.financials.dividends import DividendBook, build_dividend_book
from engine.models.registry import MODELS_BY_ID
from engine.valuation import errors as ve
from engine.valuation.bank import BankDividendDrivers, require_risk_evidence, value_bank_dividend
from engine.valuation.ddm import DividendDrivers, value_dividend
from engine.valuation.errors import ValuationError
from engine.valuation.fcff import (
    EquityBridge,
    FcffDrivers,
    project_fcff,
    terminal_from_last_year,
    value_fcff,
)

# 研究估值与现价「大致相符」的展示带宽：只影响措辞，不构成判断
PRICE_BAND = 0.05

# 研究结果与正式结论的边界：本层永远不产出 candidate / wait / avoid
RESEARCH_DECISION = "undetermined"


@dataclass(frozen=True)
class ScenarioOutcome:
    name: str
    value_per_share: float
    terminal_share: float | None
    detail: dict[str, Any]
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value_per_share": self.value_per_share,
            "terminal_share_of_value": self.terminal_share,
            "note": self.note,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SensitivityRow:
    variable: str
    delta: float
    value_per_share: float | None
    change_pct: float | None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "variable": self.variable,
            "delta": self.delta,
            "value_per_share": self.value_per_share,
            "change_pct": self.change_pct,
            "note": self.note,
        }


@dataclass(frozen=True)
class ResearchValuation:
    status: str  # complete / partial / insufficient_data / unsupported
    reason_codes: tuple[str, ...]
    model_id: str | None
    model_version: str | None
    model_status: str
    assumption_id: str | None
    assumption_status: str
    base_fiscal_year: int | None
    scenarios: tuple[ScenarioOutcome, ...]
    sensitivity: tuple[SensitivityRow, ...]
    valuation_range: dict[str, Any] | None
    price: dict[str, Any] | None
    price_relationship: dict[str, Any] | None
    applicability: dict[str, Any]
    data_used: dict[str, Any]
    cross_check: dict[str, Any] | None
    missing: tuple[dict[str, Any], ...]
    issues: tuple[dict[str, Any], ...]
    invalidation: tuple[str, ...]
    warnings: tuple[str, ...]
    headline_value_per_share: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "model_id": self.model_id,
            "model_version": self.model_version,
            "model_status": self.model_status,
            "assumption_id": self.assumption_id,
            "assumption_status": self.assumption_status,
            "base_fiscal_year": self.base_fiscal_year,
            "headline_value_per_share": self.headline_value_per_share,
            "scenarios": [s.to_dict() for s in self.scenarios],
            "sensitivity": [r.to_dict() for r in self.sensitivity],
            "valuation_range": self.valuation_range,
            "price": self.price,
            "price_relationship": self.price_relationship,
            "applicability": self.applicability,
            "data_used": self.data_used,
            "cross_check": self.cross_check,
            "missing": list(self.missing),
            "issues": list(self.issues),
            "invalidation": list(self.invalidation),
            "warnings": list(self.warnings),
        }


# --- 经营指标 ---------------------------------------------------------------
def _payout_stats(
    book: DividendBook, history: FinancialHistory, shares: float | None
) -> tuple[float | None, float | None, int, float | None]:
    """最近若干财年的派息率、稳定性与样本年数。"""
    pays: list[float] = []
    for entry in book.years:
        if not entry.has_annual_final or entry.ordinary_per_share <= 0:
            continue
        annual = history.year(entry.fiscal_year)
        if annual is None:
            continue
        profit = annual.number("net_profit_parent")
        if profit is None or profit <= 0:
            continue
        if shares is None or shares <= 0:
            continue
        eps = profit / shares
        if eps <= 0:
            continue
        pays.append(entry.ordinary_per_share / eps)
    if not pays:
        return None, None, 0, None
    latest = pays[0]
    mean3 = statistics.fmean(pays[:3]) if len(pays) >= 3 else statistics.fmean(pays)
    cv = None
    if len(pays) >= 2 and statistics.fmean(pays) > 0:
        cv = statistics.pstdev(pays[:3]) / statistics.fmean(pays[:3])
    return latest, mean3, len(pays), cv


def compute_selection_metrics(
    snapshot: Snapshot | None,
    history: FinancialHistory | None,
    book: DividendBook | None,
) -> SelectionMetrics | None:
    if snapshot is None or history is None:
        return None
    shares_fact = snapshot.fact("profile.total_shares")
    shares = float(shares_fact.value) if shares_fact and shares_fact.is_usable_number else None
    latest = history.latest
    if latest is None:
        return None
    capex = latest.number("capex")
    revenue = latest.number("revenue")
    profit = latest.number("net_profit_parent")
    capex_intensity = capex / revenue if capex is not None and revenue else None
    conversion = None
    try:
        bridge = build_fcff_bridge(history, latest.fiscal_year)
        if profit and profit > 0:
            conversion = bridge.fcff / profit
    except ValuationError:
        conversion = None

    payout_latest = payout_mean = cv = None
    years_observed = 0
    if book is not None:
        payout_latest, payout_mean, years_observed, cv = _payout_stats(book, history, shares)
    return SelectionMetrics(
        capex_intensity=capex_intensity,
        cash_conversion=conversion,
        payout_latest=payout_latest,
        payout_mean_3y=payout_mean,
        dividend_years_observed=years_observed,
        dividend_stability_cv=cv,
    )


# --- 价格 -------------------------------------------------------------------
def price_fact(snapshot: Snapshot | None) -> dict[str, Any] | None:
    """只有「已收盘 + 数值可用」的价格才允许用于现价比较。"""
    if snapshot is None:
        return None
    fact = snapshot.fact("price.close")
    if fact is None or not fact.is_usable_number:
        return None
    bar = snapshot.fact("price.bar_status")
    if bar is not None and str(bar.value) != "closed":
        return None
    return {
        "value": float(fact.value),
        "unit": "CNY_per_share",
        "currency": fact.currency or "CNY",
        "as_of": fact.period,
        "price_type": "close_unadjusted",
        "available_at": fact.available_at,
        "source": fact.source.to_dict() if fact.source else None,
        "bar_status": str(bar.value) if bar is not None else None,
    }


def _price_relationship(value: float, price: dict[str, Any]) -> dict[str, Any]:
    base = float(price["value"])
    if base <= 0:
        return {"relation": "undefined", "difference_pct": None, "note": "现价非法"}
    diff = value / base - 1.0
    if abs(diff) <= PRICE_BAND:
        relation = "research_value_in_line_with_price"
    elif diff > 0:
        relation = "research_value_above_price"
    else:
        relation = "research_value_below_price"
    return {
        "relation": relation,
        "difference_pct": diff,
        "band": PRICE_BAND,
        "note": (
            "研究情景估值与当前收盘价的对比，只用于描述差距；"
            "参数未获批准，这不是买点、不是安全边际结论，也不构成正式价格判断"
        ),
    }


# --- 单情景计算 -------------------------------------------------------------
def _fcff_scenario(
    snapshot: Snapshot,
    history: FinancialHistory,
    assumptions: AssumptionSet,
    scenario_name: str,
    *,
    overrides: dict[str, Any] | None = None,
    bridge_tolerance: float,
    absent_as_zero: bool,
    shares: float,
) -> ScenarioOutcome:
    annual = history.year(assumptions.base_fiscal_year)
    if annual is None:
        raise ValuationError(
            ve.MODEL_INPUT_MISSING,
            f"快照中没有 {assumptions.base_fiscal_year} 年年报，无法作为现金流基准",
        )
    scenario = assumptions.scenario(scenario_name)
    drivers_src = dict(scenario.drivers)
    if overrides:
        drivers_src.update(overrides)

    base_revenue = annual.number("revenue")
    if base_revenue is None:
        raise ValuationError(ve.MODEL_INPUT_MISSING, "缺少基期营业收入")

    drivers = FcffDrivers(
        base_year=assumptions.base_fiscal_year,
        base_revenue=base_revenue,
        revenue_growth=tuple(float(x) for x in drivers_src["revenue_growth"]),
        ebit_margin=tuple(float(x) for x in drivers_src["ebit_margin"]),
        tax_rate=tuple(float(x) for x in drivers_src["tax_rate"]),
        da_pct_revenue=tuple(float(x) for x in drivers_src["da_pct_revenue"]),
        capex_pct_revenue=tuple(float(x) for x in drivers_src["capex_pct_revenue"]),
        nwc_pct_incremental_revenue=float(drivers_src["nwc_pct_incremental_revenue"]),
    )
    years = project_fcff(drivers)
    growth = _terminal_growth(assumptions, scenario, overrides)
    terminal = (
        None
        if growth is None
        else terminal_from_last_year(years, growth=growth, basis=assumptions.terminal.basis)
    )
    bridge_build = build_equity_bridge(
        annual, shares=shares, absent_as_zero=absent_as_zero
    )
    rate = _discount_rate(assumptions, scenario, overrides)
    valuation = value_fcff(
        years, discount_rate=rate, terminal=terminal, bridge=bridge_build.bridge
    )
    detail = valuation.to_dict()
    detail["equity_bridge_notes"] = list(bridge_build.notes)
    detail["absent_debt_components"] = list(bridge_build.absent_components)
    detail["annual_basis"] = {
        "fiscal_year": annual.fiscal_year,
        "period_end": annual.period_end,
        "revenue": base_revenue,
        "net_profit_parent": annual.number("net_profit_parent"),
        "source": "合并利润表/资产负债表/现金流量表（快照）",
    }
    return ScenarioOutcome(
        name=scenario_name,
        value_per_share=valuation.value_per_share,
        terminal_share=valuation.stream.to_dict()["terminal_share_of_total"],
        detail=detail,
        note=scenario.note,
    )


def _dividend_scenario(
    snapshot: Snapshot,
    history: FinancialHistory,
    assumptions: AssumptionSet,
    scenario_name: str,
    *,
    overrides: dict[str, Any] | None = None,
    shares: float,
) -> ScenarioOutcome:
    annual = history.year(assumptions.base_fiscal_year)
    if annual is None:
        raise ValuationError(
            ve.MODEL_INPUT_MISSING,
            f"快照中没有 {assumptions.base_fiscal_year} 年年报，无法作为盈利基准",
        )
    profit = annual.number("net_profit_parent")
    if profit is None or profit <= 0:
        raise ValuationError(ve.MODEL_INPUT_MISSING, "基期归母净利润非正，股息路线不适用")
    equity = annual.number("equity_parent")
    roe = profit / equity if equity and equity > 0 else None

    scenario = assumptions.scenario(scenario_name)
    drivers_src = dict(scenario.drivers)
    if overrides:
        drivers_src.update(overrides)
    growth = _terminal_growth(assumptions, scenario, overrides)
    drivers = DividendDrivers(
        base_year=assumptions.base_fiscal_year,
        base_eps=profit / shares,
        eps_growth=tuple(float(x) for x in drivers_src["eps_growth"]),
        payout=tuple(float(x) for x in drivers_src["payout"]),
        cost_of_equity=_discount_rate(assumptions, scenario, overrides),
        terminal_growth=growth,
        roe=roe if roe is not None else float(drivers_src["roe"]),
        growth_consistency_tolerance=float(
            assumptions.extras.get("growth_consistency_tolerance", 0.0)
        ),
    )
    valuation = value_dividend(
        drivers,
        terminal_basis=assumptions.terminal.basis if growth is not None else None,
    )
    detail = valuation.to_dict()
    detail["annual_basis"] = {
        "fiscal_year": annual.fiscal_year,
        "period_end": annual.period_end,
        "net_profit_parent": profit,
        "equity_parent": equity,
        "roe": roe,
        "eps_basis": "归母净利润 / 当前总股本（与 DPS 的每股口径一致）",
    }
    detail["shares"] = shares
    return ScenarioOutcome(
        name=scenario_name,
        value_per_share=valuation.value_per_share,
        terminal_share=valuation.stream.to_dict()["terminal_share_of_total"],
        detail=detail,
        note=scenario.note,
    )


def _bank_scenario(
    snapshot: Snapshot,
    history: FinancialHistory,
    assumptions: AssumptionSet,
    scenario_name: str,
    *,
    overrides: dict[str, Any] | None = None,
    shares: float,
) -> ScenarioOutcome:
    annual = history.year(assumptions.base_fiscal_year)
    if annual is None:
        raise ValuationError(
            ve.MODEL_INPUT_MISSING, f"快照中没有 {assumptions.base_fiscal_year} 年年报"
        )
    profit = annual.number("net_profit_parent")
    if profit is None or profit <= 0:
        raise ValuationError(ve.MODEL_INPUT_MISSING, "基期归母净利润非正，银行股息路线不适用")
    equity = annual.number("equity_parent")
    roe = profit / equity if equity and equity > 0 else None
    if "bank_risk" not in assumptions.extras:
        raise ValuationError(
            ve.BANK_RISK_EVIDENCE_MISSING,
            "假设文件缺少 extras.bank_risk（核心一级资本充足率、监管下限、不良率、"
            "拨备覆盖率及其下限、风险加权资产）；这些阈值必须来自可核实来源",
        )
    risk = require_risk_evidence(dict(assumptions.extras["bank_risk"]))

    scenario = assumptions.scenario(scenario_name)
    drivers_src = dict(scenario.drivers)
    if overrides:
        drivers_src.update(overrides)
    growth = _terminal_growth(assumptions, scenario, overrides)
    drivers = BankDividendDrivers(
        base_year=assumptions.base_fiscal_year,
        base_net_profit_parent=profit,
        net_profit_growth=tuple(float(x) for x in drivers_src["net_profit_growth"]),
        payout=tuple(float(x) for x in drivers_src["payout"]),
        shares=shares,
        cost_of_equity=_discount_rate(assumptions, scenario, overrides),
        terminal_growth=growth,
        roe=roe if roe is not None else float(drivers_src["roe"]),
        growth_consistency_tolerance=float(
            assumptions.extras.get("growth_consistency_tolerance", 0.0)
        ),
    )
    valuation = value_bank_dividend(drivers, risk)
    detail = valuation.to_dict()
    detail["annual_basis"] = {
        "fiscal_year": annual.fiscal_year,
        "period_end": annual.period_end,
        "net_profit_parent": profit,
        "equity_parent": equity,
        "roe": roe,
        "note": "银行路线不使用普通企业净债务与经营现金流口径",
    }
    return ScenarioOutcome(
        name=scenario_name,
        value_per_share=valuation.value_per_share,
        terminal_share=(
            valuation.dividend_valuation.stream.to_dict()["terminal_share_of_total"]
        ),
        detail=detail,
        note=scenario.note,
    )


def _discount_rate(
    assumptions: AssumptionSet, scenario, overrides: dict[str, Any] | None
) -> float:
    if overrides and "discount_rate" in overrides:
        return float(overrides["discount_rate"])
    if scenario.discount_rate is not None:
        return scenario.discount_rate
    return assumptions.discount_rate.value


def _terminal_growth(
    assumptions: AssumptionSet, scenario, overrides: dict[str, Any] | None
) -> float | None:
    if assumptions.terminal.method == "none":
        return None
    if overrides and "terminal_growth" in overrides:
        return float(overrides["terminal_growth"])
    if scenario.terminal_growth is not None:
        return scenario.terminal_growth
    return assumptions.terminal.growth


_SCENARIO_RUNNERS = {
    "equity_dcf_fcff": _fcff_scenario,
    "equity_dividend_discount": _dividend_scenario,
    "bank_dividend_discount": _bank_scenario,
}


def _run_scenario(
    model_id: str,
    snapshot: Snapshot,
    history: FinancialHistory,
    assumptions: AssumptionSet,
    name: str,
    *,
    overrides: dict[str, Any] | None = None,
    shares: float,
) -> ScenarioOutcome:
    runner = _SCENARIO_RUNNERS[model_id]
    if model_id == "equity_dcf_fcff":
        return runner(  # type: ignore[misc]
            snapshot,
            history,
            assumptions,
            name,
            overrides=overrides,
            bridge_tolerance=float(assumptions.extras.get("bridge_tolerance", 0.15)),
            absent_as_zero=bool(assumptions.extras.get("absent_as_zero", False)),
            shares=shares,
        )
    return runner(  # type: ignore[misc]
        snapshot, history, assumptions, name, overrides=overrides, shares=shares
    )


# --- 敏感性 ----------------------------------------------------------------
_SENSITIVITY_VARIABLES = {
    "equity_dcf_fcff": {
        "revenue_growth": "收入增速",
        "ebit_margin": "EBIT 利润率",
        "capex_pct_revenue": "资本开支/收入",
        "discount_rate": "折现率",
        "terminal_growth": "终值增长",
    },
    "equity_dividend_discount": {
        "eps_growth": "每股收益增速",
        "payout": "派息率",
        "discount_rate": "股权成本",
        "terminal_growth": "终值增长",
    },
    "bank_dividend_discount": {
        "net_profit_growth": "净利润增速",
        "payout": "派息率",
        "discount_rate": "股权成本",
        "terminal_growth": "终值增长",
    },
}


def _apply_delta(
    model_id: str, base_drivers: dict[str, Any], variable: str, delta: float
) -> dict[str, Any]:
    if variable in ("discount_rate", "terminal_growth"):
        if model_id == "equity_dcf_fcff" and variable == "discount_rate":
            pass
        out = {variable: delta}
        if variable == "terminal_growth":
            out = {"terminal_growth": delta}
        return out
    series = list(base_drivers[variable])
    return {variable: [value + delta for value in series]}


def _sensitivity(
    model_id: str,
    snapshot: Snapshot,
    history: FinancialHistory,
    assumptions: AssumptionSet,
    base: ScenarioOutcome,
    *,
    shares: float,
) -> tuple[SensitivityRow, ...]:
    steps: dict[str, float] = dict(assumptions.extras.get("sensitivity") or {})
    labels = _SENSITIVITY_VARIABLES[model_id]
    scenario = assumptions.scenario("base")
    rows: list[SensitivityRow] = []
    for variable, step in steps.items():
        if variable not in labels:
            continue
        for sign in (1.0, -1.0):
            delta = sign * step
            overrides: dict[str, Any]
            if variable in ("discount_rate", "terminal_growth"):
                if variable == "discount_rate":
                    overrides = {"discount_rate": _discount_rate(assumptions, scenario, None) + delta}
                else:
                    base_g = _terminal_growth(assumptions, scenario, None)
                    if base_g is None:
                        continue
                    overrides = {"terminal_growth": base_g + delta}
            else:
                overrides = _apply_delta(model_id, scenario.drivers, variable, delta)
            try:
                outcome = _run_scenario(
                    model_id,
                    snapshot,
                    history,
                    assumptions,
                    "base",
                    overrides=overrides,
                    shares=shares,
                )
            except ValuationError as exc:
                rows.append(
                    SensitivityRow(
                        variable=variable,
                        delta=delta,
                        value_per_share=None,
                        change_pct=None,
                        note=f"该方向上模型拒绝计算：{exc.message}",
                    )
                )
                continue
            change = (
                outcome.value_per_share / base.value_per_share - 1.0
                if base.value_per_share
                else None
            )
            rows.append(
                SensitivityRow(
                    variable=labels[variable],
                    delta=delta,
                    value_per_share=outcome.value_per_share,
                    change_pct=change,
                    note=f"{variable} {'+' if delta >= 0 else ''}{delta:+.4f}（研究假设区间）",
                )
            )
    return tuple(rows)


def _bank_evidence_gap(snapshot: Snapshot | None) -> dict[str, Any]:
    """银行路线需要的证据：快照里到底缺哪些，连带说明已尝试的接口。"""
    required = {
        "bank.cet1_ratio": "核心一级资本充足率",
        "bank.required_cet1_minimum": "适用的核心一级资本监管下限",
        "bank.npl_ratio": "不良贷款率",
        "bank.provision_coverage": "拨备覆盖率",
        "bank.risk_weighted_assets": "风险加权资产",
    }
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    coverage = (snapshot.coverage or {}).get("bank_specific") if snapshot else None
    attempts = (coverage or {}).get("attempts") or []
    for fact_id, label in required.items():
        fact = snapshot.fact(fact_id) if snapshot is not None else None
        if fact is not None and fact.is_usable_number:
            continue
        missing.append(fact_id)
        rows.append(
            {
                "fact_id": fact_id,
                "metric": label,
                "status": fact.status.value if fact is not None else "absent",
                "note": (
                    (fact.notes if fact is not None else "")
                    or "快照中没有该字段"
                ),
            }
        )
    if missing and attempts:
        rows.append(
            {
                "fact_id": "bank_metric_probe",
                "metric": "已尝试的银行专项指标接口",
                "status": "missing",
                "note": "；".join(attempts),
            }
        )
    return {"missing": missing, "rows": rows}


# --- 主入口 -----------------------------------------------------------------
def _dedupe(items: list[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return tuple(seen)


def _missing_from_facts(snapshot: Snapshot | None, names: tuple[str, ...]) -> list[dict[str, Any]]:
    if snapshot is None:
        return []
    out: list[dict[str, Any]] = []
    for fact in snapshot.facts:
        if fact.is_usable_number:
            continue
        if not any(fact.fact_id.startswith(prefix) for prefix in names):
            continue
        out.append(
            {
                "fact_id": fact.fact_id,
                "metric": fact.metric,
                "status": fact.status.value,
                "period": fact.period,
                "note": fact.notes,
            }
        )
    return sorted(out, key=lambda x: x["fact_id"])


def run_research_valuation(
    *,
    instrument_id: str,
    snapshot: Snapshot | None,
    evidence: BusinessEvidence | None,
    assumptions: AssumptionSet | None,
    cross_check_assumptions: AssumptionSet | None = None,
) -> ResearchValuation:
    history = load_annual_history(snapshot) if snapshot is not None else None
    book = (
        build_dividend_book(load_dividend_records(snapshot), as_of=snapshot.as_of)
        if snapshot is not None
        else None
    )
    metrics = compute_selection_metrics(snapshot, history, book)
    applicability = decide(evidence, metrics)
    price = price_fact(snapshot)

    data_used: dict[str, Any] = {
        "snapshot_id": snapshot.snapshot_id if snapshot else None,
        "as_of": snapshot.as_of if snapshot else None,
        "available_at": snapshot.available_at if snapshot else None,
        "price": price,
        "annual_years": (
            [y.fiscal_year for y in history.years] if history is not None else []
        ),
        "annual_notes": list(history.notes) if history is not None else [],
        "dividend_book": book.to_dict() if book is not None else None,
        "selection_metrics": metrics.to_dict() if metrics is not None else None,
        "selection_policy": dict(SELECTION_POLICY),
    }

    base_meta = {
        "model_status": "research",
        "assumption_id": assumptions.assumption_id if assumptions else None,
        "assumption_status": assumptions.status if assumptions else "not_provided",
        "applicability": applicability.to_dict(),
    }

    def build(
        status: str,
        codes: list[str],
        *,
        model_id: str | None = None,
        scenarios: tuple[ScenarioOutcome, ...] = (),
        sensitivity: tuple[SensitivityRow, ...] = (),
        valuation_range: dict[str, Any] | None = None,
        cross_check: dict[str, Any] | None = None,
        missing: tuple[dict[str, Any], ...] = (),
        issues: tuple[dict[str, Any], ...] = (),
        invalidation: tuple[str, ...] = (),
        warnings: tuple[str, ...] = (),
    ) -> ResearchValuation:
        headline = scenarios[0].value_per_share if scenarios else None
        return ResearchValuation(
            status=status,
            reason_codes=_dedupe(codes),
            model_id=model_id,
            model_version=(
                MODELS_BY_ID[model_id].version if model_id in MODELS_BY_ID else None
            ),
            model_status=base_meta["model_status"],
            assumption_id=base_meta["assumption_id"],
            assumption_status=base_meta["assumption_status"],
            base_fiscal_year=assumptions.base_fiscal_year if assumptions else None,
            scenarios=scenarios,
            sensitivity=sensitivity,
            valuation_range=valuation_range,
            price=price,
            price_relationship=(
                _price_relationship(headline, price)
                if headline is not None and price is not None
                else None
            ),
            applicability=base_meta["applicability"],
            data_used=data_used,
            cross_check=cross_check,
            missing=missing,
            issues=issues,
            invalidation=invalidation,
            warnings=warnings,
            headline_value_per_share=headline,
        )

    # 1) 未实现路线 / 证据不足
    if applicability.status == "insufficient_evidence":
        codes = [rc.MISSING_BUSINESS_EVIDENCE, rc.RESEARCH_PARAMETERS_UNAPPROVED]
        return build(
            "insufficient_data",
            codes,
            model_id=None,
            missing=(
                {
                    "fact_id": "business_evidence",
                    "status": "missing",
                    "note": "缺少结构化业务证据，无法判断模型适用性；不按行业名称直接套用模型",
                },
            ),
            invalidation=(
                "取得可核实的业务结构与经营数据后，需要重新判定模型适用性。",
            ),
            warnings=(
                "本结果停在「尚不能判断」：既没有套用默认模型，也没有给出任何估值数字。",
            ),
        )

    if applicability.status == "family_not_implemented":
        model_spec = applicability
        return build(
            "unsupported",
            [rc.MODEL_FAMILY_NOT_IMPLEMENTED, rc.RESEARCH_PARAMETERS_UNAPPROVED],
            model_id=None,
            missing=(
                {
                    "fact_id": "applicable_model_data",
                    "status": "not_applicable",
                    "note": (
                        f"适用模型类别：{model_spec.applicable_model_category}；"
                        "所需证据见 applicability.required_evidence_to_implement"
                    ),
                },
            ),
            invalidation=(
                "首版实现对应的模型类别后，本结论需要重跑（当前不是「无投资价值」的判断）。",
            ),
            warnings=(
                "该企业族首版未实现估值路线，因此没有进入任何现金流折现模型。",
            ),
        )

    # 2) 银行：先检查资本与资产质量证据是否齐备（缺失就停，不猜默认值）
    if applicability.primary_model == "bank_dividend_discount":
        gap = _bank_evidence_gap(snapshot)
        if gap["missing"]:
            return build(
                "insufficient_data",
                [
                    rc.MISSING_BANK_RISK_EVIDENCE,
                    rc.RESEARCH_PARAMETERS_UNAPPROVED,
                ],
                model_id="bank_dividend_discount",
                missing=tuple(gap["rows"]),
                invalidation=(
                    "补齐核心一级资本充足率、不良率、拨备覆盖率、风险加权资产及监管下限后，"
                    "银行路线才能计算；在此之前不给任何研究估值数字。",
                ),
                warnings=(
                    "银行路线已实现数学与约束，但关键资本/资产质量证据缺失，因此停在资料不足。",
                    "没有用普通企业净债务或经营现金流门槛代替银行口径。",
                ),
            )

    # 3) 需要假设才能计算
    if assumptions is None:
        return build(
            "insufficient_data",
            [rc.MISSING_MODEL_INPUT, rc.RESEARCH_PARAMETERS_UNAPPROVED],
            model_id=applicability.primary_model,
            missing=(
                {
                    "fact_id": "assumption_set",
                    "status": "missing",
                    "note": "未提供研究假设文件（--assumptions），模型无法运行",
                },
            ),
            invalidation=("提供研究假设后需要重新运行。",),
            warnings=("适用性已判定，但缺少研究假设，因此没有计算任何估值数字。",),
        )

    model_id = applicability.primary_model
    if model_id is None:
        return build(
            "insufficient_data",
            [rc.MODEL_SELECTION_NOT_IMPLEMENTED],
            model_id=None,
            warnings=("适用性判定未给出主模型。",),
        )
    if assumptions.model_id != model_id:
        return build(
            "insufficient_data",
            [rc.MISSING_MODEL_INPUT],
            model_id=model_id,
            missing=(
                {
                    "fact_id": "assumption_set.model_id",
                    "status": "conflict",
                    "note": (
                        f"假设文件适用于 {assumptions.model_id}，"
                        f"但适用性判定选定的主模型是 {model_id}"
                    ),
                },
            ),
            invalidation=("改用与主模型一致的假设文件后重跑。",),
        )
    if snapshot is None or history is None or history.latest is None:
        return build(
            "insufficient_data",
            [rc.SNAPSHOT_NOT_FOUND, rc.MISSING_MODEL_INPUT],
            model_id=model_id,
            warnings=("缺少可用快照，无法取得财务事实。",),
        )
    if assumptions.instrument_id != instrument_id:
        return build(
            "insufficient_data",
            [rc.MISSING_MODEL_INPUT],
            model_id=model_id,
            missing=(
                {
                    "fact_id": "assumption_set.instrument_id",
                    "status": "conflict",
                    "note": (
                        f"假设文件属于 {assumptions.instrument_id}，与请求标的 {instrument_id} 不一致"
                    ),
                },
            ),
            invalidation=("改用本标的的假设文件后重跑。",),
        )
    if assumptions.applicable_as_of != snapshot.as_of:
        return build(
            "insufficient_data",
            [rc.DATA_STALE, rc.MISSING_MODEL_INPUT],
            model_id=model_id,
            missing=(
                {
                    "fact_id": "assumption_set.applicable_as_of",
                    "status": "stale",
                    "note": (
                        f"假设适用于 {assumptions.applicable_as_of}，当前快照 as_of={snapshot.as_of}"
                    ),
                },
            ),
            invalidation=("假设与数据时点对齐后重跑。",),
        )

    shares_fact = snapshot.fact("profile.total_shares")
    if shares_fact is None or not shares_fact.is_usable_number or float(shares_fact.value) <= 0:
        return build(
            "insufficient_data",
            [rc.MISSING_SHARE_COUNT, rc.MISSING_MODEL_INPUT],
            model_id=model_id,
            missing=(
                {
                    "fact_id": "profile.total_shares",
                    "status": "missing",
                    "note": "缺少当前总股本，无法把股权价值换算为每股价值",
                },
            ),
            invalidation=("取得核实股本后重跑。",),
        )
    shares = float(shares_fact.value)

    codes: list[str] = [rc.MODEL_RESEARCH_VALUATION, rc.RESEARCH_PARAMETERS_UNAPPROVED]
    issues: list[dict[str, Any]] = []
    warnings: list[str] = []
    missing: list[dict[str, Any]] = []

    # 3) 历史桥接 / 分红账本中的问题
    if model_id == "equity_dcf_fcff":
        try:
            bridge = build_fcff_bridge(
                history,
                assumptions.base_fiscal_year,
                tolerance=float(assumptions.extras.get("bridge_tolerance", 0.15)),
            )
            data_used["fcff_bridge"] = bridge.to_dict()
            if bridge.reconciled is False:
                codes.append(rc.BRIDGE_RECONCILIATION_GAP)
                issues.append(
                    {
                        "code": ve.BRIDGE_RECONCILIATION_FAILED,
                        "message": (
                            f"历史 FCFF 与经营现金流勾稽差额 {bridge.reconciliation_gap:,.0f} 元"
                            f"（占经营现金流 {bridge.reconciliation_relative:.1%}），"
                            "说明还有未解释的非现金/重分类项目；本结果按 top-down 桥接计算，"
                            "差额如实披露"
                        ),
                        "blocking": False,
                    }
                )
            for note in bridge.issues:
                warnings.append(f"桥接缺口：{note}")
        except ValuationError as exc:
            return build(
                "insufficient_data",
                [rc.MISSING_MODEL_INPUT, rc.RESEARCH_PARAMETERS_UNAPPROVED],
                model_id=model_id,
                missing=(
                    {
                        "fact_id": "fcff_bridge",
                        "status": "missing",
                        "note": exc.message,
                    },
                ),
                invalidation=("补齐桥接所需科目后重跑。",),
            )

    if book is not None:
        if book.unlabeled_special:
            codes.append(rc.SPECIAL_DIVIDEND_SEPARATED)
            warnings.append(
                "特别分红（含所属财年缺失的记录）单列，未进入可持续 DPS 序列："
                + "、".join(f"{r.per_share:.4f} 元/股" for r in book.unlabeled_special)
            )
        if book.unlabeled_ordinary:
            codes.append(rc.DIVIDEND_FISCAL_YEAR_MISSING)
        if book.excluded_not_implemented:
            warnings.append(
                f"{len(book.excluded_not_implemented)} 条分红记录尚未实施，未计入财年合计"
            )
        if book.excluded_other_kinds:
            warnings.append(
                f"{len(book.excluded_other_kinds)} 条非现金分红类型（如股改分红）未计入 DPS"
            )

    # 4) 股本口径交叉核对
    latest = history.year(assumptions.base_fiscal_year)
    if latest is not None:
        profit = latest.number("net_profit_parent")
        eps = latest.number("eps_basic")
        if profit and eps and eps > 0:
            weighted = profit / eps
            divergence = abs(shares - weighted) / weighted
            data_used["share_count_check"] = {
                "current_total_shares": shares,
                "weighted_shares_from_eps": weighted,
                "relative_difference": divergence,
                "note": "当前股本用于每股换算；加权股数由归母净利润/基本每股收益反推",
            }
            if divergence > 0.02:
                codes.append(rc.SHARE_COUNT_DIVERGENCE)
                warnings.append(
                    f"当前股本 {shares:,.0f} 与报告期加权股数 {weighted:,.0f} 相差 "
                    f"{divergence:.1%}：每股价值按当前股本计算，历史可比性下降"
                )

    # 5) 情景计算
    try:
        scenario_names = tuple(assumptions.scenarios)
        ordered = ("base",) + tuple(n for n in scenario_names if n != "base")
        scenarios = tuple(
            _run_scenario(
                model_id, snapshot, history, assumptions, name, shares=shares
            )
            for name in ordered
        )
    except ValuationError as exc:
        status = "insufficient_data"
        code_map = {
            ve.BANK_RISK_EVIDENCE_MISSING: rc.MISSING_BANK_RISK_EVIDENCE,
            ve.BANK_CAPITAL_BELOW_REQUIREMENT: rc.BANK_CAPITAL_CONSTRAINT,
            ve.BANK_PROVISION_BELOW_REQUIREMENT: rc.BANK_CAPITAL_CONSTRAINT,
            ve.GROWTH_NOT_FUNDED_BY_RETENTION: rc.GROWTH_NOT_FUNDED_BY_RETENTION,
            ve.DISTRIBUTION_NOT_COVERED_BY_EARNINGS: rc.EXCESS_DISTRIBUTION,
        }
        codes.append(code_map.get(exc.code, rc.MISSING_MODEL_INPUT))
        return build(
            status,
            codes,
            model_id=model_id,
            missing=(
                {
                    "fact_id": "model_input",
                    "status": "missing",
                    "note": exc.message,
                },
            ),
            invalidation=("补齐该项证据或修正假设后重跑。",),
            warnings=("模型拒绝计算，因此没有输出任何研究估值数字。",),
        )

    base = next((s for s in scenarios if s.name == "base"), scenarios[0])
    sensitivity = _sensitivity(
        model_id, snapshot, history, assumptions, base, shares=shares
    )

    values = [s.value_per_share for s in scenarios]
    valuation_range = {
        "low": min(values),
        "mid": base.value_per_share,
        "high": max(values),
        "basis": "scenario_range_not_confidence_interval",
        "scenario_names": [s.name for s in scenarios],
        "note": (
            "上下界来自输入情景，不是统计置信区间；"
            "情景由假设定义，未按估值高低挑选模型"
        ),
    }

    # 6) 交叉检查
    cross_check: dict[str, Any] | None = None
    if cross_check_assumptions is not None:
        alt = cross_check_assumptions
        try:
            if alt.instrument_id != instrument_id:
                raise ValuationError(
                    ve.MODEL_INPUT_MISSING,
                    f"交叉检查假设属于 {alt.instrument_id}，与请求标的不一致",
                )
            alt_outcome = _run_scenario(
                alt.model_id, snapshot, history, alt, "base", shares=shares
            )
            gap = alt_outcome.value_per_share / base.value_per_share - 1.0
            cross_check = {
                "model_id": alt.model_id,
                "model_version": MODELS_BY_ID[alt.model_id].version
                if alt.model_id in MODELS_BY_ID
                else None,
                "assumption_id": alt.assumption_id,
                "value_per_share": alt_outcome.value_per_share,
                "difference_pct": gap,
                "terminal_share_of_value": alt_outcome.terminal_share,
                "note": (
                    "两条路线共用同一份财务事实，但驱动项不同；"
                    "差异用于说明模型口径分歧，不取平均"
                ),
            }
            if abs(gap) > 0.25:
                codes.append(rc.CROSS_CHECK_DIVERGENCE)
                issues.append(
                    {
                        "code": rc.CROSS_CHECK_DIVERGENCE,
                        "message": (
                            f"主模型与交叉检查模型相差 {gap:.1%}："
                            "差异来自分配路径与自由现金流口径，需在复查时解释"
                        ),
                        "blocking": False,
                    }
                )
        except ValuationError as exc:
            cross_check = {
                "model_id": alt.model_id,
                "value_per_share": None,
                "note": f"交叉检查模型未得到结果：{exc.code}: {exc.message}",
            }
            warnings.append(f"交叉检查未完成：{exc.message}")
    else:
        warnings.append(
            "未提供交叉检查假设文件：本结果只有主模型，没有第二条路线的对照"
        )

    # 7) 状态与阻断
    blocking_codes = {
        rc.BRIDGE_RECONCILIATION_GAP,
        rc.CROSS_CHECK_DIVERGENCE,
    }
    status = "partial" if blocking_codes & set(codes) else "complete"
    if status == "partial":
        warnings.append(
            "勾稽/交叉检查存在未解释差额：结论按「部分完成」输出，不把差额摊平后当作完整结果"
        )

    invalidation = [
        f"若 {assumptions.base_fiscal_year} 年报数据被更正（重述），本次研究估值需要重算。",
        "若折现率、终值增长或分配路径的研究假设被修订，结论随之改变。",
        "若公司业务结构发生变化（新增金融子公司、重大并购或剥离），适用性需要重新判定。",
    ]
    if model_id == "bank_dividend_discount":
        invalidation.append("若资本充足率、不良率或拨备覆盖率证据更新，银行路线的约束条件需要重跑。")
    if price is None:
        codes.append(rc.PRICE_UNAVAILABLE_FOR_COMPARISON)
        warnings.append("缺少可核实的收盘价：本结果没有做任何现价比较。")

    if assumptions.status != STATUS_CANDIDATE:
        warnings.append(
            f"假设文件状态为 {assumptions.status}，不是正式研究候选；仅用于流程验证"
        )

    research_kinds = [
        name
        for name, prov in assumptions.provenance.items()
        if prov.kind == KIND_RESEARCH
    ]
    data_used["research_hypothesis_parameters"] = research_kinds

    return build(
        status,
        codes,
        model_id=model_id,
        scenarios=scenarios,
        sensitivity=sensitivity,
        valuation_range=valuation_range,
        cross_check=cross_check,
        missing=tuple(missing),
        issues=tuple(issues),
        invalidation=tuple(invalidation),
        warnings=tuple(warnings),
    )


__all__ = [
    "PRICE_BAND",
    "RESEARCH_DECISION",
    "ResearchValuation",
    "ScenarioOutcome",
    "SensitivityRow",
    "compute_selection_metrics",
    "price_fact",
    "run_research_valuation",
]
