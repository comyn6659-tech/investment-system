"""从合并报表科目到 FCFF 的桥接表，以及与经营现金流的勾稽。

为什么不能直接写 `FCFF = OCF − capex`：经营现金流里已经扣了利息与所得税的
实付数，也混入了非经营性项目；这样得到的数不是「企业自由现金流」，而是
「股东可分配现金」的近似。本模块把每一步写清楚，并给出与 OCF 的勾稽差额。

桥接结构（每一行都有来源、科目名与状态）：

    净利润（含少数股东）
  + 所得税费用
  = 利润总额
  + 净利息费用（财务费用净额，可为负 = 净利息收入）
  = EBIT
  − EBIT × 实际税率（实际税率 = 所得税 / 利润总额）
  = NOPAT
  + 折旧与摊销（固定资产折旧 + 无形/长期待摊/使用权资产摊销）
  − 资本开支（购建固定资产、无形资产和其他长期资产支付的现金）
  − 营运资本增加
  = FCFF

勾稽：FCFF ≈ OCF + 净利息费用 × (1 − 税率) − 资本开支。
两个口径的差额如实报告；差额超容差说明还有未解释项（非现金项目、少数股东损益、
重分类等），不把差额悄悄摊平。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.financials.annual import AnnualFinancials, FinancialHistory
from engine.valuation import errors as e
from engine.valuation.errors import ValuationError
from engine.valuation.fcff import EquityBridge

# 承担利息的负债科目（银行不使用本模块）
DEBT_COMPONENTS = (
    "short_loan",
    "long_loan",
    "bond_payable",
    "current_portion_lt_debt",
    "lease_liab",
)
_DEPRECIATION_COMPONENTS = (
    "depreciation",
    "amortization_intangible",
    "amortization_lt_prepaid",
    "amortization_right_of_use",
)
_NWC_ASSET = ("inventory", "receivables_notes", "receivables")
_NWC_LIABILITY = ("payables_notes", "payables")
# 现金流量表补充资料里的营运资本变动（正数=释放现金，与资产负债表口径互为交叉核对）
_DISCLOSED_NWC = (
    "inventory_decrease",
    "operating_receivables_decrease",
    "operating_payables_add",
)

_TAX_RATE_LOW = -0.20
_TAX_RATE_HIGH = 0.60


@dataclass(frozen=True)
class BridgeLine:
    item: str
    label: str
    amount: float | None
    sign: int
    status: str
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "item": self.item,
            "label": self.label,
            "amount": self.amount,
            "sign": self.sign,
            "status": self.status,
            "note": self.note,
        }


@dataclass(frozen=True)
class FcffBridge:
    fiscal_year: int
    period_end: str
    lines: tuple[BridgeLine, ...]
    fcff: float
    ebit: float
    tax_rate: float
    nopat: float
    depreciation_amortization: float
    capex: float
    change_in_nwc: float
    ocf_implied_fcff: float | None
    reconciliation_gap: float | None
    reconciliation_relative: float | None
    reconciled: bool | None
    nwc_source: str = ""
    change_in_nwc_cross_check: float | None = None
    issues: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "fiscal_year": self.fiscal_year,
            "period_end": self.period_end,
            "lines": [line.to_dict() for line in self.lines],
            "fcff": self.fcff,
            "ebit": self.ebit,
            "effective_tax_rate": self.tax_rate,
            "nopat": self.nopat,
            "depreciation_amortization": self.depreciation_amortization,
            "capex": self.capex,
            "change_in_nwc": self.change_in_nwc,
            "ocf_implied_fcff": self.ocf_implied_fcff,
            "reconciliation_gap": self.reconciliation_gap,
            "reconciliation_relative": self.reconciliation_relative,
            "reconciled": self.reconciled,
            "nwc_source": self.nwc_source,
            "change_in_nwc_cross_check": self.change_in_nwc_cross_check,
            "issues": list(self.issues),
        }


def _sum_optional(annual: AnnualFinancials, names: tuple[str, ...]) -> tuple[float, list[str]]:
    total = 0.0
    absent: list[str] = []
    for name in names:
        value = annual.number(name)
        if value is None:
            absent.append(name)
            continue
        total += value
    return total, absent


def _disclosed_change_in_nwc(annual: AnnualFinancials) -> tuple[float | None, list[str]]:
    """按现金流量表补充资料计算营运资本增加额（取负号：正数=占用现金）。

    这三项是报表编制者自己给出的「存货减少 / 经营性应收减少 / 经营性应付增加」，
    比只取「存货+应收−应付」更完整（还包含预付款项、其他应收等）。
    """
    absent: list[str] = []
    released = 0.0
    for name in _DISCLOSED_NWC:
        value = annual.number(name)
        if value is None:
            absent.append(name)
            continue
        released += value
    if len(absent) == len(_DISCLOSED_NWC):
        return None, absent
    return -released, absent


def _nwc(annual: AnnualFinancials) -> tuple[float, list[str]]:
    """营运资本 = 存货 + 应收（合并口径取合计项，避免与明细重复）− 应付。"""
    absent: list[str] = []
    assets = 0.0
    inventory = annual.number("inventory")
    if inventory is None:
        absent.append("inventory")
    else:
        assets += inventory
    # 应收票据及应收账款是合计口径，若存在就不再叠加应收账款明细
    receivable_total = annual.number("receivables_notes")
    if receivable_total is not None:
        assets += receivable_total
    else:
        receivable = annual.number("receivables")
        if receivable is None:
            absent.append("receivables")
        else:
            assets += receivable
    liabilities = 0.0
    payable_total = annual.number("payables_notes")
    if payable_total is not None:
        liabilities += payable_total
    else:
        payable = annual.number("payables")
        if payable is None:
            absent.append("payables")
        else:
            liabilities += payable
    return assets - liabilities, absent


def build_fcff_bridge(
    history: FinancialHistory,
    fiscal_year: int,
    *,
    tolerance: float = 0.15,
    model: str = "equity_dcf_fcff",
) -> FcffBridge:
    """为某个财年构造历史 FCFF 桥接表（需要上一年的资产负债表算营运资本变动）。"""
    annual = history.year(fiscal_year)
    prior = history.year(fiscal_year - 1)
    if annual is None:
        raise ValuationError(
            e.MODEL_INPUT_MISSING, f"缺少 {fiscal_year} 年年报数据，无法构造 FCFF 桥接"
        )

    net_profit = annual.require("net_profit", model=model)
    income_tax = annual.require("income_tax", model=model)
    pretax = annual.number("pretax_profit")
    if pretax is None:
        pretax = annual.require("pretax_profit", model=model)
    # 净利息费用：优先使用财务费用净额（含利息收入抵减），缺失时退回利息费用
    interest = annual.number("finance_expense")
    interest_note = "财务费用净额（利息支出−利息收入）"
    if interest is None:
        interest = annual.number("interest_expense")
        interest_note = "利息费用（未抵减利息收入；口径偏保守）"
    if interest is None:
        raise ValuationError(
            e.MODEL_INPUT_MISSING, f"{annual.period_end} 缺少利息费用科目，无法从利润总额还原 EBIT"
        )

    ebit = pretax + interest
    if pretax == 0:
        raise ValuationError(e.MODEL_INPUT_MISSING, "利润总额为 0，实际税率无定义")
    tax_rate = income_tax / pretax
    issues: list[str] = []
    if tax_rate < _TAX_RATE_LOW or tax_rate > _TAX_RATE_HIGH:
        raise ValuationError(
            e.MODEL_INPUT_MISSING,
            f"实际税率 {tax_rate:.2%} 超出可解释区间 [{_TAX_RATE_LOW:.0%}, {_TAX_RATE_HIGH:.0%}]，"
            "需先解释税收调整（递延税、一次性项目）",
        )
    nopat = ebit * (1.0 - tax_rate)

    depreciation, dep_absent = _sum_optional(annual, _DEPRECIATION_COMPONENTS)
    if dep_absent:
        issues.append("折旧摊销缺少科目：" + "、".join(dep_absent))
    capex = annual.require("capex", model=model)
    if capex < 0:
        raise ValuationError(e.MODEL_INPUT_MISSING, f"资本开支为负（{capex!r}）：请核对科目口径")

    # 营运资本变动：优先用现金流量表补充资料的披露数，再用资产负债表口径交叉核对
    change_nwc, nwc_absent = _disclosed_change_in_nwc(annual)
    nwc_source = "现金流量表补充资料（存货减少 + 经营性应收减少 + 经营性应付增加）"
    nwc_cross: float | None = None
    nwc_cross_docs = ""
    if change_nwc is None:
        nwc_end, nwc_absent_end = _nwc(annual)
        if prior is None:
            raise ValuationError(
                e.MODEL_INPUT_MISSING,
                f"缺少可用的营运资本变动数据（补充资料缺失，且无 {fiscal_year - 1} 年资产负债表）",
            )
        nwc_begin, nwc_absent_begin = _nwc(prior)
        if nwc_absent_end or nwc_absent_begin:
            issues.append(
                "营运资本科目缺失："
                + "、".join(sorted(set(nwc_absent_end) | set(nwc_absent_begin)))
            )
        change_nwc = nwc_end - nwc_begin
        nwc_source = "资产负债表口径：存货 + 应收 − 应付（补充资料缺失时的兜底）"
        nwc_cross_docs = "补充资料三项均缺失：" + "、".join(nwc_absent)
    if prior is not None:
        nwc_end, _ = _nwc(annual)
        nwc_begin, _ = _nwc(prior)
        nwc_cross = nwc_end - nwc_begin
        nwc_cross_docs = (
            "资产负债表口径（存货 + 应收票据及应收账款 − 应付票据及应付账款）"
            f"为 {nwc_cross:,.0f} 元；与披露口径差额 {change_nwc - nwc_cross:,.0f} 元，"
            "来自预付款项/其他应收等未纳入简化口径的项目"
        )
    fcff = nopat + depreciation - capex - change_nwc

    # 与经营现金流勾稽
    ocf = annual.number("ocf")
    implied = None if ocf is None else ocf + interest * (1.0 - tax_rate) - capex
    gap = None if implied is None else fcff - implied
    relative = None
    if gap is not None and ocf not in (None, 0):
        relative = gap / abs(ocf)
    reconciled = None if relative is None else abs(relative) <= tolerance

    lines = (
        BridgeLine(
            "net_profit",
            "净利润（含少数股东损益）",
            net_profit,
            1,
            annual.status("net_profit"),
            "合并利润表『NETPROFIT』",
        ),
        BridgeLine(
            "income_tax",
            "加：所得税费用",
            income_tax,
            1,
            annual.status("income_tax"),
            "合并利润表『INCOME_TAX』",
        ),
        BridgeLine(
            "pretax_profit",
            "＝利润总额（勾稽核对）",
            pretax,
            1,
            annual.status("pretax_profit"),
            "净利润 + 所得税费用 = 利润总额；不一致说明存在未解释项",
        ),
        BridgeLine(
            "interest_expense",
            "加：净利息费用（还原 EBIT）",
            interest,
            1,
            annual.status("finance_expense")
            if annual.number("finance_expense") is not None
            else annual.status("interest_expense"),
            interest_note,
        ),
        BridgeLine("ebit", "＝EBIT（息税前利润）", ebit, 1, "derived", "利润总额 + 净利息费用"),
        BridgeLine(
            "tax_on_ebit",
            "减：按 EBIT 口径的所得税",
            ebit * tax_rate,
            -1,
            "derived",
            f"实际税率 {tax_rate:.2%} = 所得税费用 / 利润总额",
        ),
        BridgeLine("nopat", "＝NOPAT（税后经营利润）", nopat, 1, "derived", "EBIT × (1 − 实际税率)"),
        BridgeLine(
            "depreciation_amortization",
            "加：折旧与摊销",
            depreciation,
            1,
            "derived",
            "固定资产折旧 + 无形资产摊销 + 长期待摊费用摊销 + 使用权资产摊销",
        ),
        BridgeLine(
            "capex", "减：资本开支", capex, -1, annual.status("capex"),
            "购建固定资产、无形资产和其他长期资产支付的现金",
        ),
        BridgeLine(
            "change_in_nwc",
            "减：营运资本增加",
            change_nwc,
            -1,
            "derived",
            nwc_source + "；" + nwc_cross_docs,
        ),
        BridgeLine("fcff", "＝FCFF（企业自由现金流）", fcff, 1, "derived", "NOPAT + 折旧摊销 − 资本开支 − 营运资本增加"),
    )

    return FcffBridge(
        fiscal_year=fiscal_year,
        period_end=annual.period_end,
        lines=lines,
        fcff=fcff,
        ebit=ebit,
        tax_rate=tax_rate,
        nopat=nopat,
        depreciation_amortization=depreciation,
        capex=capex,
        change_in_nwc=change_nwc,
        ocf_implied_fcff=implied,
        reconciliation_gap=gap,
        reconciliation_relative=relative,
        reconciled=reconciled,
        nwc_source=nwc_source,
        change_in_nwc_cross_check=nwc_cross,
        issues=tuple(issues),
    )


# --- 权益桥接 ---------------------------------------------------------------
@dataclass(frozen=True)
class EquityBridgeBuild:
    bridge: EquityBridge
    absent_components: tuple[str, ...]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "debt": self.bridge.debt,
            "cash": self.bridge.cash,
            "non_operating_assets": self.bridge.non_operating_assets,
            "minority_interest": self.bridge.minority_interest,
            "preferred_equity": self.bridge.preferred_equity,
            "shares": self.bridge.shares,
            "net_debt": self.bridge.net_debt,
            "absent_components": list(self.absent_components),
            "notes": list(self.notes),
        }


def build_equity_bridge(
    annual: AnnualFinancials,
    *,
    shares: float,
    absent_as_zero: bool,
    non_operating_assets: float = 0.0,
    preferred_equity: float = 0.0,
    model: str = "equity_dcf_fcff",
) -> EquityBridgeBuild:
    """从资产负债表科目构造权益桥接项。

    `absent_as_zero` 必须由研究假设显式声明并给出理由：报表未列出某负债科目，
    可能是「确实没有」，也可能是取数缺失，两者不能默认等同。
    """
    notes: list[str] = []
    absent: list[str] = []
    debt = 0.0
    for name in DEBT_COMPONENTS:
        value = annual.number(name)
        if value is None:
            absent.append(name)
            continue
        debt += value
    if absent:
        if not absent_as_zero:
            raise ValuationError(
                e.MODEL_INPUT_MISSING,
                "有息负债科目缺失且未声明 absent_as_zero：" + "、".join(absent),
            )
        notes.append(
            "报表未列出的有息负债科目按「无该项」处理："
            + "、".join(absent)
            + "（依据假设文件 absent_as_zero 声明）"
        )
    interest = annual.number("interest_expense") or annual.number("finance_expense") or 0.0
    if debt == 0 and interest > 0 and absent_as_zero:
        lease = annual.number("lease_liab")
        if not lease:
            issues = (
                f"{annual.period_end} 无任何有息负债科目，却有利息费用 {interest:,.0f}："
                "负债口径与利息费用冲突"
            )
            raise ValuationError(e.BRIDGE_ITEM_INVALID, issues)
        notes.append(
            f"利息费用 {interest:,.0f} 与租赁负债 {lease:,.0f} 对应，未另计普通借款"
        )

    cash = annual.require("cash", model=model)
    minority = annual.number("minority_equity") or 0.0
    bridge = EquityBridge(
        debt=debt,
        cash=cash,
        non_operating_assets=non_operating_assets,
        minority_interest=minority,
        preferred_equity=preferred_equity,
        shares=shares,
    )
    notes.append(
        "非经营性资产本轮按 0 处理：来源报表未把长期股权投资等拆分为非经营性项目，"
        "因此该项被记入 EV 的一部分而非单独加回（偏保守）"
    )
    return EquityBridgeBuild(bridge=bridge, absent_components=tuple(absent), notes=tuple(notes))
