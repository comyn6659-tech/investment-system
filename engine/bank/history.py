"""银行历史估值位置（描述性，不参与估值/结论）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from engine.bank.valuation_v2 import load_policy
from engine.quality.tables import read_table

ROOT = Path(__file__).resolve().parent.parent.parent
INPUTS = ROOT / "data" / "bank-pool" / "valuation-v2" / "inputs.csv.gz"


@lru_cache(maxsize=1)
def _rows() -> dict[str, dict[str, Any]]:
    return {str(row["code"]).zfill(6): row for row in read_table(INPUTS)}


def position(code: str) -> dict[str, Any]:
    row = _rows().get(str(code).zfill(6)) or {}
    if not row:
        return {"status": "missing", "months": 0, "pb_percentile": None, "dy_percentile": None}
    months = int(float(row["history_months"])) if row.get("history_months") not in (None, "") else 0
    return {
        "status": row.get("history_status") or "missing",
        "months": months,
        "start": row.get("history_start"),
        "end": row.get("history_end"),
        "pb_current": _num(row.get("pb_current")),
        "pb_percentile": _num(row.get("pb_percentile")),
        "dy_current": _num(row.get("dy_current")),
        "dy_percentile": _num(row.get("dy_percentile")),
    }


def implicit_return(code: str, price: float | None) -> float | None:
    row = _rows().get(str(code).zfill(6)) or {}
    if price is None or row.get("bvps") in (None, "") or row.get("roe_2025") in (None, ""):
        return None
    try:
        bvps = float(row["bvps"])
        latest_roe = float(row["roe_2025"])
        if bvps <= 0 or price <= 0:
            return None
        pb = price / bvps
        growth = float(load_policy()["parameters"]["implicit_return"]["growth"])
        return growth + (latest_roe - growth) / pb
    except (TypeError, ValueError, KeyError, ZeroDivisionError):
        return None


def implicit_return_line(code: str, price: float | None) -> str | None:
    value = implicit_return(code, price)
    if value is None:
        return None
    return (
        f"隐含长期回报率：约 {value:.1%}（假设净资产收益率保持最新水平、按现价买入；"
        "描述性，不参与结论）。"
    )


def one_line(code: str) -> str:
    item = position(code)
    if item["status"] == "ok" and item["pb_percentile"] is not None and item["dy_percentile"] is not None:
        return (
            f"历史位置：市净率处于近 10 年 {item['pb_percentile']:.0f}% 分位，"
            f"股息率处于 {item['dy_percentile']:.0f}% 分位"
            f"（东方财富市场市净率与分红数据，可用 {item['months']} 个月月末序列；"
            "市净率与估值同按普通股口径（价格 ÷ 每股净资产）计算，同源同口径，描述性，不给买点）。"
        )
    if item["status"] == "insufficient":
        return (
            f"历史位置：可用月末序列 {item['months']} 个月，不足 5 年，未计算分位。"
        )
    return "历史位置：暂无可用历史估值序列。"


def _num(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["implicit_return", "implicit_return_line", "one_line", "position"]
