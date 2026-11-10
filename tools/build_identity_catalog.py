#!/usr/bin/env python
"""生成 A 股个股清单（data/registry/cn_a_equities.json）。

为什么需要它：
    个股名称→代码的解析不能只靠少数手写条目，但也不能用「六位数字=A股」猜。
    这里从交易所官方清单（沪、北）与行情清单（深）取数，固化代码-名称-板块，
    并记录来源与抓取时间。

限制（会在 data/coverage 报告中同样记录）：
    深交所官方清单接口当前报错，深市条目退化为行情清单推导；
    不代表可以据此推断指数或 ETF。

用法：
    .venv/bin/python tools/build_identity_catalog.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "registry" / "cn_a_equities.json"
TZ = ZoneInfo("Asia/Singapore")


def _norm_date(value) -> str | None:
    if value is None:
        return None
    try:
        return str(value)[:10]
    except Exception:  # pragma: no cover
        return None


def main() -> int:
    now = datetime.now(TZ).isoformat(timespec="seconds")
    rows: list[list] = []
    evidence: list[dict] = []
    notes: list[str] = []

    # --- 上交所 ---------------------------------------------------------
    for symbol in ("主板A股", "科创板"):
        try:
            df = ak.stock_info_sh_name_code(symbol=symbol)
            for _, r in df.iterrows():
                rows.append(
                    [str(r["证券代码"]).zfill(6), str(r["证券简称"]), "SSE", _norm_date(r.get("上市日期"))]
                )
            evidence.append(
                {
                    "evidence_id": f"ev-sse-equity-list-{symbol}",
                    "kind": "exchange_list",
                    "publisher": "上海证券交易所",
                    "url": "https://www.sse.com.cn/assortment/stock/list/share/",
                    "retrieved_at": now,
                    "fields_cited": ["证券代码", "证券简称", "上市日期"],
                    "note": f"上交所上市公司清单（{symbol}），共 {len(df)} 条",
                }
            )
        except Exception as exc:  # pragma: no cover - 网络/上游变化
            notes.append(f"上交所清单 {symbol} 获取失败: {type(exc).__name__}: {exc}")

    # --- 深交所（失败则退化为行情清单）---------------------------------
    szse_ok = False
    try:
        df = ak.stock_info_sz_name_code(symbol="A股列表")
        for _, r in df.iterrows():
            rows.append(
                [str(r["A股代码"]).zfill(6), str(r["A股简称"]), "SZSE", _norm_date(r.get("A股上市日期"))]
            )
        szse_ok = True
        evidence.append(
            {
                "evidence_id": "ev-szse-equity-list",
                "kind": "exchange_list",
                "publisher": "深圳证券交易所",
                "url": "https://www.szse.cn/market/product/stock/list/index.html",
                "retrieved_at": now,
                "fields_cited": ["A股代码", "A股简称", "A股上市日期"],
                "note": f"深交所 A 股清单，共 {len(df)} 条",
            }
        )
    except Exception as exc:
        notes.append(f"深交所官方清单获取失败({type(exc).__name__})，深市条目退化为行情清单推导")

    if not szse_ok:
        spot = ak.stock_zh_a_spot_em()
        sz = spot[spot["代码"].astype(str).str.startswith(("00", "30"))]
        for _, r in sz.iterrows():
            rows.append([str(r["代码"]).zfill(6), str(r["名称"]), "SZSE", None])
        evidence.append(
            {
                "evidence_id": "ev-em-equity-spot",
                "kind": "vendor_api",
                "publisher": "东方财富（经 AKShare stock_zh_a_spot_em）",
                "url": "https://quote.eastmoney.com/center/gridlist.html#hs_a_board",
                "retrieved_at": now,
                "fields_cited": ["代码", "名称"],
                "note": f"行情清单推导深市A股，共 {len(sz)} 条；无上市日期",
            }
        )

    # --- 北交所 ---------------------------------------------------------
    try:
        df = ak.stock_info_bj_name_code()
        for _, r in df.iterrows():
            rows.append(
                [str(r["证券代码"]).zfill(6), str(r["证券简称"]), "BSE", _norm_date(r.get("上市日期"))]
            )
        evidence.append(
            {
                "evidence_id": "ev-bse-equity-list",
                "kind": "exchange_list",
                "publisher": "北京证券交易所",
                "url": "https://www.bse.cn/nq/listedcompany.html",
                "retrieved_at": now,
                "fields_cited": ["证券代码", "证券简称", "上市日期"],
                "note": f"北交所上市公司清单，共 {len(df)} 条",
            }
        )
    except Exception as exc:  # pragma: no cover
        notes.append(f"北交所清单获取失败: {type(exc).__name__}: {exc}")

    # 去重（同一代码+板块只保留一条）
    seen: set[tuple[str, str]] = set()
    unique: list[list] = []
    for row in rows:
        key = (row[0], row[2])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    unique.sort(key=lambda r: (r[2], r[0]))

    payload = {
        "schema_version": "equity-list/1",
        "description": "境内 A 股个股清单（仅用于名称/代码→个股身份；不参与指数或 ETF 判定）",
        "generated_at": now,
        "source": {
            "evidence_id": "ev-cn-a-equity-list",
            "kind": "exchange_list",
            "publisher": "上海证券交易所 / 深圳证券交易所 / 北京证券交易所（经 AKShare 采集）",
            "url": None,
            "retrieved_at": now,
            "note": "；".join(notes) if notes else "三个交易所清单均取到",
        },
        "evidence": evidence,
        "columns": ["code", "name", "venue", "listing_date"],
        "count": len(unique),
        "rows": unique,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"写入 {OUT} 共 {len(unique)} 条；备注: {notes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
