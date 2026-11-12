#!/usr/bin/env python
"""把好公司池的原始 JSONL 缓存转成“报告期 × 报表类型”的 csv.gz 表格。

补充要求（2026-11-12）：全市场数据不再用逐只 JSON；原始缓存不提交 git，
只提交固定快照与清单；原始数据总量控制在 1 GB 以内。

用法：
    .venv/bin/python tools/compress_quality_pool_tables.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.quality.columns import (  # noqa: E402
    BALANCE_COLUMNS,
    CASHFLOW_COLUMNS,
    DIVIDEND_COLUMNS,
    INCOME_COLUMNS,
    UNIVERSE_COLUMNS,
)
from engine.quality.tables import file_hash, write_table  # noqa: E402

CACHE = ROOT / "data" / "cache" / "quality-pool"
RAW = CACHE / "raw"
TABLES = CACHE / "tables"

STATEMENTS = {
    "balance": ("RPT_F10_FINANCE_GBALANCE", BALANCE_COLUMNS, "eastmoney.f10.balance"),
    "income": ("RPT_F10_FINANCE_GINCOME", INCOME_COLUMNS, "eastmoney.f10.income"),
    "cashflow": ("RPT_F10_FINANCE_GCASHFLOW", CASHFLOW_COLUMNS, "eastmoney.f10.cashflow"),
}
EASTMONEY_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def convert_statements() -> list[dict]:
    metas = []
    for kind, (report_name, columns, source_id) in STATEMENTS.items():
        for meta_path in sorted((RAW / "financials").glob(f"*/{kind}.meta.json")):
            year = meta_path.parent.name
            jsonl = meta_path.with_name(meta_path.name.replace(".meta.json", ".jsonl"))
            if not jsonl.exists():
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            rows = _read_jsonl(jsonl)
            table = write_table(
                TABLES / "financials" / f"{kind}-{year}.csv.gz",
                rows,
                columns,
                metadata={
                    "kind": kind,
                    "report_date": f"{year}-12-31",
                    "source_id": source_id,
                    "publisher": "东方财富",
                    "interface": report_name,
                    "url": EASTMONEY_URL,
                    "fetched_at": meta.get("fetched_at"),
                    "definition_note": "合并报表；金额单位元；按报告期一次取全市场。",
                },
            )
            metas.append(table)
            jsonl.unlink()
            meta_path.unlink()
            print(f"{kind} {year}: {table['rows']} 行 -> {table['size_bytes']} 字节", flush=True)
    return metas


def convert_dividends() -> list[dict]:
    metas = []
    for meta_path in sorted((RAW / "dividends").glob("*.meta.json")):
        period = meta_path.stem.replace(".meta", "")
        jsonl = meta_path.with_name(meta_path.name.replace(".meta.json", ".jsonl"))
        if not jsonl.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        rows = _read_jsonl(jsonl)
        table = write_table(
            TABLES / "dividends" / f"dividends-{period}.csv.gz",
            rows,
            DIVIDEND_COLUMNS,
            metadata={
                "kind": "dividends",
                "report_date": period,
                "source_id": "eastmoney.dividend",
                "publisher": "东方财富",
                "interface": "RPT_SHAREBONUS_DET",
                "url": "https://datacenter-web.eastmoney.com/api/data/v1/get",
                "fetched_at": meta.get("fetched_at"),
                "definition_note": "每 10 股派现（含税）与总股本；按报告期取方案。",
            },
        )
        metas.append(table)
        jsonl.unlink()
        meta_path.unlink()
        print(f"dividends {period}: {table['rows']} 行 -> {table['size_bytes']} 字节", flush=True)
    return metas


def convert_universe() -> dict | None:
    path = RAW / "universe.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    table = write_table(
        TABLES / "universe.csv.gz",
        payload["rows"],
        UNIVERSE_COLUMNS,
        metadata={
            "kind": "universe",
            "source_id": "sse.szse.listing",
            "publisher": "上海证券交易所 / 深圳证券交易所",
            "interface": "股票列表",
            "url": "https://query.sse.com.cn/；https://www.szse.cn/",
            "fetched_at": payload.get("fetched_at"),
            "definition_note": "当前沪深 A 股；上市日期、板块、简称；ST 按简称识别。",
        },
    )
    path.unlink()
    print(f"universe: {table['rows']} 行 -> {table['size_bytes']} 字节", flush=True)
    return table


def main() -> int:
    TABLES.mkdir(parents=True, exist_ok=True)
    metas = convert_statements() + convert_dividends()
    universe = convert_universe()
    if universe:
        metas.append(universe)
    manifest = {
        "schema_version": "quality-pool-raw-manifest/1",
        "tables": metas,
        "total_bytes": sum(meta["size_bytes"] for meta in metas),
    }
    (TABLES / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"完成：{len(metas)} 张表，压缩后 {manifest['total_bytes'] / 1024 / 1024:.2f} MB",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
