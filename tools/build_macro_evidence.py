#!/usr/bin/env python
"""采集宏观折现率基准（中国国债收益率），供研究假设引用。

只记录**观测值 + 来源 + 取数时间**；不用它替设计方决定股权风险溢价，
也不把任何收益率当成已批准政策。

输出：data/evidence/macro/market-rates.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "data" / "evidence" / "macro"
TZ = ZoneInfo("Asia/Singapore")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="采集国债收益率基准")
    parser.add_argument("--start", default="20260901", help="起始日期 YYYYMMDD")
    parser.add_argument("--as-of", dest="as_of", default=None, help="只取该日期的观测")
    args = parser.parse_args(argv)

    now = datetime.now(TZ)
    frame = ak.bond_zh_us_rate(start_date=args.start)
    frame = frame.dropna(subset=["中国国债收益率10年"])
    if frame.empty:
        print("没有可用的中国国债收益率观测", file=sys.stderr)
        return 1
    frame["日期"] = frame["日期"].astype(str)
    if args.as_of:
        rows = frame[frame["日期"] == args.as_of]
        if rows.empty:
            print(f"{args.as_of} 没有观测：不倒填相邻日期", file=sys.stderr)
            return 1
        row = rows.iloc[0]
    else:
        row = frame.iloc[-1]

    payload = {
        "schema_version": "market-rates/1",
        "as_of": str(row["日期"]),
        "fetched_at": now.isoformat(timespec="seconds"),
        "rates_pct": {
            "cn_10y": float(row["中国国债收益率10年"]),
            "cn_5y": None if pd.isna(row.get("中国国债收益率5年")) else float(row["中国国债收益率5年"]),
            "cn_30y": None if pd.isna(row.get("中国国债收益率30年")) else float(row["中国国债收益率30年"]),
        },
        "source": {
            "source_id": "em.bond_zh_us_rate",
            "publisher": "东方财富（中债口径汇总）",
            "interface": "AKShare bond_zh_us_rate",
            "url": "https://data.eastmoney.com/cjsj/globalRateBond.html",
            "retrieved_at": now.isoformat(timespec="seconds"),
            "note": "中国国债到期收益率，日频；用于研究假设中的无风险利率参照",
        },
        "usage_note": (
            "无风险利率是观测值；股权风险溢价与公司特定溢价是本轮研究假设候选，"
            "不由本文件决定，也不构成已批准政策。"
        ),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"market-rates-{payload['as_of']}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
