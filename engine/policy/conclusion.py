"""把政策试运行结果转成 CLI 的正式结论载荷。

与试运行共用 `engine.policy.trial.evaluate_company`：不另写一套估值逻辑（P3）。
结论映射：candidate_buy_point / wait / avoid → decision；其余 → undetermined。
"""

from __future__ import annotations

from typing import Any

from engine.contracts.enums import Decision, ValuationState
from engine.policy.equity import EquityPolicy

POLICY_CONCLUSION_VERSION = "equity-policy-conclusion/1"

_GRADE_MEANING = "A/B/C 衡量的是数据完整度与模型稳定性，不是公司质量。"

_VALUE_USAGE = {
    "pessimistic": "候选买点的第二道约束：现价还须不高于悲观情景价值",
    "base": "安全边际基准：候选买点要求现价 ≤ 基准价值 × (1 − 安全边际)",
    "optimistic": "回避线：现价高于乐观情景价值即判「回避」",
}


def _amount(value: float | None, unit: str = "CNY_per_share") -> dict[str, Any] | None:
    if value is None:
        return None
    return {"value": float(value), "unit": unit, "currency": "CNY"}


def decision_for(record: dict[str, Any]) -> str:
    conclusion = (record.get("trial_conclusion") or {}).get("conclusion")
    return {
        "candidate_buy_point": Decision.CANDIDATE.value,
        "wait": Decision.WAIT.value,
        "avoid": Decision.AVOID.value,
    }.get(conclusion, Decision.UNDETERMINED.value)


def valuation_state_for(decision: str) -> str:
    return {
        Decision.CANDIDATE.value: ValuationState.ATTRACTIVE.value,
        Decision.WAIT.value: ValuationState.NEUTRAL.value,
        Decision.AVOID.value: ValuationState.EXPENSIVE.value,
    }.get(decision, ValuationState.UNKNOWN.value)


def _binding(record: dict[str, Any]) -> dict[str, Any] | None:
    thresholds = (record.get("trial_conclusion") or {}).get("thresholds") or {}
    if not thresholds:
        return None
    return {
        "name": thresholds.get("binding_condition"),
        "label": thresholds.get("binding_condition_text"),
        "price": _amount(thresholds.get("binding_price")),
    }


def build_policy_conclusion(
    record: dict[str, Any],
    *,
    policy: EquityPolicy,
    analysis_date: str,
) -> dict[str, Any]:
    """构造 CLI 的 `policy_conclusion` 载荷（正式与无法判断都带）。"""
    grade_node = record.get("grade") or {}
    grade = grade_node.get("grade", "C")
    values = record.get("values") or {}
    price = record.get("price")
    thresholds = (record.get("trial_conclusion") or {}).get("thresholds") or {}
    entry = thresholds.get("candidate_entry_price")
    distance = None
    if price is not None and entry not in (None, 0):
        # 百分比数值：+104.2 表示现价仍比候选门槛高 104.2%
        distance = (price / entry - 1.0) * 100.0

    conclusion_name = (record.get("trial_conclusion") or {}).get("conclusion") or "undetermined"
    decision = decision_for(record)
    usage = {key: text for key, text in _VALUE_USAGE.items()}
    pessimistic = values.get("pessimistic")
    value_note = None
    if pessimistic is not None and pessimistic <= 0:
        pessimistic = None
        value_note = "悲观情景价值非正，负价值不可解释，按政策不展示"
    return {
        "payload_version": POLICY_CONCLUSION_VERSION,
        "analysis_date": analysis_date,
        "policy": {
            "policy_id": policy.policy_id,
            "policy_digest": policy.policy_digest,
            "status": policy.status,
            "approved_at": (policy.data.get("approval") or {}).get("approved_at"),
            "review_date": (policy.data.get("approval") or {}).get("review_date"),
        },
        "grade": {
            "grade": grade,
            "meaning": _GRADE_MEANING,
            "margin_of_safety": grade_node.get("margin_of_safety"),
            "unmet_conditions": list(grade_node.get("unmet_conditions") or []),
        },
        "values": {
            "pessimistic": _amount(pessimistic),
            "base": _amount(values.get("base")),
            "optimistic": _amount(values.get("optimistic")),
            "usage": usage,
            "note": value_note,
        },
        "price": _amount(price),
        "price_date": record.get("price_as_of"),
        "candidate_entry_price": _amount(entry),
        "distance_to_candidate_pct": distance,
        "binding_condition": _binding(record) if decision != Decision.UNDETERMINED.value else None,
        "conclusion": conclusion_name,
        "status": record.get("status"),
        "status_reason": record.get("status_reason"),
        "snapshot_id": record.get("snapshot_id"),
        "as_of": record.get("as_of"),
        "disclaimer": "本结果不构成投资建议，也不保证收益。",
    }




def record_from_result(result: Any, *, recorded_at: str):
    """从 AnalysisResult 构造账本记录；只用于 candidate/wait/avoid。"""
    from engine.policy.conclusion_ledger import ConclusionRecord

    block = (result.payload or {}).get("policy_conclusion") or {}
    if not block or block.get("conclusion") == "undetermined":
        raise ValueError("undetermined 不得写入结论账本")
    values = block.get("values") or {}
    return ConclusionRecord(
        instrument_id=str(result.instrument_id),
        code=str(((result.instrument or {}).get("code")) or ""),
        analysis_date=str(block.get("analysis_date")),
        policy_version=str((block.get("policy") or {}).get("policy_id")),
        policy_digest=str((block.get("policy") or {}).get("policy_digest")),
        snapshot_id=str(block.get("snapshot_id")),
        price=float((block.get("price") or {}).get("value")),
        price_date=str(block.get("price_date")),
        values={
            key: float((values.get(key) or {}).get("value"))
            for key in ("pessimistic", "base", "optimistic")
        },
        grade=str((block.get("grade") or {}).get("grade")),
        margin_of_safety=(block.get("grade") or {}).get("margin_of_safety"),
        conclusion=str(block.get("conclusion")),
        binding_condition=str(
            ((block.get("binding_condition") or {}).get("label"))
            or ((block.get("binding_condition") or {}).get("name"))
            or ""
        ),
        recorded_at=recorded_at,
    )

__all__ = [
    "POLICY_CONCLUSION_VERSION",
    "build_policy_conclusion",
    "decision_for",
    "record_from_result",
    "valuation_state_for",
]
