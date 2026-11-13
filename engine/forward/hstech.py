"""恒生科技指数官方月度估值前向记录（只追加）。

官方指数单张（月度 PDF）提供当前市盈率与股息率，不提供市净率；官方没有
HSTECH 的估值历史文件。因此这里按月记录官方月度快照，不做分位、信号或回测。
数据源、解析口径与 `tools/build_hstech_data.py` 一致。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Singapore")
ROOT = Path(__file__).resolve().parent.parent.parent
LEDGER = ROOT / "data" / "ledger" / "hstech-monthly-valuation.jsonl"
FACTSHEET_URL = "https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy/v1/content/gcs/download"
FACTSHEET_RESOURCE = "/public-website/webapp/factsheets/en-hk/hstech.pdf"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
SCHEMA_VERSION = "hstech-monthly-valuation/1"


class HSTechError(RuntimeError):
    pass


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(TZ)


def _get(url: str, params: dict[str, str], *, timeout: int = 90) -> bytes:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _en_date(match) -> str | None:
    if not match:
        return None
    months = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    return f"{int(match.group(3)):04d}-{months[match.group(2).lower()]:02d}-{int(match.group(1)):02d}"


def _cn_date(match) -> str | None:
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def parse_factsheet_text(text: str) -> dict[str, Any]:
    normalized = unicodedata.normalize("NFKC", text)
    row = re.search(r"(?:恒生科技指数|HSTECH)\s+([0-9.]+)\s+([0-9.]+)", normalized)
    if not row:
        raise HSTechError("官方指数单张没有找到恒生科技指数股息率/市盈率行")
    dividend_yield = float(row.group(1))
    pe_ratio = float(row.group(2))
    as_of = (
        _en_date(re.search(r"All data as at\s*(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", normalized))
        or _cn_date(re.search(r"所有数据截至\s*(\d{4})年(\d{1,2})月(\d{1,2})日", normalized))
    )
    if not as_of:
        raise HSTechError("官方指数单张没有找到数据截止日")
    return {
        "as_of": as_of,
        "pe_ratio": pe_ratio,
        "dividend_yield_pct": dividend_yield,
        "pb": None,
        "pb_note": "官方指数单张不提供市净率；公开历史估值文件也没有 HSTECH。",
        "source_id": "hsi.factsheet",
        "source_url": FACTSHEET_URL,
        "source_note": "恒生指数有限公司官方指数单张（月度 PDF）",
    }


def fetch_factsheet(*, timeout: int = 90) -> dict[str, Any]:
    from pypdf import PdfReader

    content = _get(FACTSHEET_URL, {"resource": FACTSHEET_RESOURCE}, timeout=timeout)
    reader = PdfReader(BytesIO(content))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return parse_factsheet_text(text)


def record_id(record: dict[str, Any]) -> str:
    body = {key: value for key, value in record.items() if key not in ("record_id", "recorded_at")}
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return "hsv-" + digest[:16]


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
    seen: set[str] = set()
    problems: list[str] = []
    for index, row in enumerate(rows, start=1):
        if row.get("record_id") != record_id(row):
            problems.append(f"第 {index} 行 record_id 被改写")
        key = str(row.get("date"))
        if key in seen:
            problems.append(f"第 {index} 行重复：{key}")
        seen.add(key)
    return len(rows), problems


def append_record(
    record: dict[str, Any], *, path: Path | None = None, dry_run: bool = False
) -> dict[str, Any]:
    target = Path(path or LEDGER)
    rows = read_ledger(target)
    existing = {str(row.get("date")): row for row in rows}
    key = str(record["date"])
    previous = existing.get(key)
    if previous is not None:
        if previous.get("record_id") != record.get("record_id"):
            return {"status": "conflict", "reason": f"{key} 已存在不同内容，拒绝覆盖", "date": key}
        return {"status": "skipped", "reason": "同一月份内容一致", "date": key}
    if dry_run:
        return {"status": "would_append", "date": key}
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
    return {"status": "appended", "date": key, "records_after": len(rows) + 1}


def build_record(*, now: datetime | None = None) -> dict[str, Any]:
    clock = _now(now)
    item = fetch_factsheet()
    record = {
        "schema_version": SCHEMA_VERSION,
        "date": item["as_of"],
        "frequency": "monthly",
        "index_code": "HSTECH",
        "index_name": "恒生科技指数",
        "pe": item["pe_ratio"],
        "dividend_yield_pct": item["dividend_yield_pct"],
        "pb": None,
        "pb_note": item["pb_note"],
        "source_id": item["source_id"],
        "source_url": item["source_url"],
        "source_note": item["source_note"],
        "recorded_at": clock.isoformat(timespec="seconds"),
    }
    record["record_id"] = record_id(record)
    return record


def is_fresh(*, now: datetime | None = None) -> bool:
    """是否已有上个月及以后的官方月度记录（官方月度单张按月更新）。"""
    clock = _now(now)
    first_of_previous_month = (clock.date().replace(day=1) - timedelta(days=1)).replace(day=1)
    rows = read_ledger()
    if not rows:
        return False
    latest = max(str(row.get("date") or "") for row in rows)
    return latest >= first_of_previous_month.isoformat()


def run(*, now: datetime | None = None, skip_network: bool = False, dry_run: bool = False) -> dict[str, Any]:
    if skip_network:
        return {"status": "skipped", "reason": "skip-network"}
    record = build_record(now=now)
    result = append_record(record, dry_run=dry_run)
    result["record"] = record
    return result


__all__ = [
    "HSTechError",
    "LEDGER",
    "append_record",
    "build_record",
    "check",
    "fetch_factsheet",
    "parse_factsheet_text",
    "read_ledger",
    "record_id",
    "run",
]
