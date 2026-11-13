"""官方指数估值前向记录：沪深300 / 中证500 / 中证1000（只追加）。

2026-11-13 股东决定：创业板指（399006）移出研究范围，本模块不再抓取其官方估值；
账本里已有的 399006 历史记录不改。
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Singapore")
ROOT = Path(__file__).resolve().parent.parent.parent
LEDGER = ROOT / "data" / "ledger" / "official-valuation-daily.jsonl"
CSI_PE_URL = "https://www.csindex.com.cn/csindex-home/perf/indexCsiDsPe"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
INDICES = (("000300", "沪深300"), ("000905", "中证500"), ("000852", "中证1000"))
SCHEMA_VERSION = "official-valuation-daily/1"


class OfficialValuationError(RuntimeError):
    pass


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(TZ)


def _get_json(url: str, params: dict[str, str], *, timeout: int = 60) -> dict:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_csi_pe(code: str) -> dict[str, Any]:
    payload = _get_json(CSI_PE_URL, {"indexCode": code})
    rows = payload.get("data") or []
    if not rows:
        raise OfficialValuationError(f"中证指数官网没有返回 {code} 的 PE 数据")
    latest = max(rows, key=lambda row: str(row.get("tradeDate") or ""))
    raw = str(latest.get("tradeDate") or "")
    if len(raw) != 8:
        raise OfficialValuationError(f"{code} 的 tradeDate 格式异常：{raw}")
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


def record_id(record: dict[str, Any]) -> str:
    body = {key: value for key, value in record.items() if key not in ("record_id", "recorded_at")}
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return "ovd-" + digest[:16]


def read_ledger(path: Path | None = None) -> list[dict[str, Any]]:
    target = Path(path or LEDGER)
    if not target.exists():
        return []
    rows: list[dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def check(path: Path | None = None) -> tuple[int, list[str]]:
    rows = read_ledger(path)
    seen: dict[tuple[str, str], str] = {}
    problems: list[str] = []
    for index, row in enumerate(rows, start=1):
        if row.get("record_id") != record_id(row):
            problems.append(f"第 {index} 行 record_id 被改写")
        key = (str(row.get("date")), str(row.get("index_code")))
        if key in seen:
            problems.append(f"第 {index} 行重复：{key}")
        seen[key] = row.get("record_id", "")
    return len(rows), problems


def append_records(
    records: list[dict[str, Any]],
    *,
    path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    target = Path(path or LEDGER)
    rows = read_ledger(target)
    existing = {(str(row.get("date")), str(row.get("index_code"))): row for row in rows}
    added: list[str] = []
    skipped: list[str] = []
    conflicts: list[str] = []
    for record in records:
        key = (str(record["date"]), str(record["index_code"]))
        previous = existing.get(key)
        if previous is not None:
            if previous.get("record_id") != record.get("record_id"):
                conflicts.append(f"{key} 已存在不同内容，拒绝覆盖")
            else:
                skipped.append(f"{key[0]}/{key[1]}")
            continue
        if dry_run:
            added.append(f"{key[0]}/{key[1]}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
        existing[key] = record
        added.append(f"{key[0]}/{key[1]}")
    return {
        "added": added,
        "skipped": skipped,
        "conflicts": conflicts,
        "records_after": len(rows) + (0 if dry_run else len(added)),
        "dry_run": dry_run,
    }


def build_records(*, now: datetime | None = None) -> list[dict[str, Any]]:
    clock = _now(now)
    records: list[dict[str, Any]] = []
    for code, name in INDICES:
        item = fetch_csi_pe(code)
        item["index_code"] = code
        item["index_name"] = item.pop("name") or name
        item["recorded_at"] = clock.isoformat(timespec="seconds")
        item["record_id"] = record_id(item)
        records.append(item)
    return records


def _last_trade_day(clock: datetime) -> str:
    day = clock.date()
    if clock.hour < 17:
        day = day - timedelta(days=1)
    while day.weekday() >= 5:
        day = day - timedelta(days=1)
    return day.isoformat()


def is_fresh(*, now: datetime | None = None) -> bool:
    """账本是否已覆盖最近一个交易日（三个指数都有记录）。"""
    expected = _last_trade_day(_now(now))
    rows = read_ledger()
    covered = {
        str(row.get("index_code"))
        for row in rows
        if str(row.get("date") or "") >= expected
    }
    return all(code in covered for code, _ in INDICES)


def run(*, now: datetime | None = None, skip_network: bool = False, dry_run: bool = False) -> dict[str, Any]:
    if skip_network:
        return {"status": "skipped", "reason": "skip-network", "added": [], "skipped": [], "conflicts": []}
    records = build_records(now=now)
    result = append_records(records, dry_run=dry_run)
    result["status"] = "ok" if not result["conflicts"] else "conflict"
    result["records"] = records
    return result


__all__ = [
    "INDICES",
    "LEDGER",
    "OfficialValuationError",
    "append_records",
    "build_records",
    "check",
    "fetch_csi_pe",
    "read_ledger",
    "record_id",
    "run",
]
