"""出口校验：所有出口（JSON / 文本）都必须先过这里。

规则直接来自设计书 §4 与 §12：
- 未完成、未实现、资料不足、研究状态都不能产生正式 candidate；
- 研究结果可以在结果里出现，但 decision 必须是 undetermined；
- 金额必须带币种，指数点位不得标成人民币；
- NaN / Infinity 不得出现在任何数值位置。
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

from engine.contracts import reason_codes as rc
from engine.contracts.enums import (
    AnalysisStatus,
    AssetClass,
    Decision,
    ModelStatus,
    ValuationState,
)
from engine.contracts.payloads import INDEX_POINT_UNIT, MONEY_UNITS, PAYLOAD_SCHEMAS
from engine.data.numeric import is_finite_number
from engine.version import RESULT_SCHEMA_VERSION

_INSTRUMENT_ID_RE = re.compile(r"^(EQUITY|INDEX|ETF):[0-9A-Z]+(\.[0-9A-Z]+)?$")

_BLOCKED_ANALYSIS_STATUSES = {
    AnalysisStatus.INSUFFICIENT_DATA,
    AnalysisStatus.UNSUPPORTED,
    AnalysisStatus.NEEDS_CLARIFICATION,
    AnalysisStatus.ENGINE_ERROR,
}

_ALLOWED_MODEL_RESULT_TYPES = {"equity_value", "index_signal", "etf_execution", "research_signal"}


class ContractViolation(RuntimeError):
    """结果不满足契约。禁止吞掉：这是硬性出口。"""

    def __init__(self, problems: Iterable[str]) -> None:
        self.problems = list(problems)
        super().__init__("结果契约校验失败:\n- " + "\n- ".join(self.problems))


def validate_result(result: Any) -> list[str]:
    """返回违规项列表；空列表表示通过。接受 AnalysisResult 或 dict。"""
    data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    problems: list[str] = []

    # --- 基础字段 -------------------------------------------------------
    if data.get("schema_version") != RESULT_SCHEMA_VERSION:
        problems.append(
            f"schema_version 应为 {RESULT_SCHEMA_VERSION}，实际 {data.get('schema_version')!r}"
        )
    if not data.get("intent"):
        problems.append("intent 不能为空")

    analysis_status = _enum_or_none(AnalysisStatus, data.get("analysis_status"), "analysis_status", problems)
    model_status = _enum_or_none(ModelStatus, data.get("model_status"), "model_status", problems)
    valuation_state = _enum_or_none(
        ValuationState, data.get("valuation_state"), "valuation_state", problems
    )
    decision = _enum_or_none(Decision, data.get("decision"), "decision", problems)

    codes = data.get("reason_codes") or []
    if not isinstance(codes, list):
        problems.append("reason_codes 必须是字符串列表")
        codes = []
    unknown = [c for c in codes if not rc.is_registered(c)]
    if unknown:
        problems.append(f"未登记的原因码: {unknown}")

    # --- 决策一致性 -----------------------------------------------------
    guard_codes = rc.blocking_codes([c for c in codes if rc.is_registered(c)])

    if analysis_status in _BLOCKED_ANALYSIS_STATUSES and decision != Decision.UNDETERMINED:
        problems.append(
            f"analysis_status={analysis_status.value} 时 decision 必须为 undetermined，实际 {decision}"
        )
    if model_status == ModelStatus.RESEARCH and decision == Decision.CANDIDATE:
        problems.append("model_status=research 时不得输出 candidate（研究结果不能进入正式买点）")
    if model_status == ModelStatus.DEPRECATED and decision == Decision.CANDIDATE:
        problems.append("model_status=deprecated 时不得输出 candidate")
    if decision == Decision.CANDIDATE and guard_codes:
        problems.append(f"存在阻断性原因码时不得输出 candidate: {guard_codes}")
    if decision == Decision.CANDIDATE:
        if analysis_status != AnalysisStatus.COMPLETE:
            problems.append("candidate 要求 analysis_status=complete")
        if model_status != ModelStatus.VALIDATED:
            problems.append("candidate 要求 model_status=validated")
        if valuation_state != ValuationState.ATTRACTIVE:
            problems.append("candidate 要求 valuation_state=attractive")
    if valuation_state in (ValuationState.UNKNOWN, ValuationState.NOT_APPLICABLE) and decision in (
        Decision.CANDIDATE,
        Decision.WAIT,
        Decision.AVOID,
    ):
        problems.append(
            f"valuation_state={valuation_state.value} 时不能给出 {decision.value} 这类价格结论"
        )

    # --- 原因码与澄清 ---------------------------------------------------
    if analysis_status is not None and analysis_status != AnalysisStatus.COMPLETE and not codes:
        problems.append(f"analysis_status={analysis_status.value} 时 reason_codes 不能为空")
    if analysis_status == AnalysisStatus.NEEDS_CLARIFICATION and not data.get("clarification"):
        problems.append("needs_clarification 必须给出 clarification 文本")
    if analysis_status == AnalysisStatus.ENGINE_ERROR and not data.get("warnings"):
        problems.append("engine_error 必须在 warnings 中说明失败环节")

    # --- 快照与时点 -----------------------------------------------------
    instrument = data.get("instrument")
    if instrument:
        iid = instrument.get("instrument_id", "")
        if not _INSTRUMENT_ID_RE.match(iid):
            problems.append(f"instrument_id 格式非法: {iid!r}")
        scope = instrument.get("scope") or {}
        if scope.get("status") not in {"in_scope", "out_of_scope", "unverified"}:
            problems.append(f"instrument.scope.status 非法: {scope.get('status')!r}")
    if analysis_status in (AnalysisStatus.COMPLETE, AnalysisStatus.PARTIAL):
        if not data.get("snapshot_id"):
            problems.append("complete/partial 结果必须带 snapshot_id")
        if not data.get("as_of"):
            problems.append("complete/partial 结果必须带 as_of")

    # --- 载荷一致性 -----------------------------------------------------
    payload = data.get("payload") or {}
    payload_type = payload.get("payload_type")
    if payload_type not in PAYLOAD_SCHEMAS:
        problems.append(f"未知的 payload_type: {payload_type!r}")
    else:
        missing = PAYLOAD_SCHEMAS[payload_type] - set(payload)
        if missing:
            problems.append(f"payload({payload_type}) 缺少字段: {sorted(missing)}")
        if instrument:
            expected = {
                AssetClass.EQUITY: "equity",
                AssetClass.INDEX: "index",
                AssetClass.ETF: "etf",
            }.get(AssetClass(instrument.get("asset_class")), "none")
            if payload_type != expected:
                problems.append(
                    f"payload_type={payload_type} 与 asset_class={instrument.get('asset_class')} 不匹配"
                )

    # --- 模型结果类型 ---------------------------------------------------
    for item in data.get("model_results") or []:
        if not isinstance(item, dict) or "result_type" not in item:
            problems.append("model_results 的每一项都必须是带 result_type 的对象")
            continue
        if item["result_type"] not in _ALLOWED_MODEL_RESULT_TYPES:
            problems.append(f"未知的 model_results 类型: {item['result_type']!r}")
        if decision == Decision.CANDIDATE and item["result_type"] == "research_signal":
            problems.append("研究信号不得与 candidate 同时出现")

    # --- 数值与单位 -----------------------------------------------------
    problems.extend(_check_numbers(data, path="result"))

    return problems


def enforce(result: Any) -> None:
    """校验不通过就抛异常，调用方不得忽略。"""
    problems = validate_result(result)
    if problems:
        raise ContractViolation(problems)


# --- 内部工具 ---------------------------------------------------------------
def _enum_or_none(enum_cls, value, field_name, problems):
    if value is None:
        return None
    try:
        return enum_cls(value)
    except ValueError:
        allowed = [e.value for e in enum_cls]
        problems.append(f"{field_name}={value!r} 不在允许取值 {allowed} 内")
        return None


def _check_numbers(node: Any, *, path: str) -> list[str]:
    problems: list[str] = []
    if isinstance(node, float):
        if not math.isfinite(node):
            problems.append(f"{path} 出现非有限数值: {node!r}")
        return problems
    if isinstance(node, dict):
        unit = node.get("unit")
        if unit is not None and "value" in node:
            value = node["value"]
            currency = node.get("currency")
            if value is not None and not is_finite_number(value):
                problems.append(f"{path} 的数值非法: {value!r}")
            if unit == INDEX_POINT_UNIT:
                if currency not in (None, ""):
                    problems.append(f"{path} 是指数点位，不得携带币种（{currency!r}）")
            elif unit in MONEY_UNITS:
                if not isinstance(currency, str) or not currency:
                    problems.append(f"{path} 是金额（{unit}），必须携带币种")
        for key, value in node.items():
            problems.extend(_check_numbers(value, path=f"{path}.{key}"))
        return problems
    if isinstance(node, (list, tuple)):
        for idx, value in enumerate(node):
            problems.extend(_check_numbers(value, path=f"{path}[{idx}]"))
        return problems
    return problems
