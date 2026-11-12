"""确定性等级与结论映射（政策 §5、§6，R4–R6、R13）。

所有阈值都从政策文件读取；本模块只做比较与记录，不定义任何数字。
C 级、partial、资料不足、路线不支持、引擎错误一律「无法判断」。

R4：近似项分方向计入——只把「可能让价值变低」的项目计入等级；
只可能让价值变高的保守处理只披露，不参与等级。
R6：两条路线对等——股息路线同样执行现金流勾稽，并加 3 年分红覆盖条件。
R13：窗口内缺数据时该条件判为不通过，并写明缺哪些年份。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engine.policy.equity import EquityPolicy
from engine.policy.valuation import CompanyInputs

GRADE_ORDER = ("A", "B", "C")


@dataclass(frozen=True)
class Condition:
    name: str
    required: str
    observed: Any
    passed: bool
    note: str = ""
    # 距门槛的相对距离；None 表示该条件不适用或无法计算
    distance: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "required": self.required,
            "observed": self.observed,
            "passed": self.passed,
            "note": self.note,
            "distance": self.distance,
        }


@dataclass(frozen=True)
class GradeResult:
    grade: str
    conditions_a: tuple[Condition, ...]
    conditions_b: tuple[Condition, ...]
    unmet: tuple[str, ...]
    margin: float | None
    notes: tuple[str, ...] = ()
    nearest: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "grade": self.grade,
            "margin_of_safety": self.margin,
            "conditions_A": [c.to_dict() for c in self.conditions_a],
            "conditions_B": [c.to_dict() for c in self.conditions_b],
            "unmet_conditions": list(self.unmet),
            "notes": list(self.notes),
            "nearest_condition": self.nearest,
        }


def _ocf_ratio(inputs: CompanyInputs) -> float | None:
    if inputs.profit_5y <= 0:
        return None
    return inputs.ocf_5y / inputs.profit_5y


def _net_debt_ebitda(inputs: CompanyInputs, net_debt: float | None = None) -> float | None:
    if inputs.ebitda is None or inputs.ebitda <= 0:
        return None
    debt = (
        max(0.0, inputs.debt - inputs.available_cash)
        if net_debt is None
        else max(0.0, net_debt)
    )
    return debt / inputs.ebitda


def _relative_distance(observed: float | None, threshold: float, *, upper: bool) -> float | None:
    """相对距离：下限条件 (obs−t)/t；上限条件 (t−obs)/t。"""
    if observed is None or threshold == 0:
        return None
    if upper:
        return (threshold - observed) / abs(threshold)
    return (observed - threshold) / abs(threshold)


def _missing_note(missing: tuple[int, ...]) -> str:
    return "窗口内缺少年份：" + "、".join(str(y) for y in missing)


def evaluate_grade(
    policy: EquityPolicy,
    *,
    inputs: CompanyInputs,
    base_value: float | None,
    divergence: float | None,
    approximation_total_per_share: float | None,
    approximation_unquantified: tuple[str, ...] = (),
    family: str = "non_financial",
    bank_evidence_ok: bool = False,
    model_id: str = "equity_dcf_fcff",
) -> GradeResult:
    a = policy.node("grade.A")
    b = policy.node("grade.B")
    notes: list[str] = []

    ocf_ratio = _ocf_ratio(inputs)
    leverage = _net_debt_ebitda(inputs)
    approx_pct = (
        approximation_total_per_share / base_value
        if base_value and approximation_total_per_share is not None
        else None
    )

    def _approx_condition(limit: float) -> Condition:
        if approximation_unquantified:
            return Condition(
                "approximation_impact",
                f"< 每股价值 {limit:.0%}",
                None,
                False,
                "存在无法量化的「计入等级」近似项：" + "、".join(approximation_unquantified),
            )
        if approx_pct is None:
            return Condition(
                "approximation_impact", f"< 每股价值 {limit:.0%}", None, False, "没有可用的每股价值"
            )
        return Condition(
            "approximation_impact",
            f"< 每股价值 {limit:.0%}",
            approx_pct,
            approx_pct < limit,
            f"计入等级的近似项合计 {approximation_total_per_share:,.2f} 元/股",
            distance=_relative_distance(approx_pct, limit, upper=True),
        )

    dividend_node = policy.node("grade.dividend_coverage")
    dividend_routes = set(dividend_node["applies_to"])
    coverage_applies = model_id in dividend_routes or (
        model_id == "bank_dividend_discount" and "bank_dividend_discount" in dividend_routes
    )
    coverage_ok = (
        inputs.dividend_3y_total is not None
        and inputs.ocf_capex_3y_total is not None
        and inputs.dividend_3y_total <= inputs.ocf_capex_3y_total
        and not inputs.dividend_missing_years
    )

    def _coverage_condition() -> Condition | None:
        if not coverage_applies:
            return None
        if inputs.dividend_missing_years:
            return Condition(
                "dividend_coverage",
                f"近 {dividend_node['window_years']} 年现金分红合计 ≤（经营现金流 − 资本开支）合计",
                None,
                False,
                _missing_note(inputs.dividend_missing_years),
            )
        assert inputs.dividend_3y_total is not None and inputs.ocf_capex_3y_total is not None
        gap = inputs.ocf_capex_3y_total - inputs.dividend_3y_total
        base = abs(inputs.ocf_capex_3y_total) if inputs.ocf_capex_3y_total else None
        return Condition(
            "dividend_coverage",
            f"近 {dividend_node['window_years']} 年现金分红合计 ≤（经营现金流 − 资本开支）合计",
            {
                "dividends": inputs.dividend_3y_total,
                "ocf_minus_capex": inputs.ocf_capex_3y_total,
                "gap": gap,
            },
            coverage_ok,
            "分红总额与（经营现金流 − 资本开支）均按同一 3 个财年合计",
            distance=(gap / base) if base else None,
        )

    def _common_conditions(node: dict[str, Any], *, loss_years: int, missing: tuple[int, ...], label: str) -> tuple[Condition, ...]:
        items: list[Condition] = [
            Condition(
                "available_fiscal_years",
                f">= {node.get('min_available_fiscal_years', node['lookback_fiscal_years'])}",
                inputs.available_years,
                inputs.available_years
                >= node.get("min_available_fiscal_years", node["lookback_fiscal_years"]),
                "可得财年不足时不能声称「无亏损」",
                distance=_relative_distance(
                    inputs.available_years,
                    float(node.get("min_available_fiscal_years", node["lookback_fiscal_years"])),
                    upper=False,
                ),
            ),
            Condition(
                "loss_years",
                f"最近 {node['lookback_fiscal_years']} 年至多 {node['max_loss_years']} 年亏损",
                loss_years if not missing else None,
                not missing and loss_years <= node["max_loss_years"],
                _missing_note(missing)
                if missing
                else ("亏损年份数按可得年报统计" if label == "A" else "B 级 7 年窗口"),
                distance=None
                if missing or node["max_loss_years"] == 0
                else _relative_distance(loss_years, float(node["max_loss_years"]), upper=True),
            ),
            Condition(
                "ocf_to_profit",
                f">= {node['ocf_to_profit_min']:.2f}（近 {node['ocf_window_years']} 年合计）",
                ocf_ratio,
                not inputs.ocf_missing_years
                and ocf_ratio is not None
                and ocf_ratio >= node["ocf_to_profit_min"],
                _missing_note(inputs.ocf_missing_years)
                if inputs.ocf_missing_years
                else "经营现金流合计 / 归母净利润合计",
                distance=None
                if inputs.ocf_missing_years
                else _relative_distance(ocf_ratio, float(node["ocf_to_profit_min"]), upper=False),
            ),
            Condition(
                "net_debt_to_ebitda",
                f"< {node['net_debt_to_ebitda_max']:.1f}",
                leverage,
                leverage is not None and leverage < node["net_debt_to_ebitda_max"],
                "（有息负债 − 可抵扣现金）/ EBITDA，净口径",
                distance=_relative_distance(
                    leverage, float(node["net_debt_to_ebitda_max"]), upper=True
                ),
            ),
            Condition(
                "cross_check_divergence",
                f"< {node['cross_check_divergence_max']:.0%}",
                divergence,
                divergence is not None
                and abs(divergence) < node["cross_check_divergence_max"],
                "第二条路线不可用时无法验证分歧",
                distance=_relative_distance(
                    abs(divergence) if divergence is not None else None,
                    float(node["cross_check_divergence_max"]),
                    upper=True,
                ),
            ),
            _approx_condition(node["approximation_impact_max"]),
        ]
        coverage = _coverage_condition()
        if coverage is not None:
            items.append(coverage)
        return tuple(items)

    conditions_a = _common_conditions(
        a, loss_years=inputs.loss_years, missing=inputs.loss_missing_10, label="A"
    )
    conditions_b = _common_conditions(
        b, loss_years=inputs.loss_years_7, missing=inputs.loss_missing_7, label="B"
    )

    bank_cap = policy.node("grade.banks")
    if family == "bank" and not bank_evidence_ok:
        notes.append(
            "银行路线：未取得资本充足率/不良率/拨备覆盖率，按政策最高只能 C 级"
        )
        return GradeResult(
            grade="C",
            conditions_a=conditions_a,
            conditions_b=conditions_b,
            unmet=("bank_risk_evidence",),
            margin=None,
            notes=tuple(notes),
        )

    def _nearest(
        conditions: tuple[Condition, ...], *, only_unmet: bool
    ) -> dict[str, Any] | None:
        if only_unmet:
            candidates = [c for c in conditions if not c.passed and c.distance is not None]
        else:
            candidates = [c for c in conditions if c.passed and c.distance is not None]
        if not candidates:
            return None
        item = min(candidates, key=lambda c: abs(c.distance or 0.0))
        distance = abs(item.distance or 0.0)
        alert = float(policy.node("grade.threshold_distance")["alert_below"])
        return {
            "condition": item.name,
            "distance": distance,
            "alert": distance < alert,
            "label": policy.node("grade.threshold_distance")["label"] if distance < alert else None,
        }

    if all(c.passed for c in conditions_a):
        return GradeResult(
            grade="A",
            conditions_a=conditions_a,
            conditions_b=conditions_b,
            unmet=(),
            margin=float(a["margin_of_safety"]),
            notes=tuple(notes),
            nearest=_nearest(conditions_a, only_unmet=False),
        )
    unmet_b = tuple(c.name for c in conditions_b if not c.passed)
    if all(c.passed for c in conditions_b):
        notes.append("A 级条件未全部满足，但满足 B 级条件")
        return GradeResult(
            grade="B",
            conditions_a=conditions_a,
            conditions_b=conditions_b,
            unmet=tuple(c.name for c in conditions_a if not c.passed),
            margin=float(b["margin_of_safety"]),
            notes=tuple(notes),
            nearest=_nearest(conditions_b, only_unmet=False),
        )
    if family == "bank" and bank_evidence_ok:
        notes.append("银行路线最高 B 级；本次未达到 B 级条件")
    combined: list[str] = []
    for name in [c.name for c in conditions_a if not c.passed] + list(unmet_b):
        if name not in combined:
            combined.append(name)
    return GradeResult(
        grade="C",
        conditions_a=conditions_a,
        conditions_b=conditions_b,
        unmet=tuple(combined),
        margin=None,
        notes=tuple(notes),
        nearest=_nearest(conditions_b, only_unmet=True),
    )


def force_grade_c(result: GradeResult, name: str, *, note: str = "") -> GradeResult:
    """机械护栏触发时把等级压到 C；保留原条件明细，便于审计。"""
    unmet: list[str] = [name]
    for item in result.unmet:
        if item not in unmet:
            unmet.append(item)
    notes = list(result.notes)
    if note:
        notes.append(note)
    return GradeResult(
        grade="C",
        conditions_a=result.conditions_a,
        conditions_b=result.conditions_b,
        unmet=tuple(unmet),
        margin=None,
        notes=tuple(notes),
        nearest=result.nearest,
    )


def conclude(
    policy: EquityPolicy,
    *,
    grade: str,
    status: str,
    price: float | None,
    values: dict[str, float] | None,
    margin: float | None,
) -> dict[str, Any]:
    """结论映射；返回结论、用到的阈值与起约束条件。"""
    node = policy.node("conclusion")
    if status != "complete":
        return {
            "conclusion": "undetermined",
            "reason": f"结果状态为 {status}：按政策不给正式结论",
            "rule": node["undetermined"]["rule"],
            "thresholds": None,
        }
    if grade not in ("A", "B") or values is None or price is None or margin is None:
        return {
            "conclusion": "undetermined",
            "reason": f"确定性等级 {grade}：按政策不给正式结论",
            "rule": node["undetermined"]["rule"],
            "thresholds": None,
        }
    base = values["base"]
    pess = values["pessimistic"]
    opt = values["optimistic"]
    entry = base * (1.0 - margin)
    binding = "margin_of_safety" if entry <= pess else "pessimistic_value"
    thresholds = {
        "price": price,
        "base_value": base,
        "pessimistic_value": pess,
        "optimistic_value": opt,
        "margin_of_safety": margin,
        "candidate_entry_price": entry,
        "candidate_requires_price_below_pessimistic": pess,
        "avoid_requires_price_above": opt,
        "binding_condition": binding,
        "binding_price": min(entry, pess),
        "binding_condition_text": (
            "安全边际（基准价值 × (1 − M)）"
            if binding == "margin_of_safety"
            else "悲观情景价值"
        ),
    }
    if price <= entry and price <= pess:
        return {
            "conclusion": "candidate_buy_point",
            "reason": "价格不高于基准价值×(1−安全边际)，且不高于悲观情景价值",
            "rule": node["candidate_buy_point"]["rule"],
            "thresholds": thresholds,
        }
    if price > opt:
        return {
            "conclusion": "avoid",
            "reason": "价格高于乐观情景价值",
            "rule": node["avoid"]["rule"],
            "thresholds": thresholds,
        }
    return {
        "conclusion": "wait",
        "reason": "不满足候选买点条件，也未高于乐观情景价值",
        "rule": node["wait"]["rule"],
        "thresholds": thresholds,
    }


def margin_sensitivity(
    policy: EquityPolicy,
    *,
    grade: str,
    status: str,
    price: float | None,
    values: dict[str, float] | None,
) -> list[dict[str, Any]]:
    """安全边际 20/25/30% 与 35/40/45% 下结论如何变化。"""
    grid = policy.node("grade.margin_sensitivity")
    margins: list[float] = []
    for key in ("A", "B"):
        for item in grid[key]:
            value = float(item)
            if value not in margins:
                margins.append(value)
    out: list[dict[str, Any]] = []
    for margin in sorted(margins):
        result = conclude(
            policy,
            grade=grade,
            status=status,
            price=price,
            values=values,
            margin=margin,
        )
        out.append(
            {
                "margin_of_safety": margin,
                "conclusion": result["conclusion"],
                "is_policy_margin": False,
            }
        )
    return out


__all__ = [
    "Condition",
    "GRADE_ORDER",
    "GradeResult",
    "conclude",
    "evaluate_grade",
    "force_grade_c",
    "margin_sensitivity",
]
