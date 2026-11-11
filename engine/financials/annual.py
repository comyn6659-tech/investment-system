"""从快照事实整理出「按财年」的财务画像，供桥接与模型使用。

只取年报（12-31）作为年度基准：季度累计数不在此处年化，需要 TTM 的地方走
`financials/periods.py`。每个字段都保留状态与来源，缺失就是缺失。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.contracts.enums import DataStatus
from engine.data.facts import Fact
from engine.data.snapshot import Snapshot
from engine.financials.periods import ANNUAL, parse_period

# 年度画像需要的字段 → 事实后缀
_ANNUAL_FIELDS = (
    "revenue",
    "operating_profit",
    "pretax_profit",
    "income_tax",
    "net_profit",
    "net_profit_parent",
    "interest_expense",
    "finance_expense",
    "ocf",
    "capex",
    "depreciation",
    "amortization_intangible",
    "amortization_lt_prepaid",
    "amortization_right_of_use",
    "inventory_decrease",
    "operating_receivables_decrease",
    "operating_payables_add",
    "deferred_tax",
    "cash",
    "short_loan",
    "long_loan",
    "bond_payable",
    "current_portion_lt_debt",
    "lease_liab",
    "minority_equity",
    "equity_parent",
    "total_assets",
    "total_liabilities",
    "inventory",
    "receivables",
    "receivables_notes",
    "payables",
    "payables_notes",
    "share_capital",
    "eps_basic",
    "loan_advance",
)


@dataclass(frozen=True)
class AnnualFinancials:
    fiscal_year: int
    period_end: str
    values: dict[str, Fact]
    notice_date: str | None = None

    def fact(self, name: str) -> Fact | None:
        return self.values.get(name)

    def number(self, name: str) -> float | None:
        fact = self.values.get(name)
        if fact is None or not fact.is_usable_number:
            return None
        return float(fact.value)

    def require(self, name: str, *, model: str) -> float:
        value = self.number(name)
        if value is None:
            from engine.valuation import errors as e
            from engine.valuation.errors import ValuationError

            fact = self.values.get(name)
            status = fact.status.value if fact else "absent"
            raise ValuationError(
                e.MODEL_INPUT_MISSING,
                f"{model} 需要 {self.period_end} 的 {name}，当前状态 {status}",
            )
        return value

    def status(self, name: str) -> str:
        fact = self.values.get(name)
        return fact.status.value if fact else "absent"

    def to_dict(self) -> dict[str, object]:
        return {
            "fiscal_year": self.fiscal_year,
            "period_end": self.period_end,
            "notice_date": self.notice_date,
            "values": {
                name: {
                    "value": fact.value,
                    "status": fact.status.value,
                    "unit": fact.unit,
                    "source_id": fact.source.source_id if fact.source else None,
                }
                for name, fact in sorted(self.values.items())
            },
        }


@dataclass(frozen=True)
class FinancialHistory:
    instrument_id: str
    years: tuple[AnnualFinancials, ...]
    interim_periods: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def year(self, fiscal_year: int) -> AnnualFinancials | None:
        for entry in self.years:
            if entry.fiscal_year == fiscal_year:
                return entry
        return None

    @property
    def latest(self) -> AnnualFinancials | None:
        return self.years[0] if self.years else None

    def to_dict(self) -> dict[str, object]:
        return {
            "instrument_id": self.instrument_id,
            "annual_years": [y.to_dict() for y in self.years],
            "interim_periods": list(self.interim_periods),
            "notes": list(self.notes),
        }


def load_annual_history(snapshot: Snapshot) -> FinancialHistory:
    """按报告期分组，仅保留年报；同时记录非年报期以便说明「没有强行年化」。"""
    grouped: dict[str, dict[str, Fact]] = {}
    notices: dict[str, str] = {}
    for fact in snapshot.facts:
        if not fact.fact_id.startswith("fin."):
            continue
        parts = fact.fact_id.split(".")
        if len(parts) < 3:
            continue
        period_end, suffix = parts[1], parts[2]
        if len(period_end) != 10:
            continue
        grouped.setdefault(period_end, {})[suffix] = fact
        if suffix == "notice_date" and isinstance(fact.value, str):
            notices[period_end] = fact.value

    annual: list[AnnualFinancials] = []
    interim: list[str] = []
    for period_end, facts in grouped.items():
        try:
            period = parse_period(period_end)
        except Exception:  # 非标准期末日期：不进入年报画像
            continue
        if period.kind != ANNUAL:
            interim.append(period_end)
            continue
        values = {name: facts[name] for name in _ANNUAL_FIELDS if name in facts}
        annual.append(
            AnnualFinancials(
                fiscal_year=period.fiscal_year,
                period_end=period_end,
                values=values,
                notice_date=notices.get(period_end),
            )
        )
    annual.sort(key=lambda y: y.fiscal_year, reverse=True)
    notes = []
    if interim:
        notes.append(
            f"快照中另有 {len(interim)} 个非年报报告期（{', '.join(sorted(interim, reverse=True)[:4])}…），"
            "这些累计数未在年报画像中使用，需要时按 TTM 规则转换"
        )
    return FinancialHistory(
        instrument_id=snapshot.instrument_id,
        years=tuple(annual),
        interim_periods=tuple(sorted(interim, reverse=True)),
        notes=tuple(notes),
    )


# --- 分红记录适配 -----------------------------------------------------------
def load_dividend_records(snapshot: Snapshot) -> list[Any]:
    """把快照里 `div.*.per_10` 事实转换成 DividendRecord。"""
    from engine.financials.dividends import DividendRecord

    records: list[DividendRecord] = []
    for fact in snapshot.facts:
        if not fact.fact_id.startswith("div.") or not fact.fact_id.endswith(".per_10"):
            continue
        if fact.status not in (DataStatus.KNOWN, DataStatus.KNOWN_ZERO):
            continue
        base = fact.fact_id[: -len(".per_10")]
        segments = base.split(".")
        # div.<slot>.<kind>[#announce]
        if len(segments) < 3:
            continue
        slot = segments[1]
        kind = ".".join(segments[2:])
        if "#" in kind:
            kind = kind.split("#", 1)[0]
        fiscal_label = slot if any(tag in slot for tag in ("年报", "半年报", "季报")) else None
        status_fact = snapshot.fact(f"{base}.status")
        ex_fact = snapshot.fact(f"{base}.ex_date")
        records.append(
            DividendRecord(
                fact_id=fact.fact_id,
                fiscal_year=None,
                fiscal_label=fiscal_label,
                kind=kind,
                per_10=float(fact.value),
                ex_date=str(ex_fact.value) if ex_fact and ex_fact.value else None,
                announce_date=fact.available_at,
                status=str(status_fact.value) if status_fact and status_fact.value else None,
                source_id=fact.source.source_id if fact.source else None,
            )
        )
    return records


__all__ = [
    "AnnualFinancials",
    "FinancialHistory",
    "load_annual_history",
    "load_dividend_records",
]
