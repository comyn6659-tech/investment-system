"""银行专用股息折现路线。

与普通企业股息路线的共同点：只用股东分配与股权回报要求折现，复用 `ddm` 的数学。
不同点（施工指令 §3.2）：

- **不使用**普通企业净债务口径，也不使用通用经营现金流门槛；
- 银行必须先过资本与资产质量检查：核心一级资本充足率、不良率、拨备覆盖率；
- 监管下限与风险阈值必须来自可核实来源，缺失即拒绝，绝不由实现方自造默认值；
- 「国有/大银行」不构成资本或风险检查的替代；
- 分配还需通过资本一致性检查：留存收益能否支撑风险加权资产增长所需的资本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from engine.valuation import errors as e
from engine.valuation.ddm import DividendDrivers, DividendIssue, value_dividend
from engine.valuation.errors import ValuationError

# 必须齐备的银行风险证据字段（缺失即 insufficient_data，不进入计算）
REQUIRED_RISK_FIELDS = (
    "cet1_ratio",
    "required_cet1_ratio",
    "npl_ratio",
    "provision_coverage",
    "required_provision_coverage",
    "risk_weighted_assets",
)


@dataclass(frozen=True)
class BankRiskEvidence:
    """银行资本与资产质量证据。每个字段都应带来源（在假设文件里记录）。"""

    cet1_ratio: float
    required_cet1_ratio: float
    npl_ratio: float
    provision_coverage: float
    required_provision_coverage: float
    risk_weighted_assets: float
    rwa_growth: float
    cet1_ratio_prior: float | None = None
    npl_ratio_prior: float | None = None

    @classmethod
    def missing_fields(cls, data: Mapping[str, object]) -> list[str]:
        missing: list[str] = []
        for name in REQUIRED_RISK_FIELDS:
            value = data.get(name)
            if value is None or isinstance(value, str):
                missing.append(name)
        return missing

    def checks(self) -> list[DividendIssue]:
        """返回非阻断问题；阻断问题直接抛错。"""
        issues: list[DividendIssue] = []
        if self.cet1_ratio < self.required_cet1_ratio:
            raise ValuationError(
                e.BANK_CAPITAL_BELOW_REQUIREMENT,
                f"核心一级资本充足率 {self.cet1_ratio:.2%} 低于适用下限 "
                f"{self.required_cet1_ratio:.2%}：分配能力受资本约束限制",
            )
        if self.provision_coverage < self.required_provision_coverage:
            raise ValuationError(
                e.BANK_PROVISION_BELOW_REQUIREMENT,
                f"拨备覆盖率 {self.provision_coverage:.2%} 低于适用下限 "
                f"{self.required_provision_coverage:.2%}：资产质量缓冲不足",
            )
        if (
            self.cet1_ratio_prior is not None
            and self.cet1_ratio < self.cet1_ratio_prior
        ):
            issues.append(
                DividendIssue(
                    code="BANK_CAPITAL_BUFFER_NARROWING",
                    message=(
                        f"核心一级资本充足率由 {self.cet1_ratio_prior:.2%} 降至 "
                        f"{self.cet1_ratio:.2%}，分配空间在收窄"
                    ),
                    blocking=False,
                )
            )
        if self.npl_ratio_prior is not None and self.npl_ratio > self.npl_ratio_prior:
            issues.append(
                DividendIssue(
                    code=e.BANK_ASSET_QUALITY_DETERIORATING,
                    message=(
                        f"不良率由 {self.npl_ratio_prior:.2%} 升至 {self.npl_ratio:.2%}："
                        "需要用拨备与资本吸收，股息路径应下调"
                    ),
                    blocking=False,
                )
            )
        return issues

    def required_retained_for_growth(self, net_profit: float) -> float:
        """支撑风险加权资产增长所需的留存收益。"""
        growth_asset = self.risk_weighted_assets * self.rwa_growth
        return growth_asset * self.required_cet1_ratio

    def capital_consistent_payout(self, net_profit: float) -> float:
        """在不消耗资本缓冲的前提下，当年最多可分配的派息率。"""
        if net_profit <= 0:
            raise ValuationError(
                e.MODEL_INPUT_MISSING, f"净利润 {net_profit!r} 不为正，无法计算可分配上限"
            )
        required = self.required_retained_for_growth(net_profit)
        ratio = 1.0 - required / net_profit
        return max(0.0, min(1.0, ratio))

    def to_dict(self) -> dict[str, object]:
        return {
            "cet1_ratio": self.cet1_ratio,
            "required_cet1_ratio": self.required_cet1_ratio,
            "npl_ratio": self.npl_ratio,
            "provision_coverage": self.provision_coverage,
            "required_provision_coverage": self.required_provision_coverage,
            "risk_weighted_assets": self.risk_weighted_assets,
            "rwa_growth": self.rwa_growth,
            "cet1_ratio_prior": self.cet1_ratio_prior,
            "npl_ratio_prior": self.npl_ratio_prior,
        }


@dataclass(frozen=True)
class BankDividendDrivers:
    """银行股息路径：由归母净利润、派息率与股数推出每股分配。"""

    base_year: int
    base_net_profit_parent: float
    net_profit_growth: tuple[float, ...]
    payout: tuple[float, ...]
    shares: float
    cost_of_equity: float
    terminal_growth: float | None
    roe: float | None = None
    growth_consistency_tolerance: float = 0.0


@dataclass(frozen=True)
class BankValuation:
    dps_path: tuple[float, ...]
    net_profit_path: tuple[float, ...]
    payout_path: tuple[float, ...]
    value_per_share: float
    dividend_valuation: object
    risk: BankRiskEvidence
    capital_checks: tuple[dict[str, object], ...] = ()
    issues: tuple[DividendIssue, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "model": "bank_dividend_discount",
            "net_profit_path": list(self.net_profit_path),
            "dps_path": list(self.dps_path),
            "payout_path": list(self.payout_path),
            "value_per_share": self.value_per_share,
            "dividend_discount": self.dividend_valuation.to_dict(),
            "risk_evidence": self.risk.to_dict(),
            "capital_checks": list(self.capital_checks),
            "issues": [i.to_dict() for i in self.issues],
        }


def value_bank_dividend(
    drivers: BankDividendDrivers,
    risk: BankRiskEvidence,
) -> BankValuation:
    if drivers.shares <= 0:
        raise ValuationError(e.SHARE_COUNT_INVALID, f"股数必须为正，实际 {drivers.shares!r}")
    if drivers.base_net_profit_parent <= 0:
        raise ValuationError(
            e.MODEL_INPUT_MISSING, "基期归母净利润不为正：银行股息路线不适用于亏损期"
        )
    issues = list(risk.checks())

    net_profit = drivers.base_net_profit_parent
    net_profit_path: list[float] = []
    dps_path: list[float] = []
    eps_path: list[float] = []
    capital_checks: list[dict[str, object]] = []
    for index, growth in enumerate(drivers.net_profit_growth):
        net_profit = net_profit * (1.0 + growth)
        year = drivers.base_year + 1 + index
        payout = drivers.payout[index]
        eps = net_profit / drivers.shares
        dps = eps * payout
        net_profit_path.append(net_profit)
        eps_path.append(eps)
        dps_path.append(dps)
        ceiling = risk.capital_consistent_payout(net_profit)
        capital_checks.append(
            {
                "year": year,
                "payout": payout,
                "capital_consistent_payout_ceiling": ceiling,
                "within_capital": payout <= ceiling + 1e-9,
                "retained": net_profit * (1.0 - payout),
                "required_retained": risk.required_retained_for_growth(net_profit),
            }
        )
    over_capital = [c for c in capital_checks if not c["within_capital"]]
    if over_capital:
        issues.append(
            DividendIssue(
                code="BANK_DISTRIBUTION_ABOVE_CAPITAL_CEILING",
                message=(
                    f"{len(over_capital)} 个预测年度的派息率超过资本一致性上限："
                    "该分配路径需要消耗资本缓冲或补充资本，不能按可持续分配看待"
                ),
                blocking=False,
            )
        )

    dividend_drivers = DividendDrivers(
        base_year=drivers.base_year,
        base_eps=drivers.base_net_profit_parent / drivers.shares,
        eps_growth=drivers.net_profit_growth,
        payout=drivers.payout,
        cost_of_equity=drivers.cost_of_equity,
        terminal_growth=drivers.terminal_growth,
        roe=drivers.roe,
        growth_consistency_tolerance=drivers.growth_consistency_tolerance,
    )
    valuation = value_dividend(dividend_drivers, issues=issues)
    # 结果必须与自算的 DPS 路径一致，避免两套算法漂移
    expected = tuple(round(x, 9) for x in dps_path)
    actual = tuple(round(y.dps, 9) for y in valuation.years)
    if expected != actual:
        raise ValuationError(
            e.BANK_CAPITAL_BELOW_REQUIREMENT,
            f"银行 DPS 路径与股息内核不一致：{expected} != {actual}",
        )
    _ = eps_path
    return BankValuation(
        dps_path=tuple(dps_path),
        net_profit_path=tuple(net_profit_path),
        payout_path=tuple(drivers.payout),
        value_per_share=valuation.value_per_share,
        dividend_valuation=valuation,
        risk=risk,
        capital_checks=tuple(capital_checks),
        issues=tuple(valuation.issues),
    )


def require_risk_evidence(data: Mapping[str, object]) -> BankRiskEvidence:
    """把缺失字段变成明确的 insufficient_data，而不是猜默认值。"""
    missing = BankRiskEvidence.missing_fields(data)
    if missing:
        raise ValuationError(
            e.BANK_RISK_EVIDENCE_MISSING,
            "银行路线缺少资本/资产质量字段: " + ", ".join(missing),
        )
    return BankRiskEvidence(
        cet1_ratio=float(data["cet1_ratio"]),
        required_cet1_ratio=float(data["required_cet1_ratio"]),
        npl_ratio=float(data["npl_ratio"]),
        provision_coverage=float(data["provision_coverage"]),
        required_provision_coverage=float(data["required_provision_coverage"]),
        risk_weighted_assets=float(data["risk_weighted_assets"]),
        rwa_growth=float(data.get("rwa_growth") or 0.0),
        cet1_ratio_prior=data.get("cet1_ratio_prior"),  # type: ignore[arg-type]
        npl_ratio_prior=data.get("npl_ratio_prior"),  # type: ignore[arg-type]
    )
