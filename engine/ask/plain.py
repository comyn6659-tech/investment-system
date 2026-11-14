"""对话入口的「大白话」映射：默认卡片不出现内部术语，术语与原始数字留在 --detail。"""

from __future__ import annotations

from typing import Any

# 默认卡片禁止出现的内部术语（W4 扫描）
BANNED_TERMS = (
    "FCFF",
    "主模型",
    "机制默认",
    "终值连续性护栏",
    "by_default",
    "DCF",
    "WACC",
    "EBIT",
    "EBITDA",
    "approximation_impact",
    "terminal_continuity_guardrail",
    "cross_check_divergence",
    "route_not_evaluable",
)

FAMILY_PLAIN = {
    "non_financial": "非周期",
    "cyclical": "明显周期",
    "bank": "非周期（银行）",
    "insurance": "非周期（保险）",
    "broker": "非周期（证券）",
    "holding": "非周期（控股平台）",
    "distressed": "非周期（困境）",
}

REASON_PLAIN = {
    "TERMINAL_GUARDRAIL_EXPLICIT_CASH": (
        "未来五年预计自由现金流为负，价值几乎全靠五年以后的假设，系统不给结论。"
    ),
    "TERMINAL_GUARDRAIL_TERMINAL_SHARE": (
        "价值主要来自五年以后的假设，长期假设稍有变化结论就会变，系统不给结论。"
    ),
    "TERMINAL_GUARDRAIL_BY_DEFAULT": (
        "这家公司不是按通用方法选中的，系统只能用默认参数试算，不能作为结论。"
    ),
    "PESSIMISTIC_VALUE_NONPOSITIVE": (
        "按最悲观的经营假设算出的每股价值为负，负价值无法解释，系统不给结论。"
    ),
    "BRIDGE_RECONCILIATION_GAP": (
        "按利润推算的自由现金流与公司实际经营现金流差额较大，存在未解释项，系统只给部分结果。"
    ),
    "CROSS_CHECK_DIVERGENCE": "两种算法算出的价值差得较多，需要解释差异。",
    "BASE_YEAR_ANOMALY": (
        "最近一年的营业收入或利润同比变化过大，口径可能断裂，系统不给结论。"
    ),
    "LEVERAGE_GUARDRAIL_EXCEEDED": "有息负债占比较高，超过政策护栏，系统不给结论。",
    "POLICY_GRADE_C": "数据完整度或模型稳定性只到 C 级，系统只展示研究数字，不给正式结论。",
    "POLICY_STATUS_PARTIAL": "部分数据没有对上（例如现金流勾稽或护栏），本次只算部分完成。",
    "MODEL_FAMILY_NOT_IMPLEMENTED": "这类公司需要专门的估值方法，本版还没有实现，系统不给结论。",
    "CYCLICAL_INDUSTRY_LIST_MATCH": "行业属于明显周期行业，本版还没有实现周期位置判断，系统不给结论。",
    "MISSING_BUSINESS_EVIDENCE": "缺少判断所需的业务资料。",
    "MISSING_BANK_RISK_EVIDENCE": "银行的资本充足、不良率或拨备资料不足。",
    "STALE_PRICE": "价格数据太旧，不能用来给结论。",
    "STALE_FINANCIALS": "财务数据太旧，不能用来给结论。",
    "MISSING_POLICY_FIELD": "缺少政策计算需要的字段。",
    "MISSING_MODEL_INPUT": "模型需要的数据缺失。",
    "TERMINAL_VALUE_NOT_APPLIED": "本次没有使用五年后的价值，结果只来自明确预测期。",
    "GROWTH_NOT_FUNDED_BY_RETENTION": "长期增长率超过公司留存利润能支撑的速度。",
    "EXCESS_DISTRIBUTION": "分红超过当年利润，需要用存量现金或融资支撑。",
    "SHARE_COUNT_DIVERGENCE": "当前股本与报告期加权股数差异较大，需要说明稀释影响。",
    "PRICE_UNAVAILABLE_FOR_COMPARISON": "缺少可核实的收盘价，无法与现价比较。",
    "DIVIDEND_FISCAL_YEAR_MISSING": "部分分红记录缺所属财年，未并入合计。",
    "SPECIAL_DIVIDEND_SEPARATED": "特别分红单独列出，未当成可持续分红。",
    "MODEL_CROSS_CHECK_ONLY": "这个模型只作交叉检查，不用来给结论。",
    "MODEL_RESEARCH_VALUATION": "研究估值已算出，但参数还没有批准。",
    "RESEARCH_PARAMETERS_UNAPPROVED": "研究参数还没有批准，不能作为买点依据。",
    "MODEL_NOT_IMPLEMENTED_EQUITY": "个股估值方法还没有实现。",
    "MODEL_SELECTION_NOT_IMPLEMENTED": "还没有实现模型适用性判断。",
    "MODEL_NOT_IMPLEMENTED_INDEX": "指数估值方法还没有实现。",
    "MODEL_NOT_IMPLEMENTED_ETF": "ETF 产品检查还没有实现。",
    "INDEX_NOT_IN_FIRST_BATCH": "这只指数不在最早接入的样本里，按它自己的数据能力单独处理。",
    "INDEX_REMOVED_OFFICIAL_HISTORY_UNAVAILABLE": "官方历史估值拿不到，已移出研究范围。",
    "ETF_TRACKING_INDEX_REMOVED": "跟踪的指数已移出研究范围。",
    "BANK_CAPITAL_CONSTRAINT": "资本充足率或分红路径触及约束。",
}

