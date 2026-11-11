"""折现数学：纯函数，不依赖任何数据源、模型选择或报告。

共同约定（与本轮施工指令 §3.2 一致）：
- 年末折现，首期为 t=1；
- 有限期现值 = Σ CF_t / (1+r)^t；
- 若启用永续增长终值：TV_N = CF_(N+1) / (r-g)，总现值再折 TV_N / (1+r)^N；
- 启用该终值必须 r > g，边界非法报明确错误；
- 现金流可为负，绝不为了「让模型通过」把负值截成零；
- 所有现金流入参必须是有限数值。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from engine.valuation import errors as e
from engine.valuation.errors import ValuationError


def _require_finite(value: float, *, code: str, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValuationError(code, f"{field} 必须是数值，实际 {value!r}")
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):
        raise ValuationError(code, f"{field} 必须有限，实际 {value!r}")
    return out


def check_rate(rate: float, *, field: str = "rate") -> float:
    """折现率必须有限且大于 -100%（负数现金流下 -100% 以下无经济意义）。"""
    value = _require_finite(rate, code=e.RATE_NOT_FINITE, field=field)
    if value <= -1.0:
        raise ValuationError(e.RATE_OUT_OF_RANGE, f"{field} 必须大于 -100%，实际 {value!r}")
    return value


def check_series(cash_flows: Sequence[float], *, field: str = "cash_flow") -> list[float]:
    if not cash_flows:
        raise ValuationError(e.EMPTY_CASH_FLOW_SERIES, f"{field} 序列为空")
    return [
        _require_finite(cf, code=e.CASH_FLOW_NOT_FINITE, field=f"{field}[{i}]")
        for i, cf in enumerate(cash_flows)
    ]


def discount_factor(rate: float, period: int) -> float:
    """第 period 期的折现因子 1/(1+r)^period（period 从 1 开始）。"""
    rate = check_rate(rate)
    if not isinstance(period, int) or period < 0:
        raise ValuationError(e.HORIZON_INVALID, f"period 必须是非负整数，实际 {period!r}")
    return 1.0 / (1.0 + rate) ** period


def present_value(cash_flows: Sequence[float], rate: float) -> float:
    """有限期现值，首期 t=1。"""
    rate = check_rate(rate)
    flows = check_series(cash_flows)
    total = 0.0
    for index, flow in enumerate(flows, start=1):
        total += flow / (1.0 + rate) ** index
    return total


@dataclass(frozen=True)
class PeriodValue:
    """带期号的分期现值明细，便于报告逐期展示。"""

    period: int
    cash_flow: float
    discount_factor: float
    present_value: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "period": self.period,
            "cash_flow": self.cash_flow,
            "discount_factor": self.discount_factor,
            "present_value": self.present_value,
        }


def present_value_schedule(cash_flows: Sequence[float], rate: float) -> list[PeriodValue]:
    rate = check_rate(rate)
    flows = check_series(cash_flows)
    schedule: list[PeriodValue] = []
    for index, flow in enumerate(flows, start=1):
        factor = 1.0 / (1.0 + rate) ** index
        schedule.append(
            PeriodValue(
                period=index, cash_flow=flow, discount_factor=factor, present_value=flow * factor
            )
        )
    return schedule


def annuity_present_value(payment: float, rate: float, periods: int) -> float:
    """等额年金现值（首期 t=1）：payment × [1-(1+r)^-N]/r。"""
    payment = _require_finite(payment, code=e.CASH_FLOW_NOT_FINITE, field="payment")
    rate = check_rate(rate)
    if not isinstance(periods, int) or periods <= 0:
        raise ValuationError(e.HORIZON_INVALID, f"periods 必须是正整数，实际 {periods!r}")
    if rate == 0.0:
        return payment * periods
    return payment * (1.0 - (1.0 + rate) ** -periods) / rate


def perpetuity_present_value(payment: float, rate: float) -> float:
    """永续年金现值：payment / r（g=0 的退化情形）。"""
    payment = _require_finite(payment, code=e.CASH_FLOW_NOT_FINITE, field="payment")
    rate = check_rate(rate)
    if rate <= 0.0:
        raise ValuationError(
            e.RATE_OUT_OF_RANGE, f"零增长永续要求折现率大于 0，实际 {rate!r}"
        )
    return payment / rate


def growing_perpetuity_present_value(
    next_period_cash_flow: float, rate: float, growth: float
) -> float:
    """增长型永续现值：CF_(N+1) / (r - g)，要求 r > g。"""
    flow = _require_finite(
        next_period_cash_flow, code=e.CASH_FLOW_NOT_FINITE, field="next_period_cash_flow"
    )
    rate = check_rate(rate)
    growth_value = _require_finite(growth, code=e.GROWTH_NOT_FINITE, field="growth")
    if rate <= growth_value:
        raise ValuationError(
            e.RATE_NOT_GREATER_THAN_GROWTH,
            f"增长型永续要求 r > g，实际 r={rate!r}，g={growth_value!r}",
        )
    return flow / (rate - growth_value)


@dataclass(frozen=True)
class TerminalValue:
    """终值的显式口径：必须说明 CF_(N+1) 从哪来。"""

    method: str  # growing_perpetuity / zero_growth_perpetuity
    growth: float
    next_period_cash_flow: float
    basis: str  # 文字说明：终值现金流的来源与假设
    terminal_year: int

    def value(self, rate: float) -> float:
        if self.method == "growing_perpetuity":
            return growing_perpetuity_present_value(
                self.next_period_cash_flow, rate, self.growth
            )
        if self.method == "zero_growth_perpetuity":
            if self.growth != 0.0:
                raise ValuationError(
                    e.TERMINAL_BASIS_MISSING,
                    "zero_growth_perpetuity 的 growth 必须为 0",
                )
            return perpetuity_present_value(self.next_period_cash_flow, rate)
        raise ValuationError(e.TERMINAL_BASIS_MISSING, f"未知的终值方法 {self.method!r}")

    def to_dict(self, rate: float) -> dict[str, object]:
        return {
            "method": self.method,
            "growth": self.growth,
            "next_period_cash_flow": self.next_period_cash_flow,
            "terminal_year": self.terminal_year,
            "basis": self.basis,
            "terminal_value": self.value(rate),
        }


@dataclass(frozen=True)
class DiscountedStream:
    """折现结果：分期明细 + 有限期现值 + 终值现值 + 合计。"""

    rate: float
    schedule: tuple[PeriodValue, ...]
    explicit_present_value: float
    terminal: TerminalValue | None
    terminal_present_value: float
    total_present_value: float

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "rate": self.rate,
            "explicit_present_value": self.explicit_present_value,
            "terminal_present_value": self.terminal_present_value,
            "terminal_share_of_total": (
                self.terminal_present_value / self.total_present_value
                if self.total_present_value
                else None
            ),
            "total_present_value": self.total_present_value,
            "schedule": [p.to_dict() for p in self.schedule],
        }
        if self.terminal is not None:
            payload["terminal"] = self.terminal.to_dict(self.rate)
        else:
            payload["terminal"] = None
        return payload


def discount_stream(
    cash_flows: Iterable[float],
    rate: float,
    *,
    terminal: TerminalValue | None = None,
    last_period_year: int | None = None,
) -> DiscountedStream:
    """折现一整条现金流序列，可选附加终值。

    终值的 CF_(N+1) 必须由调用方显式给出（TerminalValue.next_period_cash_flow），
    内核不会自动把末期现金流永续化。
    """
    rate = check_rate(rate)
    flows = check_series(list(cash_flows))
    schedule = tuple(present_value_schedule(flows, rate))
    explicit = sum(item.present_value for item in schedule)

    terminal_pv = 0.0
    if terminal is not None:
        if last_period_year is not None and terminal.terminal_year != last_period_year:
            raise ValuationError(
                e.TERMINAL_BASIS_MISSING,
                f"终值年份 {terminal.terminal_year} 与现金流末期 {last_period_year} 不一致",
            )
        terminal_pv = terminal.value(rate) / (1.0 + rate) ** len(flows)

    return DiscountedStream(
        rate=rate,
        schedule=schedule,
        explicit_present_value=explicit,
        terminal=terminal,
        terminal_present_value=terminal_pv,
        total_present_value=explicit + terminal_pv,
    )
