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
from engine.contracts.payloads import (
    INDEX_POINT_UNIT,
    MONEY_UNITS,
    PAYLOAD_OPTIONAL_FIELDS,
    PAYLOAD_SCHEMAS,
)
from engine.data.numeric import is_finite_number
from engine.models.registry import MODELS_BY_ID
from engine.version import RESULT_SCHEMA_VERSION

_INSTRUMENT_ID_RE = re.compile(r"^(EQUITY|INDEX|ETF):[0-9A-Z]+(\.[0-9A-Z]+)?$")

_BLOCKED_ANALYSIS_STATUSES = {
    AnalysisStatus.INSUFFICIENT_DATA,
    AnalysisStatus.UNSUPPORTED,
    AnalysisStatus.NEEDS_CLARIFICATION,
    AnalysisStatus.ENGINE_ERROR,
}

_ALLOWED_MODEL_RESULT_TYPES = {
    "equity_value",
    "index_signal",
    "etf_execution",
    "research_signal",
    # 阶段二：研究估值结果（model_status=research，decision=undetermined）
    "research_valuation",
    # 阶段三：指数估值画像（描述性统计，不是交易信号）
    "index_profile",
}

# 这些字段属于核心契约，缺失或为 null 一律视为违规（不允许「省略即通过」）
_REQUIRED_KEYS = (
    "schema_version",
    "intent",
    "analysis_status",
    "valuation_state",
    "decision",
    "reason_codes",
    "payload",
    "model_plan",
    "assumptions",
    "model_results",
    "conditions",
    "invalidation",
    "evidence_refs",
    "missing_data",
    "warnings",
    "code_version",
    "policy_version",
    "model_version",
)
_NOT_NULL_KEYS = ("analysis_status", "valuation_state", "decision", "intent", "payload")

# 只有 validated + complete 才允许出现的「正式价格判断」
_FORMAL_DECISIONS = {Decision.CANDIDATE, Decision.WAIT, Decision.AVOID}

# 这些载荷一旦非空，就等于给出了定价/信号结论
# 只限制「模型输出」，不限制「已取得的数据事实」：
# level / price / nav 是观测值，valuation / premium_discount / signal 才是模型结论。
_PRICED_PAYLOAD_FIELDS = ("valuation", "premium_discount", "signal")


class ContractViolation(RuntimeError):
    """结果不满足契约。禁止吞掉：这是硬性出口。"""

    def __init__(self, problems: Iterable[str]) -> None:
        self.problems = list(problems)
        super().__init__("结果契约校验失败:\n- " + "\n- ".join(self.problems))


