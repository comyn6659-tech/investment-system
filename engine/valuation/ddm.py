"""股息折现路线：预测每股可持续现金分配，用股权回报要求折现。

只做「归属于股东的直接分配」的现值，**不扣除企业净债务**——本模块的输入结构里
根本没有债务字段，因此重复扣减在类型层面不可能发生。

口径约定（施工指令 §3.2）：
- DPS_t = EPS_t × 派息率_t，EPS 与派息率都必须来自显式研究假设；
- 现金覆盖、盈利留存、再投资与预测增长必须互相解释：
  终值增长不得超过「ROE × 留存率」，超额分红不得搭配正终值增长；
- **超额分配不得自动永续化**：末期 DPS 超过当年每股收益时，终值必须换成
  显式声明的可持续分配（附依据），否则拒绝该终值——限制与终值增长的正负无关；
- 特别分红不进入可持续 DPS 序列（由 financials/dividends.py 在取数层剥离）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.valuation import errors as e
from engine.valuation.discounting import TerminalValue, discount_stream
from engine.valuation.errors import ValuationError

# 末期超额分配在终值里被拆成「可持续部分」时记录的问题码（非阻断，可审计）
EXCESS_DISTRIBUTION_TERMINAL_SPLIT = "EXCESS_DISTRIBUTION_TERMINAL_SPLIT"


@dataclass(frozen=True)
class DividendDrivers:
    base_year: int
    base_eps: float
    eps_growth: tuple[float, ...]
    payout: tuple[float, ...]
    cost_of_equity: float
    terminal_growth: float | None
    roe: float | None = None
    growth_consistency_tolerance: float = 0.0
    eps_is_ordinary: bool = True

    def __post_init__(self) -> None:
        horizon = len(self.eps_growth)
        if horizon <= 0:
            raise ValuationError(e.HORIZON_INVALID, "预测期必须至少 1 年")
        if len(self.payout) != horizon:
            raise ValuationError(
                e.HORIZON_INVALID,
                f"payout 长度 {len(self.payout)} 与预测期 {horizon} 不一致",
            )
        if self.base_eps <= 0:
            raise ValuationError(
                e.MODEL_INPUT_MISSING,
                f"基期每股收益必须为正，实际 {self.base_eps!r}；亏损期无法由盈利外推股息",
            )


@dataclass(frozen=True)
class DividendYear:
    year: int
    eps: float
    payout: float
    dps: float
    retained_eps: float

    def to_dict(self) -> dict[str, float | int]:
        return {
            "year": self.year,
            "eps": self.eps,
            "payout": self.payout,
            "dps": self.dps,
            "retained_eps": self.retained_eps,
        }


@dataclass(frozen=True)
class DividendIssue:
    """一致性问题：解释原因与模型影响，不用「一律拒绝」代替分析。"""

    code: str
    message: str
    blocking: bool

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "blocking": self.blocking}


def project_dividends(drivers: DividendDrivers) -> tuple[DividendYear, ...]:
    out: list[DividendYear] = []
    eps = float(drivers.base_eps)
    for index, growth in enumerate(drivers.eps_growth):
        eps = eps * (1.0 + growth)
        payout = drivers.payout[index]
        if payout < 0:
            raise ValuationError(
                e.MODEL_INPUT_MISSING, f"{drivers.base_year + 1 + index} 年派息率不能为负"
            )
        dps = eps * payout
        out.append(
            DividendYear(
                year=drivers.base_year + 1 + index,
                eps=eps,
                payout=payout,
                dps=dps,
                retained_eps=eps * (1.0 - payout),
            )
        )
    return tuple(out)


def check_growth_consistency(
    drivers: DividendDrivers,
    years: tuple[DividendYear, ...],
    *,
    terminal_growth: float | None = None,
    terminal_dps: float | None = None,
    terminal_basis: str | None = None,
) -> list[DividendIssue]:
    """盈利留存、超额分配与终值是否互相解释。

    规则顺序（先判增长，再判终值是否可支撑）：

    1. 终值增长 > ROE × 末期留存率（含容差）→ 增长没有资金来源，拒绝；
    2. 末期派息率 ≥ 100% 却要求正终值增长 → 拒绝；
    3. **末期 DPS 超过当年每股收益（超额分配）时，终值不得沿用该 DPS**：
       没有显式可持续终值就拒绝（`TERMINAL_DISTRIBUTION_UNFUNDED`），
       与终值增长是正是零无关；
    4. 显式给出可持续终值时，要求：附依据、不超过当年每股收益、且增长不超过
       该可持续派息率对应的留存能力；同时记录「超额分配已拆分」。
    """
    issues: list[DividendIssue] = []
    last = years[-1]
    growth = terminal_growth if terminal_growth is not None else drivers.terminal_growth
    excess = last.dps > last.eps + 1e-9

    if last.payout > 1.0 + 1e-9:
        issues.append(
            DividendIssue(
                code=e.DISTRIBUTION_NOT_COVERED_BY_EARNINGS,
                message=(
                    f"{last.year} 年派息率 {last.payout:.2%} 超过当年每股收益，"
                    "属于超额分配：需要以存量现金或新增融资支撑"
                ),
                blocking=False,
            )
        )

    # 终值的可持续派息率：显式可持续 DPS 优先，否则沿用末期派息率
    if terminal_dps is not None:
        if growth is None:
            raise ValuationError(
                e.TERMINAL_BASIS_MISSING,
                "未启用终值却给出了可持续终值分配：两者不能同时成立",
            )
        if not (terminal_basis or "").strip():
            raise ValuationError(
                e.TERMINAL_BASIS_MISSING,
                "显式给出可持续终值分配时必须说明资金来源与口径",
            )
        if last.eps <= 0:
            raise ValuationError(e.MODEL_INPUT_MISSING, "末期每股收益非正，无法校验可持续终值分配")
        if terminal_dps > last.eps + 1e-9:
            raise ValuationError(
                e.TERMINAL_DISTRIBUTION_UNFUNDED,
                f"声明的可持续终值分配 {terminal_dps:.4f} 超过末期每股收益 {last.eps:.4f}："
                "超过盈利的分配不能作为永续首期",
            )
        effective_payout = terminal_dps / last.eps
        if excess:
            issues.append(
                DividendIssue(
                    code=EXCESS_DISTRIBUTION_TERMINAL_SPLIT,
                    message=(
                        f"末期每股分配 {last.dps:.4f} 超过每股收益 {last.eps:.4f}，"
                        f"终值改用显式可持续分配 {terminal_dps:.4f}（依据：{terminal_basis}）；"
                        "超额部分只计入明确预测期，不进入永续项"
                    ),
                    blocking=False,
                )
            )
    else:
        effective_payout = last.payout

    if growth is not None and growth > 0:
        if effective_payout >= 1.0:
            raise ValuationError(
                e.GROWTH_NOT_FUNDED_BY_RETENTION,
                f"末期派息率 {effective_payout:.2%} 没有留存收益，却假设终值增长 {growth:.2%}",
            )
        if drivers.roe is not None:
            sustainable = drivers.roe * (1.0 - effective_payout)
            if growth > sustainable + drivers.growth_consistency_tolerance:
                raise ValuationError(
                    e.GROWTH_NOT_FUNDED_BY_RETENTION,
                    f"终值增长 {growth:.2%} 超过可持续增长 {sustainable:.2%}"
                    f"（ROE {drivers.roe:.2%} × 留存率 {1 - effective_payout:.2%}）",
                )
        elif last.retained_eps <= 0:
            raise ValuationError(
                e.GROWTH_NOT_FUNDED_BY_RETENTION, "没有留存收益却假设正终值增长"
            )

    # 启用终值但末期分配超过盈利：没有显式可持续终值就拒绝
    if growth is not None and excess and terminal_dps is None:
        raise ValuationError(
            e.TERMINAL_DISTRIBUTION_UNFUNDED,
            (
                f"末期每股分配 {last.dps:.4f} 超过当年每股收益 {last.eps:.4f}，"
                "但本次要求使用永续终值：没有持续资金来源的证据，不能把超额分配无限延续。"
                "若确有资金支持，请显式给出可持续终值分配（sustainable_terminal_dps）及其依据；"
                "否则应改为不启用终值，只计算明确预测期。"
            ),
        )
    return issues


@dataclass(frozen=True)
class DividendValuation:
    years: tuple[DividendYear, ...]
    stream: object
    value_per_share: float
    issues: tuple[DividendIssue, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "model": "dividend_discount",
            "years": [y.to_dict() for y in self.years],
            "discounting": self.stream.to_dict(),
            "value_per_share": self.value_per_share,
            "issues": [i.to_dict() for i in self.issues],
        }


def value_dividend(
    drivers: DividendDrivers,
    *,
    terminal_basis: str | None = None,
    terminal_dps: float | None = None,
    issues: list[DividendIssue] | None = None,
) -> DividendValuation:
    years = project_dividends(drivers)
    found = list(issues or [])
    found.extend(
        check_growth_consistency(
            drivers,
            years,
            terminal_growth=drivers.terminal_growth,
            terminal_dps=terminal_dps,
            terminal_basis=terminal_basis,
        )
    )

    terminal: TerminalValue | None = None
    if drivers.terminal_growth is not None:
        growth = drivers.terminal_growth
        next_dps = terminal_dps if terminal_dps is not None else years[-1].dps * (1.0 + growth)
        if next_dps <= 0:
            raise ValuationError(
                e.TERMINAL_BASIS_MISSING,
                f"终值期每股分配 {next_dps:.4f} 不为正，不能用永续增长终值",
            )
        if terminal_dps is not None and not terminal_basis:
            raise ValuationError(
                e.TERMINAL_BASIS_MISSING, "显式给出终值 DPS 时必须说明其口径来源"
            )
        terminal = TerminalValue(
            method="zero_growth_perpetuity" if growth == 0.0 else "growing_perpetuity",
            growth=growth,
            next_period_cash_flow=next_dps,
            basis=terminal_basis or "末期每股分配按终值增长率外推",
            terminal_year=years[-1].year,
        )

    stream = discount_stream(
        [y.dps for y in years],
        drivers.cost_of_equity,
        terminal=terminal,
        last_period_year=years[-1].year,
    )
    return DividendValuation(
        years=years,
        stream=stream,
        value_per_share=stream.total_present_value,
        issues=tuple(found),
    )
