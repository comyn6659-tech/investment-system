"""模型登记表。

设计书 §10：扩展模型必须声明 required_fields、supports、版本与失败状态。
阶段一没有任何模型实现，登记表只用于：
1. 说明「本来该用什么模型」；
2. 让结果里的 unsupported 有据可查，而不是含糊的「不支持」。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelDescriptor:
    model_id: str
    version: str
    applies_to: str  # equity / index / etf
    status: str  # planned / research / validated / deprecated
    implemented: bool
    required_fields: tuple[str, ...]
    supports: tuple[str, ...]
    failure_status: str
    notes: str

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "version": self.version,
            "applies_to": self.applies_to,
            "status": self.status,
            "implemented": self.implemented,
            "required_fields": list(self.required_fields),
            "supports": list(self.supports),
            "failure_status": self.failure_status,
            "notes": self.notes,
        }


MODELS: tuple[ModelDescriptor, ...] = (
    ModelDescriptor(
        model_id="equity_dcf_fcff",
        version="0.0.0-planned",
        applies_to="equity",
        status="planned",
        implemented=False,
        required_fields=(
            "fin.operating_cash_flow",
            "fin.capex",
            "fin.interest_expense",
            "fin.tax_rate",
            "fin.net_debt",
            "fin.share_count",
            "fin.non_operating_assets",
        ),
        supports=("可形成持续现金流预测的非金融公司",),
        failure_status="unsupported",
        notes="阶段二实现；FCFF 口径必须核实利息、税项、租赁、少数股东与净债务处理。",
    ),
    ModelDescriptor(
        model_id="equity_dividend_discount",
        version="0.0.0-planned",
        applies_to="equity",
        status="planned",
        implemented=False,
        required_fields=(
            "div.annual_cash_dividend",
            "div.fiscal_year",
            "div.status",
            "fin.net_profit",
            "fin.ocf",
        ),
        supports=("以持续现金分配为主要回报来源的成熟非金融公司",),
        failure_status="unsupported",
        notes="阶段二实现；特别分红不得冒充永续分红。",
    ),
    ModelDescriptor(
        model_id="bank_dividend_discount",
        version="0.0.0-planned",
        applies_to="equity",
        status="planned",
        implemented=False,
        required_fields=(
            "fin.equity",
            "fin.net_profit",
            "fin.npl_ratio",
            "fin.provision_coverage",
            "fin.capital_adequacy",
            "div.annual_cash_dividend",
        ),
        supports=("银行",),
        failure_status="unsupported",
        notes="阶段二实现；资本与资产质量数据缺失时不给正式买点。",
    ),
    ModelDescriptor(
        model_id="index_valuation_profile",
        version="0.0.0-planned",
        applies_to="index",
        status="planned",
        implemented=False,
        required_fields=("index.pe_ttm_series", "index.pb_series"),
        supports=("境内 A 股宽基指数",),
        failure_status="unsupported",
        notes="阶段三实现；必须先核实历史估值序列口径与真实覆盖长度。",
    ),
    ModelDescriptor(
        model_id="index_strategy_a_valuation_reversion",
        version="0.0.0-research",
        applies_to="index",
        status="research",
        implemented=False,
        required_fields=("index.pe_ttm_weekly_5y_history",),
        supports=("境内 A 股宽基指数",),
        failure_status="unsupported",
        notes="候选 A：PE-TTM 5 年分位 20 进 / 60 出 / 12 个月到期。阈值是研究起点，不是有效规则。",
    ),
    ModelDescriptor(
        model_id="index_strategy_b_cross_confirmation",
        version="0.0.0-research",
        applies_to="index",
        status="research",
        implemented=False,
        required_fields=("index.pe_ttm_weekly_5y_history", "index.pb_weekly_5y_history"),
        supports=("境内 A 股宽基指数",),
        failure_status="unsupported",
        notes="候选 B：PE 分位≤20 且 PB 分位≤40 进；PE≥60 或 PB≥70 出；同样 12 个月到期。",
    ),
    ModelDescriptor(
        model_id="etf_execution_check",
        version="0.0.0-planned",
        applies_to="etf",
        status="planned",
        implemented=False,
        required_fields=(
            "etf.price",
            "etf.nav",
            "etf.nav_disclosed_at",
            "etf.premium_discount_pct",
            "etf.management_fee_pct",
            "etf.custody_fee_pct",
            "etf.trading_status",
        ),
        supports=("境内普通被动 ETF",),
        failure_status="unsupported",
        notes="阶段三实现；指数信号与产品执行判断必须分开输出。",
    ),
)

MODELS_BY_ID = {m.model_id: m for m in MODELS}


def for_asset(asset_class: str) -> list[ModelDescriptor]:
    return [m for m in MODELS if m.applies_to == asset_class]


def planned_model_plan(asset_class: str, *, index_class: str | None = None) -> dict[str, object]:
    """阶段一的模型计划：不选模型，但如实列出「本应评估哪些模型」。"""
    candidates = for_asset(asset_class)
    if asset_class == "equity" and index_class is not None:  # pragma: no cover - 防御
        candidates = []
    return {
        "primary_model": None,
        "required_checks": [],
        "selection_reason": "阶段一不实现模型适用性判断，因此不选主模型",
        "not_selected": [
            {
                "model_id": m.model_id,
                "status": m.status,
                "implemented": m.implemented,
                "reason": "model_not_implemented",
            }
            for m in candidates
        ],
        "candidate_models": [m.to_dict() for m in candidates],
    }
