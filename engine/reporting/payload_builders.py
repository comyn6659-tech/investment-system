"""把快照事实整理成业务载荷。

数据缺失时字段留空并记录 missing_data，绝不用 0 或占位数字填充。
"""

from __future__ import annotations

from typing import Any

from engine.contracts.enums import AssetClass, DataStatus, EtfStrategy, IndexClass
from engine.contracts.payloads import (
    amount,
    empty_equity_payload,
    empty_etf_payload,
    empty_index_payload,
)
from engine.data.facts import Fact
from engine.data.snapshot import Snapshot
from engine.identity.models import InstrumentRef


# --- 个股 -------------------------------------------------------------------
def build_equity_payload(
    instrument: InstrumentRef, snapshot: Snapshot | None
) -> dict[str, Any]:
    payload = empty_equity_payload()
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
        entry["facts"][f.metric] = f.to_dict()
        if f.metric == "notice_date" and f.value is not None:
            entry["notice_date"] = f.value

    payload["financial_periods"] = sorted(
        periods.values(), key=lambda e: e["period"], reverse=True
    )
    payload["dividend_records"] = [
        f.to_dict() for f in facts if f.fact_id.startswith("div.")
    ]
    return payload


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
    if instrument.etf_strategy is not None:
        payload["_etf_strategy"] = instrument.etf_strategy.value

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
    "equity": ("fin.ocf", "fin.capex", "fin.net_debt", "fin.share_count"),
    "index": ("index.level", "index.pe_ttm", "index.pb", "index.dividend_yield"),
    "etf": ("etf.price", "etf.nav", "etf.management_fee_pct", "etf.premium_discount_pct"),
}


def collect_missing_data(
    asset_class: AssetClass, snapshot: Snapshot | None
) -> list[dict[str, Any]]:
    """收集缺失/不适用/过期/冲突的关键事实，供结果与摘要展示。"""
    if snapshot is None:
        return []
    key = asset_class.value
    wanted = _IMPORTANT_METRICS.get(key, ())
    out: list[dict[str, Any]] = []
    for fact in snapshot.facts:
        if fact.status == DataStatus.KNOWN or fact.status == DataStatus.KNOWN_ZERO:
            continue
        if not fact.fact_id.startswith(tuple(w.split(".")[0] + "." for w in wanted)):
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
