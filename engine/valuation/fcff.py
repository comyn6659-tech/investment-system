"""FCFF 路线：显式年度序列 → 折现 → 企业价值 → 普通股股权价值。

两步分离：
1. `project_fcff` 把研究假设（增长率、EBIT 利润率、税率、折旧摊销/资本开支占收入比、
   营运资本增量占比）变成显式的年度 EBIT / NOPAT / FCFF 序列；
2. `value_fcff` 只接收已经算好的年度序列与折现率、终值口径、权益桥接项。

这样折现内核不接触取数与模型选择，测试可以独立手算验证。

口径约定（施工指令 §3.2）：
- FCFF = EBIT × (1 − 税率) + 折旧摊销 − 资本开支 − 营运资本增加；
- 不得把「经营现金流 − 资本开支」未经桥接直接称为 FCFF（历史勾稽在 financials/bridge.py）；
- 企业价值 → 股权价值：每项调整只出现一次，净债务不得与债务、现金同时计入；
- 每股价值 = 归属普通股东价值 / 口径一致的股数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from engine.valuation import errors as e
from engine.valuation.discounting import (
    DiscountedStream,
    TerminalValue,
    discount_stream,
)
from engine.valuation.errors import ValuationError


@dataclass(frozen=True)
class FcffDrivers:
    """研究假设的显式驱动项，长度必须等于预测期年数。"""

    base_year: int
    base_revenue: float
    revenue_growth: tuple[float, ...]
    ebit_margin: tuple[float, ...]
    tax_rate: tuple[float, ...]
    da_pct_revenue: tuple[float, ...]
    capex_pct_revenue: tuple[float, ...]
    nwc_pct_incremental_revenue: float

    def __post_init__(self) -> None:
        horizon = len(self.revenue_growth)
        if horizon <= 0:
            raise ValuationError(e.HORIZON_INVALID, "预测期必须至少 1 年")
        for name in ("ebit_margin", "tax_rate", "da_pct_revenue", "capex_pct_revenue"):
            if len(getattr(self, name)) != horizon:
                raise ValuationError(
                    e.HORIZON_INVALID,
                    f"{name} 长度 {len(getattr(self, name))} 与预测期 {horizon} 不一致",
                )
        if self.base_revenue <= 0:
            raise ValuationError(
                e.MODEL_INPUT_MISSING, f"基期营业收入必须为正，实际 {self.base_revenue!r}"
            )
        for name in ("nwc_pct_incremental_revenue",):
            value = getattr(self, name)
            if value != value or value in (float("inf"), float("-inf")):
                raise ValuationError(e.CASH_FLOW_NOT_FINITE, f"{name} 必须有限")


@dataclass(frozen=True)
class FcffYear:
    """一年的 FCFF 构成，可与历史桥接表逐行对照。"""

    year: int
    revenue: float
    ebit: float
    tax_rate: float
    nopat: float
    depreciation_amortization: float
    capex: float
    change_in_nwc: float
    fcff: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "year": self.year,
            "revenue": self.revenue,
            "ebit": self.ebit,
            "tax_rate": self.tax_rate,
            "nopat": self.nopat,
            "depreciation_amortization": self.depreciation_amortization,
            "capex": self.capex,
            "change_in_nwc": self.change_in_nwc,
            "fcff": self.fcff,
        }


def project_fcff(drivers: FcffDrivers) -> tuple[FcffYear, ...]:
    """按显式驱动项生成年度 FCFF 序列（年末口径，第 1 期为 base_year+1）。"""
    years: list[FcffYear] = []
    revenue = float(drivers.base_revenue)
    for index, growth in enumerate(drivers.revenue_growth):
        prior_revenue = revenue
        revenue = prior_revenue * (1.0 + growth)
        ebit = revenue * drivers.ebit_margin[index]
        tax = drivers.tax_rate[index]
        nopat = ebit * (1.0 - tax)
        da = revenue * drivers.da_pct_revenue[index]
        capex = revenue * drivers.capex_pct_revenue[index]
        change_nwc = (revenue - prior_revenue) * drivers.nwc_pct_incremental_revenue
        fcff = nopat + da - capex - change_nwc
        years.append(
            FcffYear(
                year=drivers.base_year + 1 + index,
                revenue=revenue,
                ebit=ebit,
                tax_rate=tax,
                nopat=nopat,
                depreciation_amortization=da,
                capex=capex,
                change_in_nwc=change_nwc,
                fcff=fcff,
            )
        )
    return tuple(years)


# --- 企业价值 → 普通股股权价值 ----------------------------------------------
@dataclass(frozen=True)
class EquityBridge:
    """从企业价值走到归属普通股东的价值。

    刻意**不提供** net_debt 字段：净债务是 (债务 − 现金) 的派生量，
    如果同时允许传 net_debt 与 debt/cash，就会出现同一项被计两次的机会。
    """

    debt: float = 0.0
    cash: float = 0.0
    non_operating_assets: float = 0.0
    minority_interest: float = 0.0
    preferred_equity: float = 0.0
    shares: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "debt",
            "cash",
            "non_operating_assets",
            "minority_interest",
            "preferred_equity",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValuationError(e.BRIDGE_ITEM_INVALID, f"{name} 必须是数值，实际 {value!r}")
            if value != value or value in (float("inf"), float("-inf")):
                raise ValuationError(e.BRIDGE_ITEM_INVALID, f"{name} 必须有限，实际 {value!r}")
            if value < 0:
                raise ValuationError(
                    e.BRIDGE_ITEM_INVALID,
                    f"{name} 不能为负（{value!r}）：符号由桥接公式固定，负值请检查口径",
                )
        if self.shares is None or self.shares != self.shares:
            raise ValuationError(e.SHARE_COUNT_INVALID, "股数缺失")
        if not isinstance(self.shares, (int, float)) or self.shares <= 0:
            raise ValuationError(e.SHARE_COUNT_INVALID, f"股数必须为正，实际 {self.shares!r}")

    @property
    def net_debt(self) -> float:
        """派生量：有息负债 − 现金。仅用于展示，不得再作为桥接项传入。"""
        return self.debt - self.cash

    def equity_value(self, enterprise_value: float) -> float:
        return (
            enterprise_value
            - self.debt
            + self.cash
            + self.non_operating_assets
            - self.minority_interest
            - self.preferred_equity
        )

    def value_per_share(self, enterprise_value: float) -> float:
        return self.equity_value(enterprise_value) / self.shares

    def to_rows(self, enterprise_value: float) -> list[dict[str, object]]:
        """逐项桥接表：每项带符号、金额与口径说明，便于审计重复计数。"""
        rows: list[dict[str, object]] = [
            {
                "item": "enterprise_value",
                "label": "企业价值（EV，折现结果）",
                "sign": 1,
                "amount": enterprise_value,
                "note": "有限期现值 + 终值现值",
            },
            {
                "item": "debt",
                "label": "减：有息负债",
                "sign": -1,
                "amount": self.debt,
                "note": "短期借款 + 长期借款 + 应付债券 + 一年内到期非流动负债（含租赁负债时另行说明）",
            },
            {
                "item": "cash",
                "label": "加：货币资金",
                "sign": 1,
                "amount": self.cash,
                "note": "非经营性现金；若含受限资金需另行扣除",
            },
            {
                "item": "non_operating_assets",
                "label": "加：非经营性资产",
                "sign": 1,
                "amount": self.non_operating_assets,
                "note": "未纳入 FCFF 预测的长期股权投资等；本轮样本取 0 并说明原因",
            },
            {
                "item": "minority_interest",
                "label": "减：少数股东权益",
                "sign": -1,
                "amount": self.minority_interest,
                "note": "按账面权益扣除，未按公允价值重估",
            },
            {
                "item": "preferred_equity",
                "label": "减：其他权益工具（优先股/永续债）",
                "sign": -1,
                "amount": self.preferred_equity,
                "note": "归属普通股东之前需先扣除",
            },
        ]
        return rows


@dataclass(frozen=True)
class FcffValuation:
    years: tuple[FcffYear, ...]
    stream: DiscountedStream
    bridge: EquityBridge
    enterprise_value: float
    equity_value: float
    value_per_share: float
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "model": "fcff",
            "years": [y.to_dict() for y in self.years],
            "discounting": self.stream.to_dict(),
            "enterprise_value": self.enterprise_value,
            "bridge_rows": self.bridge.to_rows(self.enterprise_value),
            "equity_value": self.equity_value,
            "value_per_share": self.value_per_share,
            "shares": self.bridge.shares,
            "net_debt": self.bridge.net_debt,
        }


def terminal_from_last_year(
    years: Sequence[FcffYear],
    *,
    growth: float,
    basis: str,
) -> TerminalValue:
    """以末期 FCFF 为基准构造终值，并拒绝把异常负现金流永续化。"""
    if not years:
        raise ValuationError(e.EMPTY_CASH_FLOW_SERIES, "没有可用于终值的年度现金流")
    last = years[-1]
    if last.fcff <= 0:
        raise ValuationError(
            e.TERMINAL_BASIS_MISSING,
            f"末期 FCFF={last.fcff:.4f} 为负或为零，不能直接永续化；"
            "请改用明确的不启用终值口径，或先修正末期假设",
        )
    return TerminalValue(
        method="zero_growth_perpetuity" if growth == 0.0 else "growing_perpetuity",
        growth=growth,
        next_period_cash_flow=last.fcff * (1.0 + growth),
        basis=basis,
        terminal_year=last.year,
    )


def value_fcff(
    years: Sequence[FcffYear],
    *,
    discount_rate: float,
    terminal: TerminalValue | None,
    bridge: EquityBridge,
) -> FcffValuation:
    schedule = [y.fcff for y in years]
    if not schedule:
        raise ValuationError(e.EMPTY_CASH_FLOW_SERIES, "FCFF 序列为空")
    stream = discount_stream(
        schedule,
        discount_rate,
        terminal=terminal,
        last_period_year=years[-1].year,
    )
    enterprise_value = stream.total_present_value
    equity_value = bridge.equity_value(enterprise_value)
    warnings: list[str] = []
    if equity_value <= 0:
        warnings.append("折现得到的股权价值不为正：研究结果不能用于任何价格比较")
    if stream.terminal_present_value < 0:
        raise ValuationError(e.TERMINAL_BASIS_MISSING, "终值现值为负，口径不成立")
    return FcffValuation(
        years=tuple(years),
        stream=stream,
        bridge=bridge,
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        value_per_share=equity_value / bridge.shares,
        warnings=tuple(warnings),
    )
