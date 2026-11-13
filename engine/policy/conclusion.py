"""把政策试运行结果转成 CLI 的正式结论载荷。

与试运行共用 `engine.policy.trial.evaluate_company`：不另写一套估值逻辑（P3）。
结论映射：candidate_buy_point / wait / avoid → decision；其余 → undetermined。
"""

from __future__ import annotations

from typing import Any

from datetime import datetime
from zoneinfo import ZoneInfo

from engine.contracts import reason_codes as rc
from engine.contracts.enums import Decision, ValuationState
from engine.evidence.applicability import FAMILY_LABELS
from engine.policy.conclusion_ledger import (
    FORMAL_CONCLUSIONS,
    ConclusionLedger,
    ConclusionRecord,
    latest_for_instrument,
)
from engine.policy.equity import EquityPolicy

TZ = ZoneInfo("Asia/Singapore")

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


# --- 第十一批收尾 T2：改判为无法判断 ---------------------------------------
def _reversal_codes(record: dict[str, Any]) -> list[str]:
    codes = [str(code) for code in (record.get("reason_codes") or []) if code]
    route = record.get("route") or {}
    if route.get("status") == "family_not_implemented":
        codes.append(rc.MODEL_FAMILY_NOT_IMPLEMENTED)
    if route.get("family") == "cyclical":
        codes.append(rc.CYCLICAL_INDUSTRY_LIST_MATCH)
    if not codes:
        status = record.get("status")
        if status == "partial":
            codes.append(rc.POLICY_STATUS_PARTIAL)
        elif status == "insufficient_data":
            codes.append(rc.MISSING_BUSINESS_EVIDENCE)
        else:
            codes.append(rc.POLICY_GRADE_C)
    return list(dict.fromkeys(codes))


def _reversal_reason(record: dict[str, Any]) -> str:
    route = record.get("route") or {}
    if route.get("status") == "family_not_implemented":
        family = FAMILY_LABELS.get(route.get("family"), route.get("family") or "该企业族")
        category = route.get("applicable_model_category") or "对应专用口径"
        if route.get("family") == "cyclical":
            industry = str(record.get("industry") or "—")
            match = next(
                (
                    (reason.get("observed") or {}).get("cyclical_list")
                    for reason in (route.get("reasons") or [])
                    if (reason.get("observed") or {}).get("cyclical_list")
                ),
                None,
            ) or {}
            group = match.get("group") or "周期行业"
            return (
                f"明显周期企业：行业「{industry}」命中周期行业清单（{group}）；"
                f"{category}路线首版未实现，按已批准政策改判为无法判断。"
            )
        return f"{family}路线首版未实现（{category}）；按已批准政策改判为无法判断。"
    reason = str(record.get("status_reason") or "").strip()
    if reason:
        return reason
    grade = (record.get("grade") or {}).get("grade")
    return f"本次评估等级 {grade}，按政策改判为无法判断。" if grade else "本次评估无法给出正式结论。"


def append_evaluation(
    ledger: ConclusionLedger,
    record: dict[str, Any],
    *,
    policy: EquityPolicy,
    analysis_date: str,
) -> dict[str, Any]:
    """按评估结果追加正式结论或改判记录；返回写入结果（不覆盖历史）。

    - 正式结论（候选买点/等待/回避）满足条件时追加正式记录；
    - 本次为无法判断，且账本中该标的最新一条是正式结论时，追加一条改判记录；
    - 从未有过正式结论（或最新已是改判）的标的，不写入。
    """
    conclusion = str((record.get("trial_conclusion") or {}).get("conclusion") or "undetermined")
    values = record.get("values") or {}
    grade = (record.get("grade") or {}).get("grade")
    status = record.get("status")
    if (
        conclusion in FORMAL_CONCLUSIONS
        and status == "complete"
        and grade in ("A", "B")
        and record.get("price") is not None
        and values
    ):
        item = ConclusionRecord(
            instrument_id=record["instrument_id"],
            code=str(record["code"]),
            analysis_date=analysis_date,
            policy_version=policy.policy_id,
            policy_digest=policy.policy_digest,
            snapshot_id=record["snapshot_id"],
            price=float(record["price"]),
            price_date=str(record.get("price_as_of") or ""),
            values={key: float(values[key]) for key in ("pessimistic", "base", "optimistic")},
            grade=str(grade),
            margin_of_safety=(record.get("grade") or {}).get("margin_of_safety"),
            conclusion=conclusion,
            binding_condition=str(
                ((record.get("trial_conclusion") or {}).get("thresholds") or {}).get(
                    "binding_condition_text"
                )
                or ""
            ),
            recorded_at=datetime.now(TZ).isoformat(timespec="seconds"),
        )
        outcome = ledger.append([item])
        return {
            "type": "formal",
            "status": "added" if outcome.get("added") else "skipped",
            "added": outcome.get("added") or [],
            "skipped": outcome.get("skipped") or [],
            "record_id": item.record_id,
        }

    if conclusion != "undetermined":
        return {"type": "none", "reason": "非正式结论且非无法判断"}

    latest = latest_for_instrument(ledger.load(), str(record["instrument_id"]))
    if latest is None:
        return {"type": "none", "reason": "从未有过正式结论"}
    if latest.get("conclusion") not in FORMAL_CONCLUSIONS:
        return {"type": "none", "reason": "账本最新记录不是正式结论"}
    if latest.get("record_type") == "reversal":
        return {"type": "none", "reason": "账本最新记录已是改判"}

    reversal = ConclusionRecord(
        instrument_id=record["instrument_id"],
        code=str(record["code"]),
        analysis_date=analysis_date,
        policy_version=policy.policy_id,
        policy_digest=policy.policy_digest,
        snapshot_id=record["snapshot_id"],
        price=None,
        price_date="",
        values={},
        grade="",
        margin_of_safety=None,
        conclusion="undetermined",
        binding_condition="",
        recorded_at=datetime.now(TZ).isoformat(timespec="seconds"),
        record_type="reversal",
        reason_codes=tuple(_reversal_codes(record)),
        reason=_reversal_reason(record),
        reversal_of=str(latest["record_id"]),
    )
    outcome = ledger.append([reversal])
    return {
        "type": "reversal",
        "status": "added" if outcome.get("added") else "skipped",
        "added": outcome.get("added") or [],
        "skipped": outcome.get("skipped") or [],
        "record_id": reversal.record_id,
        "reversal_of": reversal.reversal_of,
    }
