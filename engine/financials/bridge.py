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
from typing import Any, Mapping

from engine.data.facts import Fact
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
# 桥接项白名单：每一项都必须「有来源的值」或「有依据的零声明」，没有第三种处理方式。
BRIDGE_ITEMS = (
    "debt_components",
    "cash",
    "minority_equity",
    "non_operating_assets",
    "preferred_equity",
)

_BRIDGE_ITEM_LABELS = {
    "debt_components": "有息负债各科目",
    "cash": "货币资金",
    "minority_equity": "少数股东权益",
    "non_operating_assets": "非经营性资产",
    "preferred_equity": "其他权益工具（优先股/永续债）",
}


@dataclass(frozen=True)
class BridgeItemPolicy:
    """某一桥接项在没有可用事实时的处理规则。

    `treat_absent_as_zero=True` 等价于声明「本标的不存在该项」，必须给出依据；
    没有这条声明时，缺数据就只能停在 insufficient_data。
    """

    item: str
    treat_absent_as_zero: bool
    basis: str = ""

    def __post_init__(self) -> None:
        if self.item not in BRIDGE_ITEMS:
            raise ValuationError(
                e.BRIDGE_ITEM_INVALID,
                f"未知的权益桥接项 {self.item!r}，允许 {list(BRIDGE_ITEMS)}",
            )
        if self.treat_absent_as_zero and not self.basis.strip():
            raise ValuationError(
                e.BRIDGE_ITEM_INVALID,
                f"桥接项 {self.item} 声明「视为零」时必须写明依据",
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "item": self.item,
            "treat_absent_as_zero": self.treat_absent_as_zero,
            "basis": self.basis,
        }


def parse_bridge_policy(raw: Mapping[str, Any]) -> dict[str, BridgeItemPolicy]:
    """解析假设文件里的 `bridge_policy`。

    只接受白名单内的项；不允许用一个全局开关授权所有项归零
    （那正是「不知道要扣多少」被当成「不需要扣」的来源）。
    """
    if not isinstance(raw, Mapping):
        raise ValuationError(e.BRIDGE_ITEM_INVALID, "bridge_policy 必须是对象")
    unknown = [key for key in raw if key not in BRIDGE_ITEMS]
    if unknown:
        raise ValuationError(
            e.BRIDGE_ITEM_INVALID, f"bridge_policy 含未定义项 {unknown}，允许 {list(BRIDGE_ITEMS)}"
        )
    policy: dict[str, BridgeItemPolicy] = {}
    for item, body in raw.items():
        if not isinstance(body, Mapping):
            raise ValuationError(e.BRIDGE_ITEM_INVALID, f"bridge_policy.{item} 必须是对象")
        policy[item] = BridgeItemPolicy(
            item=item,
            treat_absent_as_zero=bool(body.get("treat_absent_as_zero", False)),
            basis=str(body.get("basis", "")),
        )
    return policy


@dataclass(frozen=True)
class BridgeItemTreatment:
    """桥接项的最终处理方式，随结果输出以便审计。"""

    item: str
    status: str  # sourced / sourced_zero / asserted_zero
    value: float
    basis: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "item": self.item,
            "label": _BRIDGE_ITEM_LABELS.get(self.item, self.item),
            "status": self.status,
            "value": self.value,
            "basis": self.basis,
        }


@dataclass(frozen=True)
class EquityBridgeBuild:
    bridge: EquityBridge
    absent_components: tuple[str, ...]
    notes: tuple[str, ...]
    treatments: tuple[BridgeItemTreatment, ...] = ()

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
            "item_treatments": [t.to_dict() for t in self.treatments],
            "notes": list(self.notes),
        }


def _resolve_item(
    item: str,
    fact: Fact | None,
    policy: Mapping[str, BridgeItemPolicy],
    *,
    model: str,
) -> tuple[float, BridgeItemTreatment]:
    """一个桥接项要么有可用事实，要么有显式零声明，否则拒绝整个模型。"""
    label = _BRIDGE_ITEM_LABELS.get(item, item)
    if fact is not None and fact.is_usable_number:
        value = float(fact.value)
        status = "sourced_zero" if fact.is_zero else "sourced"
        return value, BridgeItemTreatment(item=item, status=status, value=value, basis=fact.source.source_id if fact.source else "")
    declared = policy.get(item)
    if declared is not None and declared.treat_absent_as_zero:
        return 0.0, BridgeItemTreatment(
            item=item, status="asserted_zero", value=0.0, basis=declared.basis
        )
    status_text = fact.status.value if fact is not None else "absent"
    detail = ""
    if fact is not None and fact.notes:
        detail = f"（来源说明：{fact.notes}）"
    raise ValuationError(
        e.MODEL_INPUT_MISSING,
        f"{model} 需要 {label}，但当前状态为 {status_text}{detail}；"
        "缺失/冲突不能被当成零，也不能由通用开关授权归零。"
        "若确认该项对本标的不存在，请在假设文件 bridge_policy 中单独声明并写明依据。",
    )


