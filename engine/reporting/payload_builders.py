"""把快照事实整理成业务载荷。

数据缺失时字段留空并记录 missing_data，绝不用 0 或占位数字填充。
"""

from __future__ import annotations

from typing import Any

from engine.contracts.enums import AssetClass, DataStatus, EtfStrategy, IndexClass
from engine.contracts.payloads import (
    RESEARCH_VALUATION_PAYLOAD_VERSION,
    amount,
    empty_equity_payload,
    empty_etf_payload,
    empty_index_payload,
)
from engine.data.facts import Fact
from engine.data.snapshot import Snapshot
from engine.identity.models import InstrumentRef


# 个股载荷只展示关键科目与最近若干报告期：完整三级科目以快照为准，
# 避免把 12 个季度 × 37 个科目原样塞进报告而使真正结论被淹没。
_EQUITY_PAYLOAD_METRICS = frozenset(
    {
        "total_operating_revenue",
        "operating_profit",
        "pretax_profit",
        "income_tax",
        "net_profit",
        "net_profit_parent",
        "operating_cash_flow",
        "capex",
        "depreciation",
        "equity_parent",
        "cash_and_equivalents",
        "total_assets",
        "basic_eps",
    }
)
_EQUITY_PAYLOAD_PERIODS = 6


# --- 个股 -------------------------------------------------------------------
def build_equity_payload(
    instrument: InstrumentRef,
    snapshot: Snapshot | None,
    *,
    research_valuation: dict[str, Any] | None = None,
    model_applicability: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """个股载荷。

    市场事实（company_facts / financial_periods / dividend_records）与模型结论严格分开：
    研究估值只进 `research_valuation`，正式 `valuation` 保留给 validated 模型。
    """
    payload = empty_equity_payload()
    if model_applicability is not None:
        payload["model_applicability"] = model_applicability
    if research_valuation is not None:
        payload["research_valuation"] = {
            "payload_version": RESEARCH_VALUATION_PAYLOAD_VERSION,
            **research_valuation,
        }
    if snapshot is None:
        return payload

    facts = snapshot.facts
    payload["company_facts"] = {
        f.metric: f.to_dict() for f in facts if f.fact_id.startswith("profile.")
    }

    periods: dict[str, dict[str, Any]] = {}
    for f in facts:
        if not f.fact_id.startswith("fin."):
            continue
        key = f.period or "unspecified"
        entry = periods.setdefault(
            key, {"period": key, "notice_date": None, "facts": {}}
        )
        if f.metric in _EQUITY_PAYLOAD_METRICS:
            entry["facts"][f.metric] = f.to_dict()
        if f.metric == "notice_date" and f.value is not None:
            entry["notice_date"] = f.value

    payload["financial_periods"] = sorted(
        periods.values(), key=lambda e: e["period"], reverse=True
    )[:_EQUITY_PAYLOAD_PERIODS]
    payload["dividend_records"] = [
        f.to_dict()
        for f in facts
        if f.fact_id.startswith("div.") and f.fact_id.endswith(".per_10")
    ]
    payload["dividend_summary"] = _dividend_summary(snapshot)
    return payload


def _dividend_summary(snapshot: Snapshot) -> dict[str, Any] | None:
    """按财年归集的分配摘要（特别分红单列，LTM 与财年合计分开标注）。"""
    from engine.financials.annual import load_dividend_records
    from engine.financials.dividends import build_dividend_book

    try:
        book = build_dividend_book(load_dividend_records(snapshot), as_of=snapshot.as_of)
    except Exception:  # 数据异常时不阻断事实报告，摘要留空
        return None
    return {
        "fiscal_years": [
            {
                "fiscal_year": y.fiscal_year,
                "ordinary_dps": y.ordinary_per_share,
                "special_dps": y.special_per_share,
                "has_annual_final": y.has_annual_final,
                "components": [
                    {
                        "kind": item.get("kind"),
                        "per_share": item.get("per_share"),
                        "ex_date": item.get("ex_date"),
                        "status": item.get("status"),
                        "bucket": item.get("bucket"),
                        "fiscal_label": item.get("fiscal_label"),
                    }
                    for item in y.components
                ],
            }
            for y in book.years
        ],
        "ltm_paid_per_share": book.ltm_paid_per_share,
        "ltm_window": list(book.ltm_window) if book.ltm_window else None,
        "unlabeled_special": [
            {"per_share": r.per_share, "kind": r.kind, "announce_date": r.announce_date}
            for r in book.unlabeled_special
        ],
        "notes": list(book.notes),
    }


# --- 指数 -------------------------------------------------------------------
def build_index_payload(
    instrument: InstrumentRef,
    snapshot: Snapshot | None,
    *,
    strategy_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = empty_index_payload()
    payload["index_class"] = instrument.index_class.value if instrument.index_class else None
    payload["strategy_state"] = {
        "status": "not_implemented",
        "model_status": "research",
        "candidates": strategy_candidates,
        "note": "阶段一不计算任何分位或信号；候选策略规则留待阶段三固定后回测。",
    }
    if snapshot is None:
        return payload

    payload["history_coverage"] = snapshot.coverage

    level_fact = snapshot.fact("index.level")
    if level_fact is not None and level_fact.is_usable_number:
        payload["level"] = amount(level_fact.value, "index_point", None)

    valuation_keys = {
        "index.pe_ttm": "pe_ttm",
        "index.pb": "pb",
        "index.pe_csindex_1": "pe_csindex_ratio_1",
        "index.dividend_yield_csindex_1": "csindex_dividend_yield_1",
        "index.dividend_yield": "dividend_yield",
    }
    valuation: dict[str, Any] = {}
    for fact_id, name in valuation_keys.items():
        fact = snapshot.fact(fact_id)
        if fact is not None:
            valuation[name] = fact.to_dict()
    payload["valuation_facts"] = valuation
    return payload


# --- ETF -------------------------------------------------------------------
def build_etf_payload(
    instrument: InstrumentRef,
    underlying: InstrumentRef | None,
    snapshot: Snapshot | None,
) -> dict[str, Any]:
    payload = empty_etf_payload()
    if underlying is not None:
        payload["underlying_index"] = {
            "instrument_id": underlying.instrument_id,
            "display_name": underlying.display_name,
            "code": underlying.code,
            "index_class": underlying.index_class.value if underlying.index_class else None,
            "scope": underlying.scope.to_dict() if underlying.scope else None,
        }
    if snapshot is None:
        return payload

    price = snapshot.fact("etf.price")
    if price is not None and price.is_usable_number:
        payload["price"] = amount(price.value, "CNY_per_share", price.currency or "CNY")

    nav = snapshot.fact("etf.nav")
    nav_date = snapshot.fact("etf.nav_date")
    nav_disclosed = snapshot.fact("etf.nav_disclosed_at")
    if nav is not None:
        payload["nav"] = {
            "value": nav.value if nav.is_usable_number else None,
            "unit": "CNY_per_share" if nav.is_usable_number else None,
            "currency": "CNY" if nav.is_usable_number else None,
            "status": nav.status.value,
            "nav_date": nav_date.value if nav_date else None,
            "disclosed_at": nav_disclosed.value if nav_disclosed else None,
            "source": nav.source.to_dict() if nav.source else None,
        }

    premium = snapshot.fact("etf.premium_discount_pct")
    if premium is not None and premium.is_usable_number:
        payload["premium_discount"] = {
            "value": premium.value,
            "unit": "percent",
            "currency": None,
            "as_of": premium.period,
            "source": premium.source.to_dict() if premium.source else None,
        }

    mgmt = snapshot.fact("etf.management_fee_pct")
    custody = snapshot.fact("etf.custody_fee_pct")
    if mgmt is not None or custody is not None:
        payload["fees"] = {
            "management": amount(mgmt.value, "percent", None) if mgmt and mgmt.is_usable_number else None,
            "custody": amount(custody.value, "percent", None)
            if custody and custody.is_usable_number
            else None,
            "source": (mgmt or custody).source.to_dict() if (mgmt or custody) else None,
        }

    status_fact = snapshot.fact("etf.trading_status")
    if status_fact is not None:
        payload["trading"] = {
            "status": status_fact.value,
            "status_code": status_fact.status.value,
            "as_of": status_fact.period,
            "source": status_fact.source.to_dict() if status_fact.source else None,
        }

    tracking = snapshot.fact("etf.tracking_note")
    if tracking is not None:
        payload["tracking"] = {
            "note": tracking.value,
            "status": tracking.status.value,
            "source": tracking.source.to_dict() if tracking.source else None,
        }
    return payload


# --- 缺失数据清单 -----------------------------------------------------------
_IMPORTANT_METRICS = {
    "equity": (
        "fin.ocf",
        "fin.capex",
        "fin.revenue",
        "fin.net_profit_parent",
        "fin.equity_parent",
        "bank.cet1_ratio",
        "price",
    ),
    "index": ("index.level", "index.pe_ttm", "index.pb", "index.dividend_yield"),
    "etf": ("etf.price", "etf.nav", "etf.management_fee_pct", "etf.premium_discount_pct"),
}


_WANTED_METRIC_NAMES = {
    "equity": {
        "operating_cash_flow",
        "capex",
        "total_operating_revenue",
        "net_profit_parent",
        "equity_parent",
        "non_performing_loan_ratio",
        "provision_coverage_ratio",
        "cet1_capital_adequacy_ratio",
        "risk_weighted_assets",
        "regulatory_cet1_minimum",
        "market_close_price",
    },
    "index": {"index_close_level", "pe_ttm", "pb", "dividend_yield"},
    "etf": {"etf_close_price", "unit_nav", "management_fee_pct", "premium_discount_pct"},
}


def collect_missing_data(
    asset_class: AssetClass, snapshot: Snapshot | None
) -> list[dict[str, Any]]:
    """收集缺失/不适用/过期/冲突的**关键**事实，供结果与摘要展示。

    只按指标名过滤，不按前缀：否则每个季报的次要科目缺失都会淹没真正的缺口。
    """
    if snapshot is None:
        return []
    wanted = _WANTED_METRIC_NAMES.get(asset_class.value, set())
    out: list[dict[str, Any]] = []
    for fact in snapshot.facts:
        if fact.status in (DataStatus.KNOWN, DataStatus.KNOWN_ZERO):
            continue
        if fact.metric not in wanted:
            continue
        out.append(
            {
                "fact_id": fact.fact_id,
                "metric": fact.metric,
                "status": fact.status.value,
                "note": fact.notes,
                "period": fact.period,
            }
        )
    return sorted(out, key=lambda e: e["fact_id"])


# --- 策略候选（阶段一只声明，不计算）----------------------------------------
def strategy_candidates() -> list[dict[str, Any]]:
    return [
        {
            "candidate": "A",
            "name": "估值回归基线",
            "model_id": "index_strategy_a_valuation_reversion",
            "model_status": "research",
            "entry": "PE-TTM 5年历史分位 ≤ 20",
            "exit": "PE 分位 ≥ 60，或持有满 12 个日历月",
            "implemented": False,
        },
        {
            "candidate": "B",
            "name": "估值交叉确认",
            "model_id": "index_strategy_b_cross_confirmation",
            "model_status": "research",
            "entry": "PE 分位 ≤ 20 且 PB 分位 ≤ 40",
            "exit": "PE 分位 ≥ 60 或 PB 分位 ≥ 70，或持有满 12 个日历月",
            "implemented": False,
        },
    ]


def describe_fact(fact: Fact | None) -> str:
    if fact is None:
        return "无该项"
    if fact.status == DataStatus.KNOWN_ZERO:
        return "已知为零"
    if fact.status == DataStatus.MISSING:
        return "缺失"
    if fact.status == DataStatus.NOT_APPLICABLE:
        return "不适用"
    if fact.status == DataStatus.STALE:
        return f"过期（{fact.period}）"
    if fact.status == DataStatus.CONFLICT:
        return f"来源冲突（{fact.conflict_values}）"
    return f"{fact.value}"


_ = (EtfStrategy, IndexClass, empty_equity_payload, empty_etf_payload, empty_index_payload)
