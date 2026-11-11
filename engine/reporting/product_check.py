"""ETF 产品资料完整性检查（第三阶段首轮）。

只回答「哪些资料齐备、哪些尚缺」，不制定溢价/流动性/费率的买卖门槛，
不做基金之间的比较或推荐，也不因有价格就断言当前可交易。
"""

from __future__ import annotations

from typing import Any

from engine.data.facts import Fact
from engine.data.snapshot import Snapshot
from engine.identity.models import InstrumentRef

PRODUCT_CHECK_VERSION = "etf-product-check/1"


def _num(snapshot: Snapshot, fact_id: str) -> dict[str, Any] | None:
    fact = snapshot.fact(fact_id)
    if fact is None:
        return None
    if not fact.is_usable_number:
        return {
            "value": None,
            "status": fact.status.value,
            "unit": fact.unit,
            "currency": fact.currency,
            "period": fact.period,
            "note": fact.notes,
        }
    return {
        "value": float(fact.value),
        "status": fact.status.value,
        "unit": fact.unit,
        "currency": fact.currency,
        "period": fact.period,
        "as_of": fact.period,
        "available_at": fact.available_at,
        "source": fact.source.to_dict() if fact.source else None,
        "note": fact.notes,
    }


def _text(snapshot: Snapshot, fact_id: str) -> dict[str, Any] | None:
    fact = snapshot.fact(fact_id)
    if fact is None:
        return None
    return {
        "value": fact.value,
        "status": fact.status.value,
        "period": fact.period,
        "note": fact.notes,
        "source": fact.source.to_dict() if fact.source else None,
    }


def _missing_item(snapshot: Snapshot, fact_id: str, label: str, why: str) -> dict[str, Any]:
    fact = snapshot.fact(fact_id)
    return {
        "item": label,
        "fact_id": fact_id,
        "status": fact.status.value if fact is not None else "absent",
        "note": (fact.notes if fact is not None and fact.notes else why),
    }


