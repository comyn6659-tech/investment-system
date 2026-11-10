"""决策护栏。

所有结论都必须经过这里，禁止模型或报告层直接写 candidate。
这里不计算任何估值，只做「允许说什么」的判定。
"""

from __future__ import annotations

from engine.contracts import reason_codes as rc
from engine.contracts.enums import AnalysisStatus, Decision, ModelStatus, ValuationState
from engine.contracts.result import AnalysisResult

_BLOCKING_STATUSES = {
    AnalysisStatus.INSUFFICIENT_DATA,
    AnalysisStatus.UNSUPPORTED,
    AnalysisStatus.NEEDS_CLARIFICATION,
    AnalysisStatus.ENGINE_ERROR,
}


def apply_guards(result: AnalysisResult) -> AnalysisResult:
    """按设计书 §4 的规则强制降级，不允许绕过。"""
    codes = list(result.reason_codes)
    decision = Decision(result.decision)
    status = AnalysisStatus(result.analysis_status)
    model_status = ModelStatus(result.model_status) if result.model_status else None
    valuation = ValuationState(result.valuation_state)

    def downgrade(new_decision: Decision) -> None:
        nonlocal decision
        decision = new_decision

    # 1) 未完成/未实现/资料不足/需要澄清/引擎错误 => undetermined
    if status in _BLOCKING_STATUSES:
        downgrade(Decision.UNDETERMINED)

    # 2) research 模型 => 只能有研究信号
    if model_status == ModelStatus.RESEARCH:
        if rc.RESEARCH_SIGNAL_ONLY not in codes:
            codes.append(rc.RESEARCH_SIGNAL_ONLY)
        downgrade(Decision.UNDETERMINED)

    # 3) deprecated 模型 => 不得输出结论
    if model_status == ModelStatus.DEPRECATED:
        if rc.MODEL_DEPRECATED not in codes:
            codes.append(rc.MODEL_DEPRECATED)
        downgrade(Decision.UNDETERMINED)

    # 4) 任何阻断性原因码 => undetermined
    if rc.blocking_codes(codes):
        downgrade(Decision.UNDETERMINED)

    # 5) 价格位置未知/不适用 => 不得给 wait/avoid 这类价格判断
    if valuation in (ValuationState.UNKNOWN, ValuationState.NOT_APPLICABLE):
        if decision in (Decision.CANDIDATE, Decision.WAIT, Decision.AVOID):
            downgrade(Decision.UNDETERMINED)

    result.decision = decision.value
    result.reason_codes = _dedupe(codes)
    return result


def assert_not_fabricated(result: AnalysisResult) -> None:
    """额外自检：unsupported 结果不得带任何定价结果。"""
    if AnalysisStatus(result.analysis_status) == AnalysisStatus.UNSUPPORTED:
        priced = [r for r in result.model_results if r.get("result_type") == "equity_value"]
        if priced:
            raise AssertionError("unsupported 结果不允许携带 equity_value 定价结果")


def _dedupe(codes: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            out.append(code)
    return out
