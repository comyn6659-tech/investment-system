"""分红明细的归集规则。

必须区分（施工指令 §3.2）：
- 所属财年 vs 实施年度：按「报告时间」归集财年，按「除权日」判断实际支付；
- 中期/季度分配 vs 年度末期分配：同一财年内相加才是该财年的合计；
- 预案 / 批准 / 实施：只有已实施（或已除权）才计入「已支付」；
- 普通分配 vs 特别分配：特别分红**不进入**可持续 DPS 序列；
- 每 10 股 vs 每股：统一换算为每股；
- 「过去十二个月实际支付」与「某财年对应分配」是两个不同指标，禁止混称。

归集顺序：先判类型（普通/特别/其他），再判财年。因此「特别分红但没有标注财年」
不会掉进「未归类普通分红」，而是单列在 `unlabeled_special`。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from engine.valuation import errors as e
from engine.valuation.errors import ValuationError

# 普通现金分配的类型白名单（巨潮『分红类型』）
ORDINARY_KINDS = ("年度分红", "中期分红", "季度分红", "分红")
SPECIAL_KINDS = ("特别分红", "特别分配")
# 已实施的状态写法（新浪『进度』与巨潮说明）
IMPLEMENTED_STATUSES = ("实施", "已实施", "实施方案", "已除权")


@dataclass(frozen=True)
class DividendRecord:
    fact_id: str
    fiscal_year: int | None
    fiscal_label: str | None  # 例如 2025年报 / 2025半年报 / 2025三季报
    kind: str  # 年度分红 / 中期分红 / 季度分红 / 特别分红 / 股改分红
    per_10: float  # 每 10 股派息（税前，元）
    ex_date: str | None
    announce_date: str | None
    status: str | None
    source_id: str | None = None

    @property
    def per_share(self) -> float:
        return self.per_10 / 10.0

    @property
    def is_special(self) -> bool:
        return any(key in (self.kind or "") for key in SPECIAL_KINDS)

    @property
    def is_ordinary(self) -> bool:
        """只有白名单里的类型才是可持续的普通现金分配。"""
        return (self.kind or "") in ORDINARY_KINDS

    @property
    def is_annual_final(self) -> bool:
        """年度末期分配（区别于中期/季度分配）。"""
        return (self.kind or "") == "年度分红"

    @property
    def is_implemented(self) -> bool:
        return bool(self.status) and any(key in self.status for key in IMPLEMENTED_STATUSES)

    def implemented_basis(self, as_of: str) -> str | None:
        """这条记录是否已实施、依据是什么。

        - 来源明确写「实施」→ implemented_status；
        - 未取到状态，但除权日已过（除权日 ≤ 分析日）→ ex_date_passed；
        - 其余视为未实施（预案/未取得且未除权）。
        除权日已过是强证据：除权即分配已执行，不需要依赖文本状态。
        """
        if self.is_implemented:
            return "implemented_status"
        if self.ex_date and self.ex_date <= as_of:
            return "ex_date_passed"
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "fact_id": self.fact_id,
            "fiscal_year": self.fiscal_year,
            "fiscal_label": self.fiscal_label,
            "kind": self.kind,
            "per_10_shares": self.per_10,
            "per_share": self.per_share,
            "ex_date": self.ex_date,
            "announce_date": self.announce_date,
            "status": self.status,
            "is_special": self.is_special,
            "is_implemented": self.is_implemented,
            "source_id": self.source_id,
        }


@dataclass(frozen=True)
class FiscalYearDistribution:
    fiscal_year: int
    ordinary_per_share: float
    special_per_share: float
    has_annual_final: bool
    components: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "fiscal_year": self.fiscal_year,
            "ordinary_dps": self.ordinary_per_share,
            "special_dps": self.special_per_share,
            "has_annual_final": self.has_annual_final,
            "components": list(self.components),
        }


@dataclass(frozen=True)
class DividendBook:
    """分红账本：财年归集、单列项、LTM 实付、缺口说明。"""

    years: tuple[FiscalYearDistribution, ...]
    unlabeled_special: tuple[DividendRecord, ...]
    unlabeled_ordinary: tuple[DividendRecord, ...]
    excluded_not_implemented: tuple[DividendRecord, ...]
    excluded_other_kinds: tuple[DividendRecord, ...]
    ltm_paid_per_share: float | None
    ltm_window: tuple[str, str] | None
    notes: tuple[str, ...] = ()

    def year(self, fiscal_year: int) -> FiscalYearDistribution | None:
        for entry in self.years:
            if entry.fiscal_year == fiscal_year:
                return entry
        return None

    def latest_complete_year(self) -> int | None:
        """只有含「年度分红」的财年才算完整；只有中期分配的财年不算。"""
        complete = [
            y.fiscal_year
            for y in self.years
            if y.has_annual_final and y.ordinary_per_share > 0
        ]
        return max(complete) if complete else None

    def to_dict(self) -> dict[str, object]:
        return {
            "fiscal_years": [y.to_dict() for y in self.years],
            "unlabeled_special": [r.to_dict() for r in self.unlabeled_special],
            "unlabeled_ordinary": [r.to_dict() for r in self.unlabeled_ordinary],
            "excluded_not_implemented": [r.to_dict() for r in self.excluded_not_implemented],
            "excluded_other_kinds": [r.to_dict() for r in self.excluded_other_kinds],
            "ltm_paid_per_share": self.ltm_paid_per_share,
            "ltm_window": list(self.ltm_window) if self.ltm_window else None,
            "notes": list(self.notes),
        }


def par_value(history: Any, base_year: int) -> float | None:
    """由基准年「股本（元）÷（归母净利润 ÷ 基本每股收益）」推出每股面值。

    送转改变股本金额，但面值不变；用最近一年推面值，再用各年期末股本 ÷
    面值还原该年股数，避免送转后用当前股本计算历史派息率（R10）。
    """
    annual = history.year(base_year)
    if annual is None:
        return None
    capital = annual.number("share_capital")
    profit = annual.number("net_profit_parent")
    eps = annual.number("eps_basic")
    if capital is None or capital <= 0 or profit is None or profit <= 0:
        return None
    if eps is None or eps <= 0:
        return None
    implied = profit / eps
    if implied <= 0:
        return None
    return capital / implied


def year_shares(annual: Any, par: float | None) -> float | None:
    """某财年期末股数：期末股本 ÷ 面值；缺股本时退回归母净利润 ÷ 基本每股收益。"""
    if annual is None:
        return None
    capital = annual.number("share_capital")
    if capital is not None and capital > 0 and par is not None and par > 0:
        return capital / par
    profit = annual.number("net_profit_parent")
    eps = annual.number("eps_basic")
    if profit is not None and profit > 0 and eps is not None and eps > 0:
        return profit / eps
    return None


def _fiscal_year_from_label(label: str | None) -> int | None:
    if not label:
        return None
    digits = "".join(ch for ch in label if ch.isdigit())
    if len(digits) >= 4:
        return int(digits[:4])
    return None


def build_dividend_book(
    records: Iterable[DividendRecord],
    *,
    as_of: str,
    require_implemented: bool = True,
) -> DividendBook:
    """按财年归集普通分配，并把特别分配与无法归集的部分单独列出。"""
    records = list(records)
    ordinary_by_year: dict[int, list[DividendRecord]] = {}
    special_by_year: dict[int, list[DividendRecord]] = {}
    unlabeled_special: list[DividendRecord] = []
    unlabeled_ordinary: list[DividendRecord] = []
    not_implemented: list[DividendRecord] = []
    other_kinds: list[DividendRecord] = []

    for record in records:
        if record.per_10 < 0:
            raise ValuationError(
                e.MODEL_INPUT_MISSING, f"{record.fact_id}: 每 10 股派息不能为负"
            )
        if record.per_10 == 0:
            continue
        if require_implemented:
            basis = record.implemented_basis(as_of)
            if basis is None:
                not_implemented.append(record)
                continue
            record = _with_basis(record, basis)
        if not record.is_ordinary and not record.is_special:
            other_kinds.append(record)
            continue
        fiscal_year = record.fiscal_year or _fiscal_year_from_label(record.fiscal_label)
        if fiscal_year is None:
            (unlabeled_special if record.is_special else unlabeled_ordinary).append(record)
            continue
        bucket = special_by_year if record.is_special else ordinary_by_year
        bucket.setdefault(fiscal_year, []).append(record)

    years: list[FiscalYearDistribution] = []
    for fiscal_year in sorted(set(ordinary_by_year) | set(special_by_year), reverse=True):
        ordinary = ordinary_by_year.get(fiscal_year, [])
        special = special_by_year.get(fiscal_year, [])
        years.append(
            FiscalYearDistribution(
                fiscal_year=fiscal_year,
                ordinary_per_share=sum(r.per_share for r in ordinary),
                special_per_share=sum(r.per_share for r in special),
                has_annual_final=any(r.is_annual_final for r in ordinary),
                components=tuple(
                    {**r.to_dict(), "bucket": "special" if r.is_special else "ordinary"}
                    for r in sorted(
                        ordinary + special,
                        key=lambda r: (r.announce_date or "", r.fact_id),
                    )
                ),
            )
        )

    window_start = _shift_year(as_of, -1)
    paid = [
        r
        for r in records
        if r.implemented_basis(as_of) is not None and r.ex_date and window_start < r.ex_date <= as_of
    ]
    ltm = sum(r.per_share for r in paid) if paid else None

    notes = [
        "财年合计 = 该财年内「年度分红 + 中期/季度分配」的每股派息之和；两者不重复计入。",
        "特别分红单列，不进入可持续 DPS 序列，也不外推为未来普通股息。",
        "「过去十二个月实际支付」按除权日滚动统计，与「某财年对应分配」不是同一指标。",
    ]
    if unlabeled_special:
        notes.append(
            f"{len(unlabeled_special)} 条特别分红缺少所属财年：单列展示，不并入任何财年合计。"
        )
    if unlabeled_ordinary:
        notes.append(
            f"{len(unlabeled_ordinary)} 条普通分红缺少所属财年：不按年份猜测，单列可查。"
        )
    if other_kinds:
        notes.append(
            f"{len(other_kinds)} 条记录不是现金分红类型（如股改分红）：不计入 DPS。"
        )
    if not_implemented:
        notes.append(
            f"{len(not_implemented)} 条记录尚未实施，不计入已支付与财年合计（单列可查）。"
        )
    return DividendBook(
        years=tuple(years),
        unlabeled_special=tuple(unlabeled_special),
        unlabeled_ordinary=tuple(unlabeled_ordinary),
        excluded_not_implemented=tuple(not_implemented),
        excluded_other_kinds=tuple(other_kinds),
        ltm_paid_per_share=ltm,
        ltm_window=(window_start, as_of) if paid else None,
        notes=tuple(notes),
    )


def _with_basis(record: DividendRecord, basis: str) -> DividendRecord:
    """把「凭什么算已实施」写进记录，便于审计。"""
    from dataclasses import replace

    return replace(record, status=f"{record.status or '未取得'}（已实施依据：{basis}）")


def _shift_year(value: str, delta: int) -> str:
    """YYYY-MM-DD 加减整年（滚动十二个月窗口，不做交易日历调整）。"""
    day = date.fromisoformat(str(value)[:10])
    try:
        shifted = day.replace(year=day.year + delta)
    except ValueError:  # 2 月 29 日
        shifted = day.replace(year=day.year + delta, day=28)
    return shifted.isoformat()
