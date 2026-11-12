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
        version="0.2.0-research",
        applies_to="equity",
        status="research",
        implemented=True,
        required_fields=(
            "fin.revenue",
            "fin.pretax_profit",
            "fin.income_tax",
            "fin.finance_expense",
            "fin.capex",
            "fin.ocf",
            "fin.cash",
            "fin.equity_parent",
            "profile.total_shares",
            "price.close",
        ),
        supports=(
            "资本开支轻、现金转化高的非金融公司",
            "可用显式研究假设形成年度经营预测的标的",
        ),
        failure_status="insufficient_data",
        notes=(
            "研究版：EBIT×(1−t)+折旧摊销−资本开支−营运资本增加；"
            "历史勾稽与 OCF 对照，差额超容差按 partial 输出。参数未获批准。"
        ),
    ),
    ModelDescriptor(
        model_id="equity_dividend_discount",
        version="0.2.0-research",
        applies_to="equity",
        status="research",
        implemented=True,
        required_fields=(
            "div.*.per_10",
            "div.*.status",
            "fin.net_profit_parent",
            "fin.equity_parent",
            "profile.total_shares",
            "price.close",
        ),
        supports=("派息率稳定、以现金分配为主要回报来源的成熟非金融公司",),
        failure_status="insufficient_data",
        notes=(
            "研究版：DPS = EPS × 派息率，用股权成本折现；"
            "特别分红不进入可持续 DPS；终值增长不得超过 ROE×留存率。参数未获批准。"
        ),
    ),
    ModelDescriptor(
        model_id="bank_dividend_discount",
        version="0.2.0-research",
        applies_to="equity",
        status="research",
        implemented=True,
        required_fields=(
            "fin.net_profit_parent",
            "fin.equity_parent",
            "div.*.per_10",
            "profile.total_shares",
            "bank.cet1_ratio",
            "bank.npl_ratio",
            "bank.provision_coverage",
            "bank.risk_weighted_assets",
        ),
        supports=("商业银行（含国有大行、股份行、城商行）",),
        failure_status="insufficient_data",
        notes=(
            "研究版：复用股息折现数学，但必须先过资本充足与资产质量检查；"
            "监管下限必须来自可核实来源，缺失即 insufficient_data。参数未获批准。"
        ),
    ),
    ModelDescriptor(
        model_id="index_valuation_profile",
        version="0.3.0-research",
        applies_to="index",
        status="research",
        implemented=True,
        required_fields=(
            "series.pe_rolling.monthly",
            "series.pb.monthly",
            "index.level",
        ),
        supports=("境内 A 股宽基指数（首批沪深300 / 中证500 / 中证1000）",),
        failure_status="insufficient_data",
        notes=(
            "研究版描述性画像：同源同列月度参考池 60 个月、有效月份≥54 才给分位；"
            "PE 与 PB 分别计算，不拼综合分；不输出目标点位，不构成交易信号。参数未获批准。"
        ),
    ),
    ModelDescriptor(
        model_id="index_strategy_a_valuation_reversion",
        version="0.4.0-research",
        applies_to="index",
        status="research",
        implemented=True,
        required_fields=(
            "series.official_close.daily",
            "series.official_pe_rolling.daily",
        ),
        supports=("境内 A 股宽基指数（首批沪深300 / 中证500 / 中证1000）",),
        failure_status="insufficient_data",
        notes=(
            "候选 A：官方滚动市盈率周度分位 20 进 / 60 出 / 12 个月到期，"
            "T+1 收盘执行（研究回测口径）。已实现为研究信号 + 只追加账本；"
            "阈值与规则未获批准，不构成买卖建议。"
        ),
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