def build_product_check(
    instrument: InstrumentRef,
    underlying: InstrumentRef | None,
    snapshot: Snapshot | None,
) -> dict[str, Any]:
    """组装 ETF 产品资料块；缺失项逐条列出，不用估算值填充。"""
    if snapshot is None:
        return {
            "payload_version": PRODUCT_CHECK_VERSION,
            "status": "insufficient_data",
            "identity": None,
            "price": None,
            "nav": None,
            "premium_discount_same_day": None,
            "fees": None,
            "turnover": None,
            "trading": None,
            "missing": [
                {"item": "快照", "fact_id": None, "status": "missing", "note": "没有可用快照"}
            ],
            "usage_note": "",
        }

    price = _num(snapshot, "etf.price")
    build_block = (snapshot.coverage or {}).get("build") or {}
    bar_status = build_block.get("bar_status")
    bar = {
        "value": bar_status,
        "status": "known" if bar_status else "unknown",
        "period": snapshot.as_of,
        "note": "按交易所收盘时间判定；intraday 表示当日未收盘",
    }
    nav = _num(snapshot, "etf.nav")
    nav_date = _text(snapshot, "etf.nav_date")
    nav_disclosed = _text(snapshot, "etf.nav_disclosed_at")
    premium = _num(snapshot, "etf.premium_discount_same_day_pct")

    # 同日折溢价的前提必须在出口再核一次：价格、净值、折溢价三者同一估值日期，
    # 且净值必须为正。快照一旦被改动或拼接，就不许继续用它。
    premium_reject_reason: str | None = None
    if premium is not None and premium.get("value") is not None:
        price_date = (price or {}).get("period")
        nav_day = (nav_date or {}).get("value")
        nav_value = (nav or {}).get("value")
        if price_date != nav_day:
            premium_reject_reason = (
                f"价格日期 {price_date} 与净值日期 {nav_day} 不一致，拒绝同日折溢价"
            )
        elif premium.get("period") != price_date:
            premium_reject_reason = (
                f"折溢价记录日期 {premium.get('period')} 与价格日期 {price_date} 不一致"
            )
        elif nav_value is None or float(nav_value) <= 0:
            premium_reject_reason = f"单位净值 {nav_value} 不可用或非正"
    if premium_reject_reason:
        premium = {
            "label": "事后同日折溢价",
            "value": None,
            "status": "rejected",
            "unit": "percent",
            "currency": None,
            "period": None,
            "note": premium_reject_reason,
        }
    elif premium is not None:
        premium = {**premium, "label": "事后同日折溢价"}

    missing: list[dict[str, Any]] = []
    if nav_disclosed is not None and nav_disclosed["status"] != "known":
        missing.append(
            {
                "item": "净值披露时刻",
                "fact_id": "etf.nav_disclosed_at",
                "status": nav_disclosed["status"],
                "note": nav_disclosed.get("note") or "披露时刻未知",
            }
        )
    if premium is None or premium["value"] is None:
        missing.append(
            {
                "item": "同日折溢价",
                "fact_id": "etf.premium_discount_same_day_pct",
                "status": (premium or {}).get("status", "absent"),
                "note": (premium or {}).get("note") or "未计算",
            }
        )
    missing.extend(
        [
            _missing_item(
                snapshot,
                "etf.trading_status",
                "二级市场交易状态（停复牌）",
                "未取得交易所停复牌字段",
            ),
            _missing_item(
                snapshot,
                "etf.bid_ask_spread_cny",
                "买卖价差",
                "日线行情不含盘口数据",
            ),
        ]
    )

    fees = None
    mgmt = _num(snapshot, "etf.management_fee_pct")
    custody = _num(snapshot, "etf.custody_fee_pct")
    if mgmt is not None or custody is not None:
        fees = {
            "management": mgmt,
            "custody": custody,
            "effective_date_known": False,
            "note": "费率来自基金概况页，未标注生效日期；以基金合同为准",
        }

    turnover = None
    latest = _num(snapshot, "etf.turnover_cny")
    avg = _num(snapshot, "etf.turnover_avg_20d_cny")
    days = _num(snapshot, "etf.turnover_avg_days_used")
    volume = _num(snapshot, "etf.volume_shares")
    if latest is not None or avg is not None:
        days_used = int(days["value"]) if days and days.get("value") is not None else None
        turnover = {
            "latest": latest,
            "average": avg,
            "average_label": (
                f"近 {days_used} 个交易日平均成交额"
                if days_used
                else "平均成交额"
            ),
            "days_used": days_used,
            "is_full_20d": days_used == 20,
            "volume": volume,
        }

    trading = {
        "secondary_market_status": _text(snapshot, "etf.trading_status"),
        "bid_ask_spread": _text(snapshot, "etf.bid_ask_spread_cny"),
        "subscription_status": _text(snapshot, "etf.subscription_status"),
        "redemption_status": _text(snapshot, "etf.redemption_status"),
        "note": (
            "申购/赎回状态来自基金净值表，不等于二级市场交易状态；"
            "本轮没有取得停复牌与盘口数据，因此不断言当前可交易"
        ),
    }

    identity = {
        "instrument_id": instrument.instrument_id,
        "display_name": instrument.display_name,
        "etf_strategy": instrument.etf_strategy.value if instrument.etf_strategy else None,
        "tracking_note": _text(snapshot, "etf.tracking_note"),
        "underlying_index": (
            {
                "instrument_id": underlying.instrument_id,
                "display_name": underlying.display_name,
            }
            if underlying is not None
            else None
        ),
        "evidence": [e.to_dict() for e in instrument.evidence],
    }

    # 完整性只看「本轮会去取的字段」；停复牌与盘口本来就取不到，
    # 它们列在 missing 里并单独给原因码，不影响可计算部分的完整性
    computable = {
        "identity": underlying is not None or instrument.underlying_index_id is not None,
        "price": price is not None and price.get("value") is not None,
        "nav": nav is not None and nav.get("value") is not None,
        "premium": premium is not None and premium.get("value") is not None,
        "fees": fees is not None,
        "turnover": turnover is not None,
    }
    missing_computable = [name for name, ok in computable.items() if not ok]
    status = "complete" if not missing_computable else "partial"

    return {
        "payload_version": PRODUCT_CHECK_VERSION,
        "status": status,
        "identity": identity,
        "price": {
            "price": price,
            "bar_status": bar,
            "price_type": "close_unadjusted",
        },
        "nav": {
            "value": nav,
            "nav_date": nav_date,
            "disclosed_at": nav_disclosed,
            "disclosure_time_known": bool(
                nav_disclosed is not None and nav_disclosed["status"] == "known"
            ),
        },
        "premium_discount_same_day": premium,
        "fees": fees,
        "turnover": turnover,
        "trading": trading,
        "missing": missing,
        "missing_computable": missing_computable,
        "usage_note": (
            "本块只说明资料是否齐备，不构成买卖条件；"
            "「资料齐备」不等于「满足买入条件」，指数估值位置与 ETF 产品资料也不互相替代。"
        ),
    }


__all__ = ["PRODUCT_CHECK_VERSION", "build_product_check"]
