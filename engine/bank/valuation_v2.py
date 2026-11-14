"""银行专用估值草案 v2：合理市净率法（剩余收益法的稳态形式），只做试运行。

所有参数从 `data/policies/bank-valuation-v2-draft.json` 读取；本模块不另设业务数字。
不写正式账本，不改变 CLI 银行结论。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.policy.equity import EquityPolicy
from engine.policy.valuation import normalized_risk_free

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_POLICY = ROOT / "data" / "policies" / "bank-valuation-v2-draft.json"
POLICY_SCHEMA = "bank-valuation-policy/1"


class BankValuationV2Error(RuntimeError):
    pass


def load_policy(path: Path | None = None) -> dict[str, Any]:
    node = Path(path or DEFAULT_POLICY)
    payload = json.loads(node.read_text(encoding="utf-8"))
    if payload.get("schema_version") != POLICY_SCHEMA:
        raise BankValuationV2Error(f"银行估值 v2 schema 非法：{payload.get('schema_version')!r}")
    if payload.get("status") != "draft_unapproved":
        raise BankValuationV2Error("银行估值 v2 必须是未批准草案")
    return payload


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class BankValuationInputs:
    code: str
    name: str
    in_scope: bool
    bvps: float | None
    shares: float | None
    price: float | None
    price_date: str | None
    roe_3y_avg: float | None
    roe_5y_avg: float | None
    roe_5y_min: float | None
    npl_latest: float | None
    npl_change_5y: float | None
    provision_coverage: float | None
    loss_years_7: int | None
    data_complete: bool
    in_bank_pool: bool

    @classmethod
    def from_row(cls, row: dict[str, Any], *, in_bank_pool: bool) -> "BankValuationInputs":
        return cls(
            code=str(row["code"]).zfill(6),
            name=str(row.get("name") or ""),
            in_scope=str(row.get("in_scope")) == "True",
            bvps=_num(row.get("bvps")),
            shares=_num(row.get("shares")),
            price=_num(row.get("price")),
            price_date=str(row.get("price_date") or "") or None,
            roe_3y_avg=_num(row.get("roe_3y_avg")),
            roe_5y_avg=_num(row.get("roe_5y_avg")),
            roe_5y_min=_num(row.get("roe_5y_min")),
            npl_latest=_num(row.get("npl_latest")),
            npl_change_5y=_num(row.get("npl_change_5y")),
            provision_coverage=_num(row.get("provision_coverage")),
            loss_years_7=(
                int(float(row["loss_years_7"]))
                if row.get("loss_years_7") not in (None, "")
                else None
            ),
            data_complete=str(row.get("data_complete")) == "True",
            in_bank_pool=in_bank_pool,
        )


def _cap_growth(growth: float, risk_free: float) -> float:
    return min(float(growth), float(risk_free))


def _asset_quality_deduction(inputs: BankValuationInputs, policy: dict[str, Any]) -> bool:
    node = policy["parameters"]["asset_quality_deduction"]
    provision = inputs.provision_coverage
    change = inputs.npl_change_5y
    if provision is None or change is None:
        return False
    return (
        provision < float(node["trigger_provision_coverage_pct"])
        or change > float(node["trigger_npl_change_pp"])
    )


def value_bank(
    inputs: BankValuationInputs,
    policy: dict[str, Any],
    equity_policy: EquityPolicy,
    macro: dict[str, Any],
    *,
    premium: float | None = None,
    margin: float | None = None,
) -> dict[str, Any]:
    rf = normalized_risk_free(equity_policy, macro)["normalized"]
    erp = equity_policy.value("discount_rate.equity_risk_premium")
    premium_value = float(policy["parameters"]["cost_of_equity"]["bank_premium"] if premium is None else premium)
    margin_value = float(policy["parameters"]["margin_of_safety"]["value"] if margin is None else margin)
    k = rf + erp + premium_value
    growth = policy["parameters"]["growth"]
    deduction = 1.0 if _asset_quality_deduction(inputs, policy) else 0.0
    deduct = float(policy["parameters"]["asset_quality_deduction"]["deduction"]) * deduction

    roe = {
        "base": (min(inputs.roe_3y_avg, inputs.roe_5y_avg) - deduct)
        if inputs.roe_3y_avg is not None and inputs.roe_5y_avg is not None
        else None,
        "pessimistic": (
            inputs.roe_5y_min - float(policy["parameters"]["roe_pessimistic"]["deduction"]) - deduct
        )
        if inputs.roe_5y_min is not None
        else None,
        "optimistic": (inputs.roe_5y_avg - deduct) if inputs.roe_5y_avg is not None else None,
    }
    g = {
        "base": _cap_growth(growth["base"], rf),
        "pessimistic": _cap_growth(growth["pessimistic"], rf),
        "optimistic": _cap_growth(growth["optimistic"], rf),
    }
    values: dict[str, float | None] = {}
    multiples: dict[str, float | None] = {}
    for name in ("pessimistic", "base", "optimistic"):
        roe_value = roe[name]
        if inputs.bvps is None or roe_value is None or k <= g[name]:
            values[name] = None
            multiples[name] = None
            continue
        multiples[name] = (roe_value - g[name]) / (k - g[name])
        values[name] = inputs.bvps * multiples[name]

    base_value = values.get("base")
    pessimistic = values.get("pessimistic")
    optimistic = values.get("optimistic")
    candidate_entry = (
        base_value * (1.0 - margin_value) if base_value is not None else None
    )
    binding = None
    threshold = None
    if candidate_entry is not None and pessimistic is not None:
        if candidate_entry <= pessimistic:
            binding = "margin_of_safety"
            threshold = candidate_entry
        else:
            binding = "pessimistic_value"
            threshold = pessimistic

    reasons: list[str] = []
    eligible = True
    if not inputs.in_bank_pool:
        eligible = False
        reasons.append("不在已批准银行池（银行池 11 家）")
    if not inputs.data_complete:
        eligible = False
        reasons.append("银行证据不齐全")
    max_loss = int(policy["eligibility"]["max_loss_years_7"])
    if inputs.loss_years_7 is None or inputs.loss_years_7 > max_loss:
        eligible = False
        reasons.append("最近 7 年有亏损")
    if any(values.get(name) is None or values[name] <= 0 for name in ("pessimistic", "base", "optimistic")):
        eligible = False
        reasons.append("三档价值存在非正值，无法解释")
    if inputs.price is None:
        eligible = False
        reasons.append("缺现价")

    conclusion = "undetermined"
    if eligible:
        price = float(inputs.price)
        if price <= candidate_entry and price <= pessimistic:
            conclusion = "candidate_buy_point"
        elif price > optimistic:
            conclusion = "avoid"
        else:
            conclusion = "wait"

    return {
        "code": inputs.code,
        "name": inputs.name,
        "in_scope": inputs.in_scope,
        "in_bank_pool": inputs.in_bank_pool,
        "data_complete": inputs.data_complete,
        "price": inputs.price,
        "price_date": inputs.price_date,
        "bvps": inputs.bvps,
        "shares": inputs.shares,
        "roe": roe,
        "asset_quality_deduction": bool(deduction),
        "deduction": deduct,
        "growth": g,
        "risk_free": rf,
        "equity_risk_premium": erp,
        "bank_premium": premium_value,
        "cost_of_equity": k,
        "values": values,
        "multiples": multiples,
        "candidate_entry_price": candidate_entry,
        "binding_condition": binding,
        "threshold_price": threshold,
        "margin_of_safety": margin_value,
        "conclusion": conclusion,
        "reasons": reasons,
        "status": "complete" if eligible else ("insufficient_data" if not inputs.data_complete else "undetermined"),
    }


__all__ = [
    "BankValuationInputs",
    "BankValuationV2Error",
    "DEFAULT_POLICY",
    "load_policy",
    "value_bank",
]
