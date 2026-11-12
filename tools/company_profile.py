#!/usr/bin/env python
"""公司资料（名称 / 行业 / 上市日期 / 总股本）的统一取数。

东方财富 push2 接口（`stock_individual_info_em`）在当前网络下会返回非 JSON
响应（已知限流），因此这里准备两条同源可核实的路径：

1. 优先东方财富个股信息（字段最全，含当前总股本）；
2. 失败则回退巨潮资讯：
   - 股本变动 `stock_share_change_cninfo` → 总股本（单位：万股）；
   - 行业分类变更 `stock_industry_change_cninfo` → 名称与行业（优先申万口径，
     因为它的门类/大类直接写「银行 / 证券 / 保险 / 煤炭 / 钢铁」）。

两条路径都记录实际使用的来源与方法；都没有取得时返回 None 字段，绝不猜。
"""

from __future__ import annotations

import time
from pathlib import Path

import akshare as ak
import pandas as pd

_EM_INFO_BLOCKED = False


def _f(value) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _text(value) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = " ".join(str(value).split())
    return None if text in ("", "nan", "None", "--") else text


def _retry(fn, *, attempts: int = 3, sleep: float = 2.0):
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt + 1 < attempts:
                time.sleep(sleep * (attempt + 1))
    raise RuntimeError(f"{type(last).__name__}: {last}") from last


def _industry_from_cninfo(frame: pd.DataFrame) -> tuple[str | None, str | None]:
    """取最近一次行业分类，优先申万口径（门类/大类直接用「银行 / 证券」等词）。"""
    if frame is None or frame.empty:
        return None, None
    frame = frame.copy()
    frame["变更日期"] = pd.to_datetime(frame["变更日期"], errors="coerce")
    frame = frame.dropna(subset=["变更日期"]).sort_values("变更日期")
    preferred = frame[frame["分类标准"].astype(str).str.contains("申银万国")]
    row = preferred.iloc[-1] if not preferred.empty else frame.iloc[-1]
    parts = [
        _text(row.get("行业门类")),
        _text(row.get("行业大类")),
        _text(row.get("行业次类")),
        _text(row.get("行业中类")),
    ]
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    label = " / ".join(seen) if seen else None
    standard = _text(row.get("分类标准"))
    return label, standard


def company_profile(code: str, *, as_of: str, cache_dir: Path) -> dict:
    """返回 {name, industry, listing_date, total_shares, source_id, method, notes}。"""
    global _EM_INFO_BLOCKED
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    if not _EM_INFO_BLOCKED:
        try:
            info = _retry(lambda: ak.stock_individual_info_em(symbol=code), attempts=2)
            info_map = dict(zip(info["item"], info["value"]))
            return {
                "name": _text(info_map.get("股票简称")),
                "industry": _text(info_map.get("行业")),
                "listing_date": (
                    _text(info_map.get("上市时间"))[:10].replace("/", "-")
                    if _text(info_map.get("上市时间"))
                    else None
                ),
                "total_shares": _f(info_map.get("总股本")),
                "source_id": "em.stock_individual_info",
                "method": "东方财富个股信息",
                "notes": ["当前总股本与行业分类取自东方财富个股信息（当前值，无历史版本）"],
            }
        except Exception:  # noqa: BLE001 - 限流/结构变化都回退，不静默编造
            _EM_INFO_BLOCKED = True

    notes = [
        "东方财富个股信息接口不可用（返回非 JSON），已回退巨潮资讯的股本变动与行业分类。",
        "总股本取巨潮「股本变动」最近一条记录，单位由万股换算为股。",
    ]
    total_shares: float | None = None
    name: str | None = None
    industry: str | None = None
    industry_standard: str | None = None

    try:
        starts = f"{max(int(as_of[:4]) - 20, 1990)}0101"
        ends = as_of.replace("-", "")
        shares = _retry(
            lambda: ak.stock_share_change_cninfo(
                symbol=code, start_date=starts, end_date=ends
            )
        )
        shares.to_csv(cache_dir / f"equity_share_change_{code}.csv", index=False)
        shares = shares.copy()
        shares["变动日期"] = pd.to_datetime(shares["变动日期"], errors="coerce")
        shares = shares.dropna(subset=["变动日期"]).sort_values("变动日期")
        for _, row in shares.iloc[::-1].iterrows():
            value = _f(row.get("总股本"))
            if value is not None and value > 0:
                total_shares = value * 10000.0
                name = name or _text(row.get("证券简称"))
                break
    except Exception as exc:  # noqa: BLE001
        notes.append(f"巨潮股本变动未取得：{type(exc).__name__}: {exc}")

    try:
        starts = f"{max(int(as_of[:4]) - 20, 1990)}0101"
        ends = as_of.replace("-", "")
        industry_frame = _retry(
            lambda: ak.stock_industry_change_cninfo(
                symbol=code, start_date=starts, end_date=ends
            )
        )
        industry_frame.to_csv(cache_dir / f"equity_industry_{code}.csv", index=False)
        industry, industry_standard = _industry_from_cninfo(industry_frame)
        if name is None and not industry_frame.empty:
            name = _text(industry_frame.sort_values("变更日期").iloc[-1].get("新证券简称"))
    except Exception as exc:  # noqa: BLE001
        notes.append(f"巨潮行业分类未取得：{type(exc).__name__}: {exc}")

    if industry_standard:
        notes.append(f"行业分类口径：{industry_standard}（优先申万口径，门类/大类/次类/中类拼接）")

    return {
        "name": name,
        "industry": industry,
        "listing_date": None,
        "total_shares": total_shares,
        "source_id": "cninfo.company_profile",
        "method": "巨潮资讯股本变动 + 行业分类变更",
        "notes": notes,
    }


__all__ = ["company_profile"]
