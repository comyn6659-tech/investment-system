"""财务报告期的口径处理。

原始报表是**年初至报告期末的累计数**。这里只做三件事：

1. 识别报告期类型（一季 / 半年 / 三季 / 年报）；
2. 在口径可比的前提下推导 TTM：`最新累计 + 上年年报 − 上年同期累计`；
3. 明确拒绝「把季报乘系数当年报」这类强行年化。

缺少必要比较期时，一律返回失败并说明缺哪一期，不猜、不插值、不年化。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from engine.valuation import errors as e
from engine.valuation.errors import ValuationError


class PeriodKind(str):
    """报告期类型（用字符串常量，便于直接写进 JSON）。"""


Q1 = "q1"
H1 = "h1"
Q3 = "q3"
ANNUAL = "annual"

_QUARTER_BY_MONTH_DAY = {
    (3, 31): Q1,
    (6, 30): H1,
    (9, 30): Q3,
    (12, 31): ANNUAL,
}


@dataclass(frozen=True, order=True)
class FiscalPeriod:
    period_end: str  # YYYY-MM-DD
    fiscal_year: int
    kind: str

    @property
    def month_day(self) -> tuple[int, int]:
        day = date.fromisoformat(self.period_end)
        return (day.month, day.day)

    def to_dict(self) -> dict[str, object]:
        return {
            "period_end": self.period_end,
            "fiscal_year": self.fiscal_year,
            "kind": self.kind,
        }


def parse_period(period_end: str) -> FiscalPeriod:
    """把 YYYY-MM-DD 解析为报告期；不认识的月末日期直接拒绝。"""
    try:
        day = date.fromisoformat(str(period_end)[:10])
    except ValueError as exc:
        raise ValuationError(e.MODEL_INPUT_MISSING, f"无法解析报告期 {period_end!r}") from exc
    kind = _QUARTER_BY_MONTH_DAY.get((day.month, day.day))
    if kind is None:
        raise ValuationError(
            e.MODEL_INPUT_MISSING,
            f"报告期 {period_end!r} 不是标准的季末/半年末/年末日期，口径无法确认",
        )
    return FiscalPeriod(period_end=day.isoformat(), fiscal_year=day.year, kind=kind)


def annual_periods(period_ends: list[str]) -> list[FiscalPeriod]:
    out = [parse_period(p) for p in period_ends]
    return sorted([p for p in out if p.kind == ANNUAL], reverse=True)


def single_quarter_value(
    cumulative: dict[str, float], period_end: str
) -> float:
    """把累计数拆成单季数；缺少可减项时拒绝，不用比例估算。

    - 一季：本身就是单季；
    - 半年：H1 − Q1；
    - 三季：Q3 累计 − H1 累计；
    - 年报：全年 − 三季累计。
    """
    period = parse_period(period_end)
    if period.kind == Q1:
        if period.period_end not in cumulative:
            raise ValuationError(e.MODEL_INPUT_MISSING, f"缺少 {period.period_end} 累计数")
        return cumulative[period.period_end]

    if period.kind == H1:
        base = f"{period.fiscal_year}-03-31"
    elif period.kind == Q3:
        base = f"{period.fiscal_year}-06-30"
    else:
        base = f"{period.fiscal_year}-09-30"

    if period.period_end not in cumulative:
        raise ValuationError(e.MODEL_INPUT_MISSING, f"缺少 {period.period_end} 累计数")
    if base not in cumulative:
        raise ValuationError(
            e.MODEL_INPUT_MISSING,
            f"缺少 {base} 累计数，无法拆分 {period.period_end} 的单季数（不做比例估算）",
        )
    return cumulative[period.period_end] - cumulative[base]


def ttm_value(cumulative: dict[str, float], period_end: str) -> tuple[float, dict[str, object]]:
    """滚动十二个月：最新累计 + 上年年报 − 上年同期累计。

    返回 (值, 计算依据)。缺任一分量就拒绝，绝不把半年报乘 2 当年报。
    """
    period = parse_period(period_end)
    if period.kind == ANNUAL:
        if period.period_end not in cumulative:
            raise ValuationError(e.MODEL_INPUT_MISSING, f"缺少 {period.period_end} 年报数")
        return cumulative[period.period_end], {
            "method": "annual_period",
            "formula": "年报累计数本身即为 12 个月",
            "components": {"annual": period.period_end},
        }

    prior_annual = f"{period.fiscal_year - 1}-12-31"
    prior_same = f"{period.fiscal_year - 1}-{period.period_end[5:]}"
    missing = [p for p in (period.period_end, prior_annual, prior_same) if p not in cumulative]
    if missing:
        raise ValuationError(
            e.MODEL_INPUT_MISSING,
            f"缺少可比期间 {missing}，不能强行年化（拒绝把累计数乘系数）",
        )
    value = cumulative[period.period_end] + cumulative[prior_annual] - cumulative[prior_same]
    return value, {
        "method": "ttm_additive",
        "formula": "最新累计 + 上年年报 − 上年同期累计",
        "components": {
            "latest_cumulative": period.period_end,
            "prior_annual": prior_annual,
            "prior_same_period": prior_same,
        },
        "note": "三项均为年初至期末累计数，口径一致；未做任何比例放大",
    }


def reject_annualisation(period_end: str) -> None:
    """显式拒绝「季报×4」这类年化。"""
    period = parse_period(period_end)
    if period.kind != ANNUAL:
        raise ValuationError(
            e.MODEL_INPUT_MISSING,
            f"{period_end} 是 {period.kind} 累计数，缺少完整年度不可强行年化；"
            "请改用 TTM（需要上年年报与上年同期累计）",
        )