def validate_result(result: Any) -> list[str]:
    """返回违规项列表；空列表表示通过。接受 AnalysisResult 或 dict。"""
    data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    problems: list[str] = []

    # --- 必填字段 -------------------------------------------------------
    missing_keys = [key for key in _REQUIRED_KEYS if key not in data]
    if missing_keys:
        problems.append(f"缺少必填字段: {missing_keys}")
    null_keys = [key for key in _NOT_NULL_KEYS if key in data and data.get(key) is None]
    if null_keys:
        problems.append(f"必填字段不能为 null: {null_keys}")

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
    # 阻断性原因码对所有正式价格判断一视同仁：candidate / wait / avoid 都不允许
    if decision in _FORMAL_DECISIONS and guard_codes:
        problems.append(
            f"存在阻断性原因码时不得输出 {decision.value} 这类正式价格判断: {guard_codes}"
        )
    if decision in _FORMAL_DECISIONS:
        if model_status != ModelStatus.VALIDATED:
            problems.append(
                f"decision={decision.value} 属于正式价格判断，要求 model_status=validated"
                f"（当前 {model_status.value if model_status else None}）"
            )
        if analysis_status != AnalysisStatus.COMPLETE:
            problems.append(
                f"decision={decision.value} 属于正式价格判断，要求 analysis_status=complete"
                f"（当前 {analysis_status.value if analysis_status else None}）"
            )
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
        optional = PAYLOAD_OPTIONAL_FIELDS.get(payload_type, set())
        required = PAYLOAD_SCHEMAS[payload_type] - optional
        missing = required - set(payload)
        if missing:
            problems.append(f"payload({payload_type}) 缺少字段: {sorted(missing)}")
        extra = set(payload) - PAYLOAD_SCHEMAS[payload_type]
        if extra:
            problems.append(f"payload({payload_type}) 含未定义字段: {sorted(extra)}")
        problems.extend(_check_research_valuation_payload(payload, data, analysis_status, model_status))
        problems.extend(_check_index_profile_payload(payload, data, analysis_status, model_status))
        problems.extend(_check_product_check_payload(payload, data, model_status))
        if analysis_status != AnalysisStatus.COMPLETE or model_status != ModelStatus.VALIDATED:
            priced = [
                field
                for field in _PRICED_PAYLOAD_FIELDS
                if payload.get(field) not in (None, {}, [], "")
            ]
            if priced:
                problems.append(
                    f"分析未完成或模型未 validated 时，payload 不得携带定价/信号载荷: {priced}"
                )
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
        if item["result_type"] == "research_signal":
            problems.extend(
                _check_research_signal(item, data, analysis_status, model_status, decision)
            )
        elif item["result_type"] == "research_valuation":
            problems.extend(
                _check_research_valuation(item, data, analysis_status, model_status, decision)
            )
        elif item["result_type"] == "index_profile":
            problems.extend(
                _check_index_profile(item, data, analysis_status, model_status, decision)
            )
        elif analysis_status != AnalysisStatus.COMPLETE or model_status != ModelStatus.VALIDATED:
            problems.append(
                f"model_results 含 {item['result_type']}，但分析未完成或模型未 validated"
            )
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


# --- 研究结果的来源约束 ------------------------------------------------------
def _check_research_signal(item, data, analysis_status, model_status, decision) -> list[str]:
    """研究结果必须能追到「真实登记过、且确实被执行」的模型。

    仅凭一个非空字符串 model_id 不算有来源：
    - 模型必须在模型登记表中；
    - 版本必须与登记版本一致；
    - 必须与 model_plan.primary_model 一致（说明确实是这次分析选用的模型）；
    - 整体状态必须是 research，且分析确实执行过（complete/partial）；
    - 决策必须仍是 undetermined。
    """
    problems: list[str] = []
    model_id = item.get("model_id")
    plan = data.get("model_plan") or {}

    if not model_id:
        problems.append("research_signal 必须注明 model_id（真实模型来源）")
        return problems

    model = MODELS_BY_ID.get(model_id)
    if model is None:
        problems.append(f"research_signal 引用了未登记的模型: {model_id!r}")
    else:
        if model.status != ModelStatus.RESEARCH.value:
            problems.append(
                f"模型 {model_id} 的登记状态为 {model.status}，不能作为 research_signal 来源"
            )
        if item.get("model_version") != model.version:
            problems.append(
                f"research_signal 的 model_version={item.get('model_version')!r} "
                f"与登记版本 {model.version!r} 不一致"
            )
        if plan.get("primary_model") != model_id:
            problems.append(
                "research_signal 必须与 model_plan.primary_model 一致"
                "（说明该模型确实是本次分析选用的模型）"
            )

    if item.get("model_status") != ModelStatus.RESEARCH.value:
        problems.append("research_signal 必须标记 model_status=research")
    if model_status != ModelStatus.RESEARCH:
        problems.append(
            "携带 research_signal 的结果，整体 model_status 必须为 research"
            f"（当前 {model_status.value if model_status else None}）"
        )
    if analysis_status not in (AnalysisStatus.COMPLETE, AnalysisStatus.PARTIAL):
        problems.append(
            "研究结果只能在 complete/partial 的分析中出现"
            "（未建模或未执行的结果不能夹带模型计算价格）"
        )
    if decision != Decision.UNDETERMINED:
        problems.append("携带研究结果时 decision 必须为 undetermined")
    return problems


