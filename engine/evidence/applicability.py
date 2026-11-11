"""模型适用性：从结构化业务证据到「用哪个模型 / 为什么不用」。

规则（施工指令 §3.1）：
- 行业只提供线索，不能单独完成模型选择；
- 首版未实现的路线（保险、券商、控股、明显周期、困境）明确返回适用模型类别
  与本版不能处理的原因，**不默认送进 DCF**；
- 判定只使用业务证据与经营指标，**不使用当前股价**，因此选择不会随价格改变；
- 缺少关键业务证据时停在「尚不能判断」，不按行业白名单直接计算。

判定阈值为研究候选（research candidate），随结果一起输出，未获设计方批准。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.contracts import reason_codes as rc
from engine.evidence.models import BusinessEvidence

POLICY_VERSION = "model-applicability/1"

# 研究候选阈值：全部随结果输出，未获批准
SELECTION_POLICY: dict[str, Any] = {
    "policy_version": POLICY_VERSION,
    "status": "research_candidate_pending_approval",
    "revenue_volatility_cyclical": 0.08,
    "revenue_drawdown_cyclical": 0.15,
    "cyclical_industries": [
        "煤炭",
        "钢铁",
        "有色",
        "化工",
        "航运",
        "港口",
        "房地产",
        "水泥",
        "建材",
        "工程机械",
        "养殖",
        "石油",
        "航空",
    ],
    "fcff_capex_intensity_max": 0.10,
    "fcff_cash_conversion_min": 0.70,
    "dividend_payout_min": 0.55,
    "notes": [
        "阈值是研究起点，不是已批准政策；提高或降低门槛会改变主模型但不改变已算出的价值。",
        "判定只读业务证据与经营指标，不读当前股价。",
    ],
}

# 首版未实现的路线 → 适用的模型类别 + 本版不能处理的原因
UNIMPLEMENTED_FAMILIES: dict[str, dict[str, str]] = {
    "insurance": {
        "label": "保险（寿险/财险）",
        "applicable_model_category": "内含价值（EV）+ 新业务价值（NBV）折现",
        "reason_code": rc.MODEL_FAMILY_NOT_IMPLEMENTED,
        "reason": (
            "保险公司利润与现金流受准备金假设、投资收益率与会计准则共同影响，"
            "普通企业 FCFF 与股息折现会把投资收益与准备金释放错当成经营现金流。"
            "本版未实现内含价值口径，因此不进任何估值模型。"
        ),
        "required_evidence_to_implement": [
            "内含价值报告（EV）与其假设",
            "新业务价值（NBV）与代理人/银保渠道拆分",
            "投资资产久期与收益率假设",
        ],
    },
    "broker": {
        "label": "证券（券商）",
        "applicable_model_category": "正常化盈利 + 分部（经纪/自营/资管/资本中介）估值",
        "reason_code": rc.MODEL_FAMILY_NOT_IMPLEMENTED,
        "reason": (
            "券商利润高度依赖市场成交与自营波动，资本中介业务还占用大量资产负债表；"
            "单点 FCFF 或股息折现会把周期性收入当成可持续现金流。"
            "本版未实现正常化盈利与分部口径，因此不进任何估值模型。"
        ),
        "required_evidence_to_implement": [
            "分业务收入与利润（经纪/自营/资管/信用）",
            "净资本与风险覆盖率",
            "市场成交额与自营收益的正常化基准",
        ],
    },
    "holding": {
        "label": "控股平台",
        "applicable_model_category": "分部/持股净资产（NAV）折价估值",
        "reason_code": rc.MODEL_FAMILY_NOT_IMPLEMENTED,
        "reason": (
            "控股平台的合并现金流来自不同行业子公司，直接折现会混用不同风险与增长；"
            "本版未实现分部估值与控股折价口径，因此不进任何估值模型。"
        ),
        "required_evidence_to_implement": [
            "主要子公司持股比例与单独财务数据",
            "各分部行业分类",
            "控股折价的历史依据",
        ],
    },
    "cyclical": {
        "label": "明显周期企业",
        "applicable_model_category": "周期位置判断 + 正常化现金流折现",
        "reason_code": rc.MODEL_FAMILY_NOT_IMPLEMENTED,
        "reason": (
            "明显周期企业的单年现金流由价格周期主导，直接用当期现金流外推会产生顺周期偏差；"
            "本版未实现周期位置判断与正常化口径，因此不进任何估值模型。"
        ),
        "required_evidence_to_implement": [
            "产品价格与价差的多年序列",
            "产能利用率与库存周期位置",
            "跨越完整周期的正常化利润率",
        ],
    },
    "distressed": {
        "label": "亏损/困境企业",
        "applicable_model_category": "持续经营判断 + 恢复路径/清算回收估值",
        "reason_code": rc.MODEL_FAMILY_NOT_IMPLEMENTED,
        "reason": (
            "亏损或困境企业在恢复路径未确认前，"
            "股息与自由现金流都不具备可外推的基准；本版未实现持续经营判断，因此不进任何估值模型。"
        ),
        "required_evidence_to_implement": [
            "债务到期结构与现金余额",
            "亏损原因拆分（一次性/结构性）",
            "恢复或重组的可执行方案",
        ],
    },
}

FAMILY_LABELS = {
    "non_financial": "非金融企业",
    "bank": "银行",
    **{key: value["label"] for key, value in UNIMPLEMENTED_FAMILIES.items()},
}


@dataclass(frozen=True)
class RejectedModel:
    model_id: str
    reason_code: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "reason_code": self.reason_code,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ApplicabilityDecision:
    status: str  # selected / family_not_implemented / insufficient_evidence
    family: str
    primary_model: str | None
    cross_check_model: str | None
    reasons: tuple[dict[str, Any], ...]
    rejected: tuple[RejectedModel, ...]
    indicators: dict[str, Any]
    evidence_id: str | None = None
    evidence_version: str | None = None
    applicable_model_category: str | None = None
    required_evidence_to_implement: tuple[str, ...] = ()
    policy: dict[str, Any] = field(default_factory=lambda: dict(SELECTION_POLICY))

    @property
    def family_label(self) -> str:
        return FAMILY_LABELS.get(self.family, self.family)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "family": self.family,
            "family_label": self.family_label,
            "primary_model": self.primary_model,
            "cross_check_model": self.cross_check_model,
            "applicable_model_category": self.applicable_model_category,
            "required_evidence_to_implement": list(self.required_evidence_to_implement),
            "reasons": [dict(r) for r in self.reasons],
            "rejected_models": [r.to_dict() for r in self.rejected],
            "indicators": self.indicators,
            "evidence_id": self.evidence_id,
            "evidence_version": self.evidence_version,
            "policy": self.policy,
        }


def _contains(text: str | None, needles: tuple[str, ...]) -> bool:
    return bool(text) and any(needle in str(text) for needle in needles)


def classify_family(evidence: BusinessEvidence) -> tuple[str, list[dict[str, Any]]]:
    """只用业务证据判定企业族；返回 (family, 判定依据)。"""
    reasons: list[dict[str, Any]] = []
    industry = evidence.industry_clue or ""
    top = evidence.top_segments(limit=2)
    top_names = [s.segment for s in top]

    if _contains(industry, ("保险", "保险Ⅱ", "保险Ⅲ")) or any(
        _contains(name, ("寿险", "财产保险", "再保险", "健康险")) for name in top_names
    ):
        reasons.append(
            {
                "signal": "industry_and_segments",
                "observed": {"industry": industry, "top_segments": top_names},
                "conclusion": "financial_insurance",
                "note": "行业与主营构成同时指向保险业务",
            }
        )
        return "insurance", reasons

    if _contains(industry, ("证券", "证券Ⅱ", "证券Ⅲ")):
        reasons.append(
            {
                "signal": "industry",
                "observed": {"industry": industry, "top_segments": top_names},
                "conclusion": "financial_broker",
                "note": "行业分类指向证券业务，主营构成包含经纪/证券投资",
            }
        )
        return "broker", reasons

    if _contains(industry, ("银行", "银行Ⅱ", "银行Ⅲ")):
        reasons.append(
            {
                "signal": "industry",
                "observed": {"industry": industry, "top_segments": top_names},
                "conclusion": "financial_bank",
                "note": "银行以存贷利差与资本占用为核心，适用银行专用口径",
            }
        )
        return "bank", reasons

    if _contains(industry, ("综合", "控股")):
        reasons.append(
            {
                "signal": "industry",
                "observed": {"industry": industry},
                "conclusion": "holding_platform",
                "note": "行业分类为综合/控股，需分部口径",
            }
        )
        return "holding", reasons

    loss_years = evidence.loss_years(recent=4)
    if loss_years:
        reasons.append(
            {
                "signal": "annual_history",
                "observed": {"loss_years": list(loss_years)},
                "conclusion": "distressed_or_loss_making",
                "note": "最近四个财年内出现归母净利润为负",
            }
        )
        return "distressed", reasons

    volatility = evidence.revenue_volatility()
    drawdown = evidence.revenue_drawdown()
    cyclical_industry = any(
        key in industry for key in SELECTION_POLICY["cyclical_industries"]
    )
    if cyclical_industry and (
        (volatility is not None and volatility >= SELECTION_POLICY["revenue_volatility_cyclical"])
        or (drawdown is not None and drawdown >= SELECTION_POLICY["revenue_drawdown_cyclical"])
    ):
        reasons.append(
            {
                "signal": "industry_plus_revenue_volatility",
                "observed": {
                    "industry": industry,
                    "revenue_growth_stdev": volatility,
                    "max_revenue_drawdown": drawdown,
                },
                "conclusion": "cyclical",
                "note": (
                    "行业属周期类且收入波动/回撤超过研究阈值；"
                    "行业本身只是线索，需收入波动共同确认"
                ),
            }
        )
        return "cyclical", reasons

    reasons.append(
        {
            "signal": "default_non_financial",
            "observed": {
                "industry": industry,
                "top_segments": top_names,
                "revenue_growth_stdev": volatility,
                "max_revenue_drawdown": drawdown,
                "cyclical_industry_clue": cyclical_industry,
            },
            "conclusion": "non_financial",
            "note": "未命中金融、控股、困境或明显周期规则",
        }
    )
    return "non_financial", reasons


def _reject_unimplemented(
    family: str, evidence: BusinessEvidence, reasons: list[dict[str, Any]]
) -> ApplicabilityDecision:
    spec = UNIMPLEMENTED_FAMILIES[family]
    return ApplicabilityDecision(
        status="family_not_implemented",
        family=family,
        primary_model=None,
        cross_check_model=None,
        reasons=tuple(reasons),
        rejected=(
            RejectedModel("equity_dcf_fcff", spec["reason_code"], spec["reason"]),
            RejectedModel("equity_dividend_discount", spec["reason_code"], spec["reason"]),
        ),
        indicators=_indicators(evidence),
        evidence_id=evidence.evidence_id,
        evidence_version=evidence.version,
        applicable_model_category=spec["applicable_model_category"],
        required_evidence_to_implement=tuple(spec["required_evidence_to_implement"]),
    )


def _indicators(evidence: BusinessEvidence) -> dict[str, Any]:
    return {
        "industry": evidence.industry_clue,
        "top_segments": [
            {"segment": s.segment, "revenue_share": s.revenue_share}
            for s in evidence.top_segments()
        ],
        "revenue_growth_stdev": evidence.revenue_volatility(),
        "max_revenue_drawdown": evidence.revenue_drawdown(),
        "loss_years": list(evidence.loss_years(recent=4)),
        "annual_years_observed": len(evidence.annual),
    }


def _reject_other(
    evidence: BusinessEvidence, reasons: list[dict[str, Any]], primary: str
) -> tuple[RejectedModel, ...]:
    rejected: list[RejectedModel] = []
    other = (
        "equity_dividend_discount" if primary == "equity_dcf_fcff" else "equity_dcf_fcff"
    )
    if other == "equity_dcf_fcff":
        rejected.append(
            RejectedModel(
                "equity_dcf_fcff",
                rc.MODEL_CROSS_CHECK_ONLY,
                "作为交叉检查模型：本标的资本开支较重或现金转化偏低，"
                "单年自由现金流受再投资节奏影响，不宜作为主模型",
            )
        )
    else:
        rejected.append(
            RejectedModel(
                "equity_dividend_discount",
                rc.MODEL_CROSS_CHECK_ONLY,
                "作为交叉检查模型：本标的资本开支轻、现金转化高，"
                "分配路径主要由自由现金流决定，股息折现提供一致性检验",
            )
        )
    _ = (evidence, reasons)
    return tuple(rejected)


@dataclass(frozen=True)
class SelectionMetrics:
    """选择主模型所需的经营指标（全部来自已核实数据，不含股价）。"""

    capex_intensity: float | None
    cash_conversion: float | None  # FCFF / 归母净利润
    payout_latest: float | None
    payout_mean_3y: float | None
    dividend_years_observed: int
    dividend_stability_cv: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "capex_intensity": self.capex_intensity,
            "cash_conversion": self.cash_conversion,
            "payout_latest": self.payout_latest,
            "payout_mean_3y": self.payout_mean_3y,
            "dividend_years_observed": self.dividend_years_observed,
            "dividend_stability_cv": self.dividend_stability_cv,
        }


def decide(
    evidence: BusinessEvidence | None,
    metrics: SelectionMetrics | None,
) -> ApplicabilityDecision:
    """完整判定：企业族 → 是否可实现 → 主模型与交叉检查模型。"""
    if evidence is None:
        return ApplicabilityDecision(
            status="insufficient_evidence",
            family="unknown",
            primary_model=None,
            cross_check_model=None,
            reasons=(
                {
                    "signal": "business_evidence_missing",
                    "observed": {},
                    "conclusion": "cannot_decide",
                    "note": (
                        "缺少结构化业务证据：无法判断现金流性质与分配基础，"
                        "不按行业名称直接套用模型"
                    ),
                },
            ),
            rejected=(),
            indicators={},
        )

    family, reasons = classify_family(evidence)
    if family in UNIMPLEMENTED_FAMILIES:
        return _reject_unimplemented(family, evidence, reasons)

    if family == "bank":
        return ApplicabilityDecision(
            status="selected",
            family=family,
            primary_model="bank_dividend_discount",
            cross_check_model=None,
            reasons=tuple(reasons),
            rejected=(
                RejectedModel(
                    "equity_dcf_fcff",
                    rc.MODEL_NOT_APPLICABLE_TO_BANK,
                    "银行不适用普通企业 FCFF：存款与同业负债是经营负债而非融资，"
                    "资本开支与营运资本口径也不成立",
                ),
                RejectedModel(
                    "equity_dividend_discount",
                    rc.MODEL_NOT_APPLICABLE_TO_BANK,
                    "普通企业股息折现缺少资本充足与资产质量约束，"
                    "银行必须走 bank_dividend_discount",
                ),
            ),
            indicators=_indicators(evidence),
            evidence_id=evidence.evidence_id,
            evidence_version=evidence.version,
            applicable_model_category="银行专用股息折现（资本与资产质量约束）",
        )

    if metrics is None:
        reasons.append(
            {
                "signal": "operating_metrics_missing",
                "observed": {},
                "conclusion": "cannot_choose_between_fcff_and_ddm",
                "note": "缺少资本开支强度/现金转化/派息等经营指标，无法在两条路线中选择主模型",
            }
        )
        return ApplicabilityDecision(
            status="insufficient_evidence",
            family=family,
            primary_model=None,
            cross_check_model=None,
            reasons=tuple(reasons),
            rejected=(),
            indicators=_indicators(evidence),
            evidence_id=evidence.evidence_id,
            evidence_version=evidence.version,
        )

    capex_intensity = metrics.capex_intensity
    conversion = metrics.cash_conversion
    payout = metrics.payout_mean_3y if metrics.payout_mean_3y is not None else metrics.payout_latest

    policy = SELECTION_POLICY
    reasons.append(
        {
            "signal": "capital_intensity_and_cash_conversion",
            "observed": {
                "capex_intensity": capex_intensity,
                "cash_conversion_fcff_over_profit": conversion,
                "payout_mean_3y": metrics.payout_mean_3y,
                "thresholds": {
                    "fcff_capex_intensity_max": policy["fcff_capex_intensity_max"],
                    "fcff_cash_conversion_min": policy["fcff_cash_conversion_min"],
                    "dividend_payout_min": policy["dividend_payout_min"],
                },
            },
            "conclusion": None,
            "note": "经营指标只用于在两条已实现路线之间排序，不改变价值计算本身",
        }
    )

    if (
        capex_intensity is not None
        and conversion is not None
        and capex_intensity < policy["fcff_capex_intensity_max"]
        and conversion >= policy["fcff_cash_conversion_min"]
    ):
        primary, cross = "equity_dcf_fcff", "equity_dividend_discount"
        reasons[-1] = {
            **reasons[-1],
            "conclusion": "fcff_primary",
            "note": (
                f"资本开支强度 {capex_intensity:.1%} < {policy['fcff_capex_intensity_max']:.0%}"
                f" 且 FCFF/归母净利润 {conversion:.2f} ≥ {policy['fcff_cash_conversion_min']:.2f}："
                "自由现金流本身就是可分配来源，用 FCFF 作主模型，股息折现作交叉检查"
            ),
        }
    elif payout is not None and payout >= policy["dividend_payout_min"]:
        primary, cross = "equity_dividend_discount", "equity_dcf_fcff"
        reasons[-1] = {
            **reasons[-1],
            "conclusion": "dividend_primary",
            "note": (
                f"三年平均派息率 {payout:.1%} ≥ {policy['dividend_payout_min']:.0%}，"
                f"且资本开支强度 {capex_intensity if capex_intensity is None else round(capex_intensity, 4)}："
                "股东回报主要由分配路径决定，用股息折现作主模型，FCFF 作交叉检查"
            ),
        }
    else:
        primary, cross = "equity_dcf_fcff", "equity_dividend_discount"
        reasons[-1] = {
            **reasons[-1],
            "conclusion": "fcff_primary_by_default",
            "note": "资本开支轻或现金转化未知、派息率也不足：按经营现金流路线作主模型",
        }

    return ApplicabilityDecision(
        status="selected",
        family=family,
        primary_model=primary,
        cross_check_model=cross,
        reasons=tuple(reasons),
        rejected=_reject_other(evidence, reasons, primary),
        indicators={**_indicators(evidence), **metrics.to_dict()},
        evidence_id=evidence.evidence_id,
        evidence_version=evidence.version,
    )


__all__ = [
    "ApplicabilityDecision",
    "FAMILY_LABELS",
    "POLICY_VERSION",
    "RejectedModel",
    "SELECTION_POLICY",
    "SelectionMetrics",
    "UNIMPLEMENTED_FAMILIES",
    "classify_family",
    "decide",
]
