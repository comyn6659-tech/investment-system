#!/usr/bin/env python
"""构建个股结构化业务证据（有出处、可重放）。

只采集**可核实的事实**，不做主观打分：
- 行业分类（东方财富个股信息，仅作为线索）；
- 主营构成（按产品与按地区，含收入占比与毛利率）；
- 近若干年营业收入与归母净利润（用于观察周期性/亏损年份）。

模型适用性由 engine/evidence/applicability.py 依据这些事实与规则判定，
并把判定理由与版本一起写进分析结果——不按股票代码特殊放行，也不看当前股价。

用法：
    .venv/bin/python tools/build_business_evidence.py
    .venv/bin/python tools/build_business_evidence.py --only 600519,601318
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
sys.path.insert(0, str(ROOT / "tools"))

from company_profile import company_profile  # noqa: E402

from engine.data.snapshot import canonical_json  # noqa: E402
from engine.evidence.models import BusinessEvidence  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
EVIDENCE_DIR = ROOT / "data" / "evidence"
CACHE_DIR = ROOT / "data" / "cache"

# 样本：三条已实现路线 + 三条首版未实现路线（用于证明「不被默认送进 DCF」）
DEFAULT_CODES = ("600519", "600900", "601398", "601318", "600030", "601088")
ANNUAL_POINTS = 8


def _venue(code: str) -> str:
    return "SH" if code.startswith(("6", "9")) else "SZ"


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


def _parse_cn_amount(value) -> float | None:
    """解析「1,234.5亿」「56.7万」这类中文金额文本。"""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in ("False", "nan", "None", "--"):
        return None
    unit = 1.0
    if text.endswith("万亿"):
        unit, text = 1e12, text[:-2]
    elif text.endswith("亿"):
        unit, text = 1e8, text[:-1]
    elif text.endswith("万"):
        unit, text = 1e4, text[:-1]
    try:
        return float(text) * unit
    except ValueError:
        return None


def build_evidence(code: str, *, as_of: str, now: datetime) -> dict:
    stamp = now.isoformat(timespec="seconds")
    venue = _venue(code)
    sources: list[dict] = []

    profile = company_profile(code, as_of=as_of, cache_dir=CACHE_DIR)
    name = str(profile.get("name") or code)
    industry = profile.get("industry")
    if profile["source_id"] == "em.stock_individual_info":
        sources.append(
            {
                "evidence_id": "ev-em-stock-info",
                "kind": "vendor_api",
                "publisher": "东方财富",
                "interface": "AKShare stock_individual_info_em",
                "url": "https://quote.eastmoney.com/",
                "retrieved_at": stamp,
                "fields_cited": ["股票简称", "行业", "上市时间", "总股本"],
                "note": "行业分类只是模型选择的线索之一，不单独决定模型",
            }
        )
    else:
        sources.append(
            {
                "evidence_id": "ev-cninfo-company-profile",
                "kind": "vendor_api",
                "publisher": "巨潮资讯",
                "interface": (
                    "AKShare stock_industry_change_cninfo + stock_share_change_cninfo"
                ),
                "url": "https://webapi.cninfo.com.cn/#/company",
                "retrieved_at": stamp,
                "fields_cited": ["证券简称", "行业门类", "行业大类", "行业次类", "行业中类", "总股本"],
                "note": (
                    "东方财富个股信息不可用时回退巨潮：行业取最近一次分类（优先申万口径），"
                    "总股本取股本变动最近一条；行业分类只是线索，不单独决定模型"
                ),
            }
        )

    zygc = ak.stock_zygc_em(symbol=f"{venue}{code}")
    zygc.to_csv(CACHE_DIR / f"business_zygc_{code}.csv", index=False)
    report_dates = [str(x)[:10] for x in zygc["报告日期"].unique()]
    latest = max(report_dates) if report_dates else None
    by_product: list[dict] = []
    by_region: list[dict] = []
    if latest:
        latest_rows = zygc[zygc["报告日期"].astype(str).str[:10] == latest]
        for _, row in latest_rows.iterrows():
            kind = str(row.get("分类类型") or "")
            segment = {
                "segment": str(row.get("主营构成") or ""),
                "revenue_share": _f(row.get("收入比例")),
                "revenue": _f(row.get("主营收入")),
                "gross_margin": _f(row.get("毛利率")),
            }
            if "产品" in kind:
                by_product.append(segment)
            elif "地区" in kind:
                by_region.append(segment)
    sources.append(
        {
            "evidence_id": "ev-em-zygc",
            "kind": "vendor_api",
            "publisher": "东方财富",
            "interface": "AKShare stock_zygc_em（主营构成）",
            "url": "https://emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/Index",
            "retrieved_at": stamp,
            "fields_cited": ["报告日期", "分类类型", "主营构成", "收入比例", "毛利率"],
            "note": "按最新报告期披露的主营构成；收入比例为占比，不是金额",
        }
    )

    abstract = ak.stock_financial_abstract(symbol=code)
    annual: list[dict] = []
    revenue_row = abstract[abstract["指标"] == "营业总收入"]
    profit_row = abstract[abstract["指标"] == "归母净利润"]
    if not revenue_row.empty and not profit_row.empty:
        revenue_row = revenue_row.iloc[0]
        profit_row = profit_row.iloc[0]
        for column in abstract.columns:
            if not (isinstance(column, str) and len(column) == 8 and column.endswith("1231")):
                continue
            year = int(column[:4])
            revenue = _parse_cn_amount(revenue_row.get(column))
            profit = _parse_cn_amount(profit_row.get(column))
            if revenue is None and profit is None:
                continue
            annual.append(
                {
                    "fiscal_year": year,
                    "revenue": revenue,
                    "net_profit_parent": profit,
                }
            )
    annual.sort(key=lambda x: x["fiscal_year"], reverse=True)
    annual = annual[:ANNUAL_POINTS]
    sources.append(
        {
            "evidence_id": "ev-em-financial-abstract",
            "kind": "vendor_api",
            "publisher": "东方财富",
            "interface": "AKShare stock_financial_abstract（关键指标）",
            "url": "https://data.eastmoney.com/bbsj/",
            "retrieved_at": stamp,
            "fields_cited": ["营业总收入", "归母净利润"],
            "note": "按年报期（YYYY1231）取值；原表为中文金额文本，已换算为元",
        }
    )

    total_shares = profile.get("total_shares")
    payload = {
        "schema_version": "business-evidence/1",
        "instrument_id": f"EQUITY:{code}.{venue}",
        "display_name": name,
        "as_of": as_of,
        "prepared_at": stamp,
        "version": as_of,
        "industry_clue": industry,
        "business_mix": {
            "report_date": latest,
            "by_product": by_product,
            "by_region": by_region,
        },
        "annual_history": annual,
        "share_count_current": total_shares,
        "sources": sources,
        "notes": [
            "本文件只记录可核实事实与出处；模型适用性判定见 engine/evidence/applicability.py。",
            "行业分类只作为线索，不单独决定模型；判定不使用当前股价。",
        ],
    }
    # 内容哈希由 BusinessEvidence 统一定义，避免采集端与读取端口径不一致
    payload["evidence_id"] = BusinessEvidence.from_dict(payload).evidence_id
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建个股结构化业务证据")
    parser.add_argument("--only", default=None, help="只构建指定代码，逗号分隔")
    parser.add_argument("--as-of", dest="as_of", default="2026-11-10", help="证据适用日期")
    args = parser.parse_args(argv)

    codes = tuple(x.strip() for x in args.only.split(",")) if args.only else DEFAULT_CODES
    now = datetime.now(TZ)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    built = []
    for code in codes:
        payload = build_evidence(code, as_of=args.as_of, now=now)
        path = EVIDENCE_DIR / f"{payload['instrument_id'].replace(':', '-')}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        built.append(
            {
                "instrument_id": payload["instrument_id"],
                "evidence_id": payload["evidence_id"],
                "file": path.name,
                "industry": payload["industry_clue"],
                "segments": [s["segment"] for s in payload["business_mix"]["by_product"]][:4],
                "annual_points": len(payload["annual_history"]),
            }
        )
        print(json.dumps(built[-1], ensure_ascii=False), file=sys.stderr)
    print(json.dumps({"built_at": now.isoformat(timespec="seconds"), "evidence": built}, ensure_ascii=False, indent=2))
    _ = canonical_json
    return 0


if __name__ == "__main__":
    sys.exit(main())
