"""候选 A 的状态机：分位 ≤20 入场、≥60 退出、满 12 个日历月到期退出。

冻结规则（施工单 §3.3）：
- 空仓 + 分位 ≤20 → 入场信号；持仓 + 分位 ≥60 → 退出信号；
- 持有满 12 个日历月仍未退出 → 在下一个可执行时点退出，记 `expiry_exit`；
- 同一观察点先判断退出；退出后不在同一观察点重新入场；
- 信号在观察点收盘后形成，按下一交易日收盘成交（主口径 T+1，T+2 敏感性）；
- 观察点不可评估：空仓不入场；持仓保持并记 `signal_not_evaluable`，到期规则照常；
- 仓位只有 0/1，只做多，不加杠杆。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

EXIT_SIGNAL = "signal_exit"
EXIT_EXPIRY = "expiry_exit"
EXIT_END = "end_of_sample"


def add_months(day: str, months: int) -> str:
    value = date.fromisoformat(day[:10])
    total = value.year * 12 + (value.month - 1) + months
    year, month = total // 12, total % 12 + 1
    # 月末对齐：目标月没有该日时取月末
    import calendar

    day_in_month = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day_in_month).isoformat()


@dataclass(frozen=True)
class Trade:
    entry_signal_date: str
    entry_exec_date: str
    entry_price: float
    entry_percentile: float | None
    exit_signal_date: str | None
    exit_exec_date: str
    exit_price: float
    exit_percentile: float | None
    exit_reason: str
    holding_days: int
    gross_return: float
    net_return: float
    cost_bp: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_signal_date": self.entry_signal_date,
            "entry_exec_date": self.entry_exec_date,
            "entry_price": self.entry_price,
            "entry_percentile": self.entry_percentile,
            "exit_signal_date": self.exit_signal_date,
            "exit_exec_date": self.exit_exec_date,
            "exit_price": self.exit_price,
            "exit_percentile": self.exit_percentile,
            "exit_reason": self.exit_reason,
            "holding_days": self.holding_days,
            "gross_return": self.gross_return,
            "net_return": self.net_return,
            "cost_bp": self.cost_bp,
        }


@dataclass(frozen=True)
class Decision:
    date: str
    week: str
    percentile: float | None
    evaluable: bool
    action: str  # none / entry_signal / exit_signal / expiry_signal / not_evaluable_holding
    exec_date: str | None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "week": self.week,
            "percentile": self.percentile,
            "evaluable": self.evaluable,
            "action": self.action,
            "exec_date": self.exec_date,
            "note": self.note,
        }


def gross_price_factor(exit_price: float, entry_price: float, cost: float) -> float:
    """含成本的买卖价格比：(exit/entry) × (1-c)²。

    买入与卖出各扣一次单边成本，乘性扣除，不做二次重复扣费；
    权益曲线用同一口径，因此「逐笔净收益连乘」与「权益曲线终值」必须一致。
    """
    return (exit_price / entry_price) * (1.0 - cost) ** 2


def _execution_price(
    calendar: Sequence[str], prices: dict[str, float], signal_date: str, lag: int
) -> tuple[str, float] | None:
    """信号日在日历中的位置 + lag 个交易日；越界返回 None。"""
    try:
        index = calendar.index(signal_date)
    except ValueError:
        return None
    target = index + lag
    if target >= len(calendar):
        return None
    exec_date = calendar[target]
    price = prices.get(exec_date)
    if price is None:
        return None
    return exec_date, price


def run_state_machine(
    observations: Sequence[dict[str, Any]],
    *,
    calendar: Sequence[str],
    prices: dict[str, float],
    lag: int = 1,
    cost_bp: float = 0.0,
    entry_threshold: float = 20.0,
    exit_threshold: float = 60.0,
    max_hold_months: int = 12,
    sample_end: str | None = None,
) -> dict[str, Any]:
    """跑一遍 0/1 状态机，返回成交列表与决策日志。

    `observations` 需按日期升序，元素含 `date`、`week`、`percentile`、`evaluable`。
    `prices` 是执行口径的价格表（全收益或价格指数），`calendar` 是交易日序列。
    """
    holding: dict[str, Any] | None = None
    trades: list[Trade] = []
    decisions: list[Decision] = []
    cost = cost_bp / 10000.0

    for obs in observations:
        day = obs["date"]
        pct = obs.get("percentile")
        evaluable = bool(obs.get("evaluable"))
        action = "none"
        exec_date: str | None = None
        note = ""

        # 1) 持仓：先判断退出（信号 ≥60，或持有满 12 个日历月）
        if holding is not None:
            exit_reason = None
            if evaluable and pct is not None and pct >= exit_threshold:
                exit_reason = EXIT_SIGNAL
            elif day >= add_months(holding["entry_exec_date"], max_hold_months):
                exit_reason = EXIT_EXPIRY
            elif not evaluable:
                action = "not_evaluable_holding"
                note = "观察点不可评估：保持持仓，到期规则照常"

            if exit_reason is not None:
                executed = _execution_price(calendar, prices, day, lag)
                if executed is None:
                    # 样本末尾没有可执行时点：按最后一个交易日收盘平掉
                    last = sample_end or calendar[-1]
                    if last in prices:
                        executed = (last, prices[last])
                        note = "样本结束前没有 T+lag 执行日，按最后交易日收盘平仓"
                        exit_reason = EXIT_END if exit_reason == EXIT_SIGNAL else exit_reason
                if executed is not None:
                    exec_date, exit_price = executed
                    entry_price = holding["entry_price"]
                    gross = exit_price / entry_price - 1.0
                    # 单边成本在买入与卖出各扣一次：乘性 (1-c)^2，与权益曲线口径一致
                    net = gross_price_factor(exit_price, entry_price, cost) - 1.0
                    trades.append(
                        Trade(
                            entry_signal_date=holding["signal_date"],
                            entry_exec_date=holding["entry_exec_date"],
                            entry_price=entry_price,
                            entry_percentile=holding["percentile"],
                            exit_signal_date=day,
                            exit_exec_date=exec_date,
                            exit_price=exit_price,
                            exit_percentile=pct,
                            exit_reason=exit_reason,
                            holding_days=(
                                date.fromisoformat(exec_date)
                                - date.fromisoformat(holding["entry_exec_date"])
                            ).days,
                            gross_return=gross,
                            net_return=net,
                            cost_bp=cost_bp,
                        )
                    )
                    decisions.append(
                        Decision(day, obs["week"], pct, evaluable, "exit_signal", exec_date, note)
                    )
                    holding = None
                    # 同一观察点不再重新入场
                    continue
                else:
                    action = "exit_signal_no_execution"
                    note = "有退出信号但找不到可执行的价格"

        # 2) 空仓：分位 ≤20 才产生入场信号
        if holding is None and action == "none":
            if evaluable and pct is not None and pct <= entry_threshold:
                executed = _execution_price(calendar, prices, day, lag)
                if executed is not None:
                    exec_date, entry_price = executed
                    holding = {
                        "signal_date": day,
                        "entry_exec_date": exec_date,
                        "entry_price": entry_price,
                        "percentile": pct,
                    }
                    action = "entry_signal"
                else:
                    action = "entry_signal_no_execution"
                    note = "有入场信号但样本末尾没有可执行的价格"

        decisions.append(Decision(day, obs["week"], pct, evaluable, action, exec_date, note))

    # 样本结束时仍持仓：按最后交易日收盘平掉，单独标记
    if holding is not None:
        last = sample_end or calendar[-1]
        exit_price = prices.get(last)
        if exit_price is not None:
            trades.append(
                Trade(
                    entry_signal_date=holding["signal_date"],
                    entry_exec_date=holding["entry_exec_date"],
                    entry_price=holding["entry_price"],
                    entry_percentile=holding["percentile"],
                    exit_signal_date=None,
                    exit_exec_date=last,
                    exit_price=exit_price,
                    exit_percentile=None,
                    exit_reason=EXIT_END,
                    holding_days=(
                        date.fromisoformat(last) - date.fromisoformat(holding["entry_exec_date"])
                    ).days,
                    gross_return=exit_price / holding["entry_price"] - 1.0,
                    net_return=gross_price_factor(exit_price, holding["entry_price"], cost) - 1.0,
                    cost_bp=cost_bp,
                )
            )

    return {
        "trades": [t.to_dict() for t in trades],
        "decisions": [d.to_dict() for d in decisions],
        "open_position_at_end": holding is not None,
    }


__all__ = [
    "Decision",
    "EXIT_END",
    "EXIT_EXPIRY",
    "EXIT_SIGNAL",
    "Trade",
    "add_months",
    "gross_price_factor",
    "run_state_machine",
]