UNMET_PLAIN = {
    "pessimistic_value_nonpositive": "按最悲观假设算出的价值为负",
    "terminal_continuity_guardrail": "价值太依赖五年以后的假设",
    "cross_check_divergence": "两种算法结果差异较大",
    "approximation_impact": "少量近似处理影响了等级",
    "ocf_to_profit": "经营现金流与利润的匹配程度",
    "net_debt_to_ebitda": "有息负债相对经营利润偏高",
    "loss_years": "有亏损年份",
    "available_fiscal_years": "可用年报年份",
    "route_not_evaluable": "这条路线资料不齐",
    "bank_risk_evidence": "银行资本与资产质量资料",
    "leverage_guardrail": "有息负债超过护栏",
    "base_year_anomaly": "最近一年同比变化过大",
    "data_freshness": "数据太旧",
    "price_missing": "缺现价",
    "shares_missing": "缺总股本",
}

_PRIORITY = (
    "TERMINAL_GUARDRAIL_EXPLICIT_CASH",
    "TERMINAL_GUARDRAIL_TERMINAL_SHARE",
    "PESSIMISTIC_VALUE_NONPOSITIVE",
    "BRIDGE_RECONCILIATION_GAP",
    "BASE_YEAR_ANOMALY",
    "TERMINAL_GUARDRAIL_BY_DEFAULT",
    "LEVERAGE_GUARDRAIL_EXCEEDED",
    "STALE_PRICE",
    "STALE_FINANCIALS",
    "MISSING_BANK_RISK_EVIDENCE",
    "MISSING_BUSINESS_EVIDENCE",
    "POLICY_GRADE_C",
    "POLICY_STATUS_PARTIAL",
)


def cycle_text(family: str | None) -> str:
    return FAMILY_PLAIN.get(str(family or ""), "非周期")


def reason_plain(code: str) -> str:
    return REASON_PLAIN.get(str(code), "这次评估有未满足的条件，按政策不给正式结论。")


def unmet_plain(name: str) -> str:
    return UNMET_PLAIN.get(str(name), "有一项条件没有满足")


_CATEGORY = {
    "TERMINAL_GUARDRAIL_EXPLICIT_CASH": "cash",
    "TERMINAL_GUARDRAIL_TERMINAL_SHARE": "terminal",
    "PESSIMISTIC_VALUE_NONPOSITIVE": "pessimistic",
    "BRIDGE_RECONCILIATION_GAP": "bridge",
    "BASE_YEAR_ANOMALY": "base",
    "TERMINAL_GUARDRAIL_BY_DEFAULT": "default",
    "CROSS_CHECK_DIVERGENCE": "cross",
    "LEVERAGE_GUARDRAIL_EXCEEDED": "leverage",
    "terminal_continuity_guardrail": "terminal",
    "pessimistic_value_nonpositive": "pessimistic",
    "cross_check_divergence": "cross",
    "approximation_impact": "approx",
    "ocf_to_profit": "bridge",
    "base_year_anomaly": "base",
    "leverage_guardrail": "leverage",
}

def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def record_reasons(record: dict[str, Any], *, headline_only: bool = False) -> list[str]:
    """从评估记录提取不重复的大白话原因；按严重程度排序。"""
    codes = [str(code) for code in (record.get("reason_codes") or []) if code]
    ordered = [code for code in _PRIORITY if code in codes] + [
        code for code in codes if code not in _PRIORITY
    ]
    texts: list[str] = []
    seen_text: set[str] = set()
    seen_category: set[str] = set()
    for code in ordered:
        text = reason_plain(code)
        category = _CATEGORY.get(code)
        if text in seen_text or (category and category in seen_category):
            continue
        texts.append(text)
        seen_text.add(text)
        if category:
            seen_category.add(category)
    grade = (record.get("grade") or {}).get("unmet_conditions") or []
    for name in grade:
        text = unmet_plain(name)
        category = _CATEGORY.get(name)
        if text in seen_text or (category and category in seen_category):
            continue
        texts.append(text)
        seen_text.add(text)
        if category:
            seen_category.add(category)
    if headline_only:
        return texts[:1]
    return texts


def route_reason(record: dict[str, Any]) -> str:
    route = record.get("route") or {}
    family = route.get("family")
    if family == "cyclical":
        return "明显周期企业需要先判断周期位置，本版还没有实现，系统不给结论。"
    family_text = {
        "insurance": "保险公司",
        "broker": "证券公司",
        "holding": "控股平台",
        "distressed": "亏损或困境企业",
        "bank": "银行",
    }.get(str(family), "这类公司")
    return f"{family_text}需要专门的估值方法，本版还没有实现，系统不给结论。"


__all__ = [
    "BANNED_TERMS",
    "FAMILY_PLAIN",
    "REASON_PLAIN",
    "UNMET_PLAIN",
    "cycle_text",
    "reason_plain",
    "record_reasons",
    "route_reason",
    "unmet_plain",
]