def build_equity_bridge(
    annual: AnnualFinancials,
    *,
    shares: float,
    policy: Mapping[str, BridgeItemPolicy] | None = None,
    model: str = "equity_dcf_fcff",
) -> EquityBridgeBuild:
    """从资产负债表科目构造权益桥接项。

    每个桥接项只有两种合法来源：报表中可用的数值，或假设文件对**该项**的显式零声明。
    不再有 `absent_as_zero` 这类一次性授权所有项的开关。
    """
    policy = dict(policy or {})
    notes: list[str] = []
    treatments: list[BridgeItemTreatment] = []
    absent: list[str] = []

    debt = 0.0
    for name in DEBT_COMPONENTS:
        fact = annual.fact(name)
        if fact is not None and fact.is_usable_number:
            debt += float(fact.value)
        else:
            absent.append(name)
    debt_treatment = _resolve_composite_debt(annual, debt, absent, policy, model=model)
    treatments.append(debt_treatment)
    if debt_treatment.status == "asserted_zero":
        notes.append(f"有息负债：{debt_treatment.basis}")

    cash_fact = annual.fact("cash")
    if cash_fact is None or not cash_fact.is_usable_number:
        raise ValuationError(
            e.MODEL_INPUT_MISSING,
            f"{model} 缺少可用的货币资金，无法完成企业价值到股权价值的桥接",
        )
    cash = float(cash_fact.value)
    treatments.append(
        BridgeItemTreatment(
            item="cash",
            status="sourced_zero" if cash_fact.is_zero else "sourced",
            value=cash,
            basis=cash_fact.source.source_id if cash_fact.source else "",
        )
    )

    minority, minority_treatment = _resolve_item(
        "minority_equity", annual.fact("minority_equity"), policy, model=model
    )
    treatments.append(minority_treatment)
    if minority_treatment.status == "asserted_zero":
        notes.append(f"少数股东权益：{minority_treatment.basis}")

    non_op, non_op_treatment = _resolve_absent_item(
        "non_operating_assets", policy, model=model
    )
    treatments.append(non_op_treatment)
    if non_op_treatment.status == "asserted_zero":
        notes.append(f"非经营性资产：{non_op_treatment.basis}")

    preferred, preferred_treatment = _resolve_absent_item(
        "preferred_equity", policy, model=model
    )
    treatments.append(preferred_treatment)
    if preferred_treatment.status == "asserted_zero":
        notes.append(f"其他权益工具：{preferred_treatment.basis}")

    interest = annual.number("interest_expense") or annual.number("finance_expense") or 0.0
    if debt == 0 and interest > 0 and absent:
        lease = annual.number("lease_liab")
        if not lease:
            raise ValuationError(
                e.BRIDGE_ITEM_INVALID,
                f"{annual.period_end} 无任何有息负债科目，却有利息费用 {interest:,.0f}："
                "负债口径与利息费用冲突",
            )
        notes.append(f"利息费用 {interest:,.0f} 与租赁负债 {lease:,.0f} 对应，未另计普通借款")

    bridge = EquityBridge(
        debt=debt,
        cash=cash,
        non_operating_assets=non_op,
        minority_interest=minority,
        preferred_equity=preferred,
        shares=shares,
    )
    return EquityBridgeBuild(
        bridge=bridge,
        absent_components=tuple(absent),
        notes=tuple(notes),
        treatments=tuple(treatments),
    )


def _resolve_composite_debt(
    annual: AnnualFinancials,
    debt: float,
    absent: list[str],
    policy: Mapping[str, BridgeItemPolicy],
    *,
    model: str,
) -> BridgeItemTreatment:
    if not absent:
        return BridgeItemTreatment(item="debt_components", status="sourced", value=debt)
    declared = policy.get("debt_components")
    if declared is None or not declared.treat_absent_as_zero:
        raise ValuationError(
            e.MODEL_INPUT_MISSING,
            f"{model} 缺少有息负债科目 {absent}；缺失不能被当成零。"
            "若确认本标的没有这些科目，请在假设文件 bridge_policy.debt_components 中声明并写明依据。",
        )
    basis = declared.basis + f"（未列示的科目：{'、'.join(absent)}）"
    # 仍有部分科目来自报表时，状态必须体现「部分有来源 + 部分按声明视为零」
    status = "asserted_zero" if debt == 0 else "sourced_partial"
    return BridgeItemTreatment(
        item="debt_components",
        status=status,
        value=debt,
        basis=basis,
    )


def _resolve_absent_item(
    item: str, policy: Mapping[str, BridgeItemPolicy], *, model: str
) -> tuple[float, BridgeItemTreatment]:
    declared = policy.get(item)
    if declared is None or not declared.treat_absent_as_zero:
        raise ValuationError(
            e.MODEL_INPUT_MISSING,
            f"{model} 未取得「{_BRIDGE_ITEM_LABELS.get(item, item)}」数据；"
            "缺失不能被当成零，请在假设文件 bridge_policy 中对该项单独声明并写明依据，"
            "或补齐该科目。",
        )
    return 0.0, BridgeItemTreatment(
        item=item, status="asserted_zero", value=0.0, basis=declared.basis
    )
