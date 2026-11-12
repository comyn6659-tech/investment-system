"""业务载荷：个股 / 指数 / ETF 各自不同的结构。

共用身份、来源与日期，但不强迫指数填写公司 EPS，也不要求 ETF 有公司 ROE。
每种载荷都有明确的 `payload_type`，出口校验会核对它与 asset_class 是否匹配。
"""

from __future__ import annotations

from typing import Any

# 单位约定：
# - index_point：指数点位，不允许带币种；
# - CNY / CNY_per_share：金额，必须带币种；
# - percent：百分数（3.03 表示 3.03%）；
# - shares / count / ratio / per_share。
INDEX_POINT_UNIT = "index_point"
MONEY_UNITS = frozenset({"CNY", "CNY_per_share", "USD", "HKD"})
ALLOWED_UNITS = frozenset(
    {"index_point", "CNY", "CNY_per_share", "USD", "HKD", "percent", "shares", "count", "ratio", "per_share"}
)


def amount(value: float | int | None, unit: str, currency: str | None = None) -> dict[str, Any]:
    """构造规范金额/数值对象。"""
    if unit not in ALLOWED_UNITS:
        raise ValueError(f"未定义的单位: {unit}")
    if unit == INDEX_POINT_UNIT:
        if currency is not None:
            raise ValueError("指数点位不允许携带币种")
    elif unit in MONEY_UNITS:
        if not currency:
            raise ValueError(f"金额单位 {unit} 必须携带币种")
    return {"value": value, "unit": unit, "currency": currency}


PAYLOAD_SCHEMAS: dict[str, set[str]] = {
    "equity": {
        "payload_type",
        "company_facts",
        "financial_periods",
        "dividend_records",
        "dividend_summary",
        "model_applicability",
        "valuation",
        # 阶段二研究估值（有版本）：正式 valuation 仍保留给 validated 模型，
        # 研究结果单独放在这里，绝不混进市场事实字段
        "research_valuation",
        # 阶段四第二批：已批准政策的正式结论（含三档价值、等级、起约束条件）
        "policy_conclusion",
    },
    "index": {
        "payload_type",
        "index_class",
        "level",
        "valuation_facts",
        "history_coverage",
        "strategy_state",
        # 阶段四：候选 A 研究信号（未批准；只描述条件是否成立）
        "candidate_a_signal",
        # 阶段三研究画像（有版本）：描述性统计，不是交易信号
        "valuation_profile",
    },
    "etf": {
        "payload_type",
        "underlying_index",
        "price",
        "nav",
        "premium_discount",
        "fees",
        "trading",
        "tracking",
        # 阶段三产品资料完整性检查（有版本）
        "product_check",
    },
    "none": {"payload_type"},
}


# 可选字段：旧结果（阶段一）没有这些字段仍然合法，新结果可以带上
PAYLOAD_OPTIONAL_FIELDS: dict[str, set[str]] = {
    "equity": {"research_valuation", "dividend_summary", "policy_conclusion"},
    "index": {"valuation_profile", "candidate_a_signal"},
    "etf": {"product_check"},
    "none": set(),
}

RESEARCH_VALUATION_PAYLOAD_VERSION = "equity-research/1"
POLICY_CONCLUSION_PAYLOAD_VERSION = "equity-policy-conclusion/1"


def empty_equity_payload() -> dict[str, Any]:
    return {
        "payload_type": "equity",
        "company_facts": {},
        "financial_periods": [],
        "dividend_records": [],
        "model_applicability": {"status": "not_evaluated", "reason_code": None},
        "valuation": None,
        "research_valuation": None,
        "dividend_summary": None,
        "policy_conclusion": None,
    }


def empty_index_payload() -> dict[str, Any]:
    return {
        "payload_type": "index",
        "index_class": None,
        "level": None,
        "valuation_facts": {},
        "history_coverage": {},
        "strategy_state": {"status": "research_signal_only", "model_status": "research"},
        "candidate_a_signal": None,
        "valuation_profile": None,
    }


def empty_etf_payload() -> dict[str, Any]:
    return {
        "payload_type": "etf",
        "underlying_index": None,
        "price": None,
        "nav": None,
        "premium_discount": None,
        "fees": None,
        "trading": None,
        "tracking": None,
        "product_check": None,
    }


def empty_payload() -> dict[str, Any]:
    return {"payload_type": "none"}
