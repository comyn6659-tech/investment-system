#!/usr/bin/env python
"""官方指数当日估值前向记录（只追加）。

用途：每个交易日记录沪深300、中证500、中证1000的官方当日 PE
（中证指数官网 `indexCsiDsPe`；PB 不提供）。本记录**不参与任何计算**，
也不进入个股/指数/ETF 的结论路径。

2026-11-13 股东决定：创业板指（399006）移出研究范围，停止其官方估值前向记录；
已有记录不改，仍保留在账本中。

用法：
    .venv/bin/python tools/update_official_valuation_ledger.py --dry-run
    .venv/bin/python tools/update_official_valuation_ledger.py
    .venv/bin/python tools/update_official_valuation_ledger.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TZ = ZoneInfo("Asia/Singapore")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
LEDGER = ROOT / "data" / "ledger" / "official-valuation-daily.jsonl"
CSI_PE_URL = "https://www.csindex.com.cn/csindex-home/perf/indexCsiDsPe"

CSI_INDICES = (
    ("000300", "沪深300"),
    ("000905", "中证500"),
    ("000852", "中证1000"),
)


def _now() -> datetime:
    return datetime.now(TZ)


def _get_json(url: str, params: dict[str, str], *, timeout: int = 60) -> dict:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _latest_trade_day() -> str:
    today = _now().date()
    while today.weekday() >= 5:
        today = date.fromordinal(today.toordinal() - 1)
    return today.isoformat()


def fetch_csi_pe(code: str) -> dict:
    payload = _get_json(CSI_PE_URL, {"indexCode": code})
    rows = payload.get("data") or []
    if not rows:
        raise RuntimeError(f"中证指数官网没有返回 {code} 的 PE 数据")
    latest = max(rows, key=lambda row: str(row.get("tradeDate") or ""))
    raw = str(latest.get("tradeDate") or "")
    if len(raw) != 8:
        raise RuntimeError(f"{code} 的 tradeDate 格式异常：{raw}")
    return {
        "date": f"{raw[:4]}-{raw[4:6]}-{raw[6:]}",
        "name": latest.get("indexName") or code,
        "pe": float(latest["peg"]),
        "pe_field": "peg",
        "pe_definition": "中证指数官网 indexCsiDsPe 返回字段 peg；接口未说明滚动/静态口径，本记录不擅自标注为滚动",
        "pb": None,
        "pb_note": "中证指数官网未提供该指数 PB 的公开批量接口",
        "source_id": "csindex.indexCsiDsPe",
        "source_url": CSI_PE_URL,
    }


def _record_id(record: dict) -> str:
    body = {key: value for key, value in record.items() if key not in ("record_id", "recorded_at")}
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return "ovd-" + digest[:16]


def build_records() -> list[dict]:
    records = []
    for code, name in CSI_INDICES:
        item = fetch_csi_pe(code)
        item["index_code"] = code
        item["index_name"] = item.pop("name") or name
        records.append(item)
    for record in records:
        record["recorded_at"] = _now().isoformat(timespec="seconds")
        record["record_id"] = _record_id(record)
    return records


def _read_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    rows = []
    with LEDGER.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def check() -> int:
    rows = _read_ledger()
    seen: dict[tuple[str, str], str] = {}
    problems = []
    for index, row in enumerate(rows, start=1):
        expected = _record_id(row)
        if row.get("record_id") != expected:
            problems.append(f"第 {index} 行 record_id 被改写")
        key = (str(row.get("date")), str(row.get("index_code")))
        if key in seen:
            problems.append(f"第 {index} 行重复：{key}")
        seen[key] = row.get("record_id", "")
    if problems:
        print("\n".join(problems))
        return 1
    print(f"官方估值账本 {len(rows)} 条，0 问题")
    return 0


def append(records: list[dict], *, dry_run: bool = False) -> int:
    rows = _read_ledger()
    existing = {(str(row.get("date")), str(row.get("index_code"))): row for row in rows}
    appended = 0
    for record in records:
        key = (record["date"], record["index_code"])
        previous = existing.get(key)
        if previous is not None:
            if previous.get("record_id") != record["record_id"]:
                print(f"冲突：{key} 已存在不同内容，拒绝覆盖")
                return 1
            print(f"跳过（已存在）：{key}")
            continue
        if dry_run:
            print(f"将追加：{key} PE={record.get('pe')} PB={record.get('pb')}")
        else:
            LEDGER.parent.mkdir(parents=True, exist_ok=True)
            with LEDGER.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
        appended += 1
    if dry_run:
        print(f"dry-run：将追加 {appended} 条")
    else:
        print(f"追加 {appended} 条，账本共 {len(rows) + appended} 条")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return check()
    records = build_records()
    for record in records:
        print(
            f"{record['date']} {record['index_code']} {record['index_name']} "
            f"PE={record['pe']} PB={record.get('pb')}"
        )
    return append(records, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