# --- 研究估值结果的来源约束 ---------------------------------------------------
_RESEARCH_VALUATION_FORBIDDEN_STATES = {
    ValuationState.ATTRACTIVE,
    ValuationState.NEUTRAL,
    ValuationState.EXPENSIVE,
}


def _check_research_valuation(item, data, analysis_status, model_status, decision) -> list[str]:
    """研究估值必须能追到真实登记、确实实现的模型，且不得冒充正式价格结论。"""
    problems: list[str] = []
    model_id = item.get("model_id")
    plan = data.get("model_plan") or {}

    if not model_id:
        problems.append("research_valuation 必须注明 model_id")
        return problems
    model = MODELS_BY_ID.get(model_id)
    if model is None:
        problems.append(f"research_valuation 引用了未登记的模型: {model_id!r}")
    else:
        if not model.implemented:
            problems.append(f"模型 {model_id} 未实现，不能输出研究估值")
        if item.get("model_version") != model.version:
            problems.append(
                f"research_valuation 的 model_version={item.get('model_version')!r} "
                f"与登记版本 {model.version!r} 不一致"
            )
        if plan.get("primary_model") != model_id:
            problems.append("research_valuation 必须与 model_plan.primary_model 一致")
    if item.get("model_status") != ModelStatus.RESEARCH.value:
        problems.append("research_valuation 必须标记 model_status=research")
    if model_status != ModelStatus.RESEARCH:
        problems.append(
            "携带 research_valuation 的结果，整体 model_status 必须为 research"
            f"（当前 {model_status.value if model_status else None}）"
        )
    if analysis_status not in (AnalysisStatus.COMPLETE, AnalysisStatus.PARTIAL):
        problems.append("研究估值只能在 complete/partial 的分析中出现")
    if decision != Decision.UNDETERMINED:
        problems.append("携带研究估值时 decision 必须为 undetermined")
    return problems


def _check_research_valuation_payload(payload, data, analysis_status, model_status) -> list[str]:
    """载荷里的研究估值块：必须与模型结果一致，且不得混进正式定价字段。"""
    problems: list[str] = []
    research = payload.get("research_valuation")
    if payload.get("payload_type") != "equity":
        if research not in (None, {}, [], ""):
            problems.append("research_valuation 只能出现在个股载荷中")
        return problems

    results = [
        item
        for item in (data.get("model_results") or [])
        if isinstance(item, dict) and item.get("result_type") == "research_valuation"
    ]
    if research in (None, {}):
        if results:
            problems.append("model_results 声明了 research_valuation，但载荷中没有对应内容")
        return problems

    if not isinstance(research, dict):
        problems.append("research_valuation 必须是对象或 null")
        return problems
    if not research.get("payload_version"):
        problems.append("research_valuation 必须带 payload_version")
    if not results:
        problems.append("载荷含 research_valuation，但 model_results 没有对应记录")
    else:
        if results[0].get("model_id") != research.get("model_id"):
            problems.append("research_valuation 的 model_id 与 model_results 不一致")
        if results[0].get("model_version") != research.get("model_version"):
            problems.append("research_valuation 的 model_version 与 model_results 不一致")
    if model_status != ModelStatus.RESEARCH.value:
        problems.append("载荷含研究估值时，整体 model_status 必须为 research")
    valuation_state = data.get("valuation_state")
    if valuation_state in {s.value for s in _RESEARCH_VALUATION_FORBIDDEN_STATES}:
        problems.append(
            "研究估值不能给出 attractive/neutral/expensive 的价格位置判断"
            f"（当前 {valuation_state!r}）"
        )
    if payload.get("valuation") not in (None, {}, [], ""):
        problems.append("研究估值不得写入正式 valuation 字段（该字段保留给 validated 模型）")
    return problems


