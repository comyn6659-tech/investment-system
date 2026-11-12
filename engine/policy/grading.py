"""确定性等级与结论映射（政策 §5、§6）。

所有阈值都从政策文件读取；本模块只做比较与记录，不定义任何数字。
C 级、partial、资料不足、路线不支持、引擎错误一律「无法判断」。
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "required": self.required,
            "observed": self.observed,
            "passed": self.passed,
            "note": self.note,
        }


@dataclass(frozen=True)
class GradeResult:
    grade: str
    conditions_a: tuple[Condition, ...]
    conditions_b: tuple[Condition, ...]
    unmet: tuple[str, ...]
    margin: float | None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "grade": self.grade,
            "margin_of_safety": self.margin,
            "conditions_A": [c.to_dict() for c in self.conditions_a],
            "conditions_B": [c.to_dict() for c in self.conditions_b],
            "unmet_conditions": list(self.unmet),
            "notes": list(self.notes),
        }


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _ocf_ratio(inputs: CompanyInputs) -> float | None:
    if inputs.profit_5y <= 0:
        return None
    return inputs.ocf_5y / inputs.profit_5y


def _net_debt_ebitda(inputs: CompanyInputs) -> float | None:
    if inputs.ebitda is None or inputs.ebitda <= 0:
        return None
    return (inputs.debt - inputs.available_cash) / inputs.ebitda


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
                "存在无法量化的近似项：" + "、".join(approximation_unquantified),
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
            f"近似项合计 {approximation_total_per_share:,.2f} 元/股",
        )

    conditions_a = (
        Condition(
            "available_fiscal_years",
            f">= {a['min_available_fiscal_years']}",
            inputs.available_years,
            inputs.available_years >= a["min_available_fiscal_years"],
            "可得财年不足时不能声称「近 10 年无亏损」",
        ),
        Condition(
            "loss_years",
            f"最近 {a['lookback_fiscal_years']} 年无亏损（可得年份内）",
            inputs.loss_years,
            inputs.loss_years <= a["max_loss_years"],
            "亏损年份数按可得年报统计",
        ),
        Condition(
            "ocf_to_profit",
            f">= {a['ocf_to_profit_min']:.2f}（近 {a['ocf_window_years']} 年合计）",
            ocf_ratio,
            ocf_ratio is not None and ocf_ratio >= a["ocf_to_profit_min"],
            "经营现金流合计 / 归母净利润合计",
        ),
        Condition(
            "net_debt_to_ebitda",
            f"< {a['net_debt_to_ebitda_max']:.1f}",
            leverage,
            leverage is not None and leverage < a["net_debt_to_ebitda_max"],
            "（有息负债 − 可抵扣现金）/ EBITDA",
        ),
        Condition(
            "cross_check_divergence",
            f"< {a['cross_check_divergence_max']:.0%}",
            divergence,
            divergence is not None and abs(divergence) < a["cross_check_divergence_max"],
            "第二条路线不可用时无法验证分歧",
        ),
        _approx_condition(a["approximation_impact_max"]),
    )

    conditions_b = (
        Condition(
            "available_fiscal_years",
            f">= {b['lookback_fiscal_years']}",
            inputs.available_years,
            inputs.available_years >= b["lookback_fiscal_years"],
            "B 级同样要求至少 7 个财年",
        ),
        Condition(
            "loss_years",
            f"最近 {b['lookback_fiscal_years']} 年至多 {b['max_loss_years']} 年亏损",
            inputs.loss_years_7,
            inputs.loss_years_7 <= b["max_loss_years"],
            "亏损年份数按 B 级的 7 年窗口统计",
        ),
        Condition(
            "ocf_to_profit",
            f">= {b['ocf_to_profit_min']:.2f}（近 {b['ocf_window_years']} 年合计）",
            ocf_ratio,
            ocf_ratio is not None and ocf_ratio >= b["ocf_to_profit_min"],
            "经营现金流合计 / 归母净利润合计",
        ),
        Condition(
            "net_debt_to_ebitda",
            f"< {b['net_debt_to_ebitda_max']:.1f}",
            leverage,
            leverage is not None and leverage < b["net_debt_to_ebitda_max"],
            "（有息负债 − 可抵扣现金）/ EBITDA",
        ),
        Condition(
            "cross_check_divergence",
            f"< {b['cross_check_divergence_max']:.0%}",
            divergence,
            divergence is not None and abs(divergence) < b["cross_check_divergence_max"],
            "第二条路线不可用时无法验证分歧",
        ),
        _approx_condition(b["approximation_impact_max"]),
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

    if all(c.passed for c in conditions_a):
        return GradeResult(
            grade="A",
            conditions_a=conditions_a,
            conditions_b=conditions_b,
            unmet=(),
            margin=float(a["margin_of_safety"]),
            notes=tuple(notes),
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
    """结论映射；返回结论、用到的阈值与未满足条件。"""
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
    thresholds = {
        "price": price,
        "base_value": base,
        "pessimistic_value": pess,
        "optimistic_value": opt,
        "margin_of_safety": margin,
        "candidate_entry_price": entry,
        "candidate_requires_price_below_pessimistic": pess,
        "avoid_requires_price_above": opt,
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
