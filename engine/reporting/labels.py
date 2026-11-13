"""报告用中文标签：把内部英文条件名换成可读中文。

仅改变展示，不改变任何判定结果或字段值。未知名称原样保留并标注 `（内部名）`，
避免因为漏映射而静默丢失信息。
"""

from __future__ import annotations

CONDITION_LABELS = {
    "available_fiscal_years": "可得财年数",
    "loss_years": "亏损年数",
    "ocf_to_profit": "经营现金流/净利润",
    "net_debt_to_ebitda": "净有息负债/EBITDA",
    "cross_check_divergence": "两条路线分歧",
    "approximation_impact": "近似项影响",
    "bank_risk_evidence": "银行资本与资产质量证据",
    "leverage_guardrail": "杠杆护栏",
    "terminal_continuity_guardrail": "终值连续性护栏",
    "pessimistic_value_nonpositive": "悲观情景价值非正",
    "base_year_anomaly": "基期异常变动",
    "data_freshness": "数据新鲜度",
    "route_not_evaluable": "路线不可评估",
    "price_missing": "缺现价",
    "shares_missing": "缺总股本",
}

FAMILY_LABELS_ZH = {
    "non_financial": "非金融企业",
    "bank": "银行",
    "insurance": "保险",
    "broker": "证券",
    "holding": "控股平台",
    "cyclical": "明显周期",
    "distressed": "亏损/困境",
}


def condition_label(name: str) -> str:
    return CONDITION_LABELS.get(name, f"{name}（内部名）")


def condition_labels(names) -> list[str]:
    return [condition_label(str(name)) for name in names or []]


def family_label(name: str | None) -> str:
    if not name:
        return "未判定"
    return FAMILY_LABELS_ZH.get(name, name)


__all__ = ["CONDITION_LABELS", "FAMILY_LABELS_ZH", "condition_label", "condition_labels", "family_label"]