def _check_index_profile(item, data, analysis_status, model_status, decision) -> list[str]:
    """指数画像是描述性统计：必须可溯源到已实现的登记模型，且不得变成交易信号。"""
    problems: list[str] = []
    model_id = item.get("model_id")
    plan = data.get("model_plan") or {}
    if not model_id:
        problems.append("index_profile 必须注明 model_id")
        return problems
    model = MODELS_BY_ID.get(model_id)
    if model is None:
        problems.append(f"index_profile 引用了未登记的模型: {model_id!r}")
    else:
        if not model.implemented:
            problems.append(f"模型 {model_id} 未实现，不能输出指数画像")
        if item.get("model_version") != model.version:
            problems.append(
                f"index_profile 的 model_version={item.get('model_version')!r} "
                f"与登记版本 {model.version!r} 不一致"
            )
        if plan.get("primary_model") != model_id:
            problems.append("index_profile 必须与 model_plan.primary_model 一致")
    if item.get("model_status") != ModelStatus.RESEARCH.value:
        problems.append("index_profile 必须标记 model_status=research")
    if model_status != ModelStatus.RESEARCH:
        problems.append("携带 index_profile 的结果，整体 model_status 必须为 research")
    if analysis_status not in (AnalysisStatus.COMPLETE, AnalysisStatus.PARTIAL):
        problems.append("指数画像只能在 complete/partial 的分析中出现")
    if decision != Decision.UNDETERMINED:
        problems.append("携带指数画像时 decision 必须为 undetermined")
    if item.get("is_trading_signal") is not False:
        problems.append("index_profile 必须显式声明 is_trading_signal=false")
    return problems


def _check_index_profile_payload(payload, data, analysis_status, model_status) -> list[str]:
    problems: list[str] = []
    profile = payload.get("valuation_profile")
    if payload.get("payload_type") != "index":
        if profile not in (None, {}, [], ""):
            problems.append("valuation_profile 只能出现在指数载荷中")
        return problems
    if profile in (None, {}):
        return problems
    if not isinstance(profile, dict):
        problems.append("valuation_profile 必须是对象或 null")
        return problems
    if not profile.get("payload_version"):
        problems.append("valuation_profile 必须带 payload_version")
    if profile.get("is_trading_signal") is not False:
        problems.append("valuation_profile 必须显式声明 is_trading_signal=false")
    if not profile.get("decision_note"):
        problems.append("valuation_profile 必须说明它能回答什么问题")
    results = [
        item
        for item in (data.get("model_results") or [])
        if isinstance(item, dict) and item.get("result_type") == "index_profile"
    ]
    # 只有确实算出了指标才要求模型结果；覆盖不足时画像块只用于说明缺口
    if profile.get("metrics") and not results:
        problems.append("valuation_profile 含已计算指标，但 model_results 没有对应记录")
    if profile.get("metrics") and model_status != ModelStatus.RESEARCH.value:
        problems.append("载荷含已计算的指数画像时，整体 model_status 必须为 research")
    if data.get("valuation_state") in ("attractive", "neutral", "expensive"):
        problems.append("指数画像不能给出 attractive/neutral/expensive 的价格位置判断")
    if payload.get("valuation_facts") and payload["valuation_facts"].get("signal"):
        problems.append("指数画像不得写入 signal 字段")
    return problems


def _check_product_check_payload(payload, data, model_status) -> list[str]:
    problems: list[str] = []
    check = payload.get("product_check")
    if payload.get("payload_type") != "etf":
        if check not in (None, {}, [], ""):
            problems.append("product_check 只能出现在 ETF 载荷中")
        return problems
    if check in (None, {}):
        return problems
    if not isinstance(check, dict):
        problems.append("product_check 必须是对象或 null")
        return problems
    if not check.get("payload_version"):
        problems.append("product_check 必须带 payload_version")
    if not check.get("usage_note"):
        problems.append("product_check 必须说明「资料齐备不等于满足买入条件」")
    if payload.get("premium_discount") not in (None, {}, [], ""):
        problems.append("同日折溢价只能写在 product_check，不得占用正式 premium_discount 字段")
    if model_status not in (ModelStatus.RESEARCH.value, None):
        problems.append("ETF 产品资料检查不得声称模型已 validated")
    return problems


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
