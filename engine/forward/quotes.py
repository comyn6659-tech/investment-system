"""全市场行情快照的前向刷新（只追加、重复跳过）。

行情来自腾讯财经 `qt.gtimg.cn`，与第十批批量输入同源同解析口径：
- 收盘前取上一交易日收盘，收盘后取当日收盘；价格日期逐行记录；
- 每个交易日一个不可变文件 `data/pool-valuation/live/quotes-YYYY-MM-DD.json`；
- 同一价格日期再次运行：内容一致跳过，内容不同报冲突，不覆盖。
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from engine.data.snapshot import stable_digest
from engine.forward.calendar import is_trading_day, previous_trading_day

TZ = ZoneInfo("Asia/Singapore")
ROOT = Path(__file__).resolve().parent.parent.parent
LIVE_DIR = ROOT / "data" / "pool-valuation" / "live"
EQUITY_LIST = ROOT / "data" / "registry" / "cn_a_equities.json"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
QUOTE_URL = "https://qt.gtimg.cn/"
CHUNK = 200
SCHEMA_VERSION = "live-quotes/1"


class QuoteError(RuntimeError):
    pass


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(TZ)


def _stamp(now: datetime) -> str:
    return now.isoformat(timespec="seconds")


def _codes() -> list[str]:
    payload = json.loads(EQUITY_LIST.read_text(encoding="utf-8"))
    rows = payload["rows"]
    columns = list(payload.get("columns") or [])
    code_index = columns.index("code") if "code" in columns else 0
    venue_index = columns.index("venue") if "venue" in columns else None
    codes = set()
    for row in rows:
        if venue_index is not None and str(row[venue_index]) not in ("SSE", "SZSE"):
            continue  # 北交所不在好公司池/个股政策范围，也不在批量估值表中
        codes.add(str(row[code_index]).zfill(6))
    return sorted(codes)


def _prev_trading_day(day: date) -> date:
    return previous_trading_day(day)


def fetch_quotes(codes: list[str], *, now: datetime | None = None, timeout: int = 30) -> list[dict]:
    """抓取全市场行情；返回逐行字典（code/name/price/price_date/previous_close）。"""
    clock = _now(now)
    use_previous = clock.hour < 15
    rows: list[dict] = []
    for start in range(0, len(codes), CHUNK):
        chunk = codes[start : start + CHUNK]
        symbols = ",".join(("sh" if code.startswith("6") else "sz") + code for code in chunk)
        request = urllib.request.Request(
            f"{QUOTE_URL}q={symbols}", headers={"User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("gbk", errors="ignore")
        for line in text.splitlines():
            match = re.match(r'v_(?:sh|sz)(\d{6})="(.+)"', line.strip())
            if not match:
                continue
            code, payload = match.groups()
            fields = payload.split("~")
            if len(fields) < 40:
                continue
            try:
                current = float(fields[3])
                previous = float(fields[4]) if fields[4] else None
            except (TypeError, ValueError):
                continue
            if current <= 0:
                continue
            timestamp = next((item for item in fields if re.fullmatch(r"\d{14}", item)), None)
            quote_day = (
                date(int(timestamp[:4]), int(timestamp[4:6]), int(timestamp[6:8]))
                if timestamp
                else clock.date()
            )
            if use_previous and previous:
                price = previous
                price_day = _prev_trading_day(quote_day - timedelta(days=1))
            else:
                price = current
                price_day = quote_day
            rows.append(
                {
                    "code": code,
                    "name": fields[1],
                    "price": price,
                    "price_date": price_day.isoformat(),
                    "previous_close": previous,
                }
            )
        time.sleep(0.05)
    if not rows:
        raise QuoteError("腾讯行情没有返回任何可用行")
    return rows


def _payload(rows: list[dict], *, now: datetime) -> dict[str, Any]:
    dates = sorted({row["price_date"] for row in rows})
    if not dates:
        raise QuoteError("行情快照没有价格日期")
    price_date = dates[-1]
    body = {
        "schema_version": SCHEMA_VERSION,
        "price_date": price_date,
        "mixed_price_dates": len(dates) > 1,
        "all_price_dates": dates,
        "fetched_at": _stamp(now),
        "source": {
            "source_id": "tencent.quote_snapshot",
            "publisher": "腾讯财经",
            "interface": "qt.gtimg.cn 全市场行情快照（分批）",
            "url": QUOTE_URL,
            "definition_note": (
                "全市场一次性行情快照；收盘前取上一交易日收盘价，收盘后取当日收盘价；"
                "价格日期逐行记录。"
            ),
        },
        "rows": rows,
    }
    body["content_hash"] = stable_digest(
        {key: body[key] for key in ("schema_version", "price_date", "rows")}
    )
    return body


def _load_manifest() -> dict[str, Any]:
    path = LIVE_DIR / "manifest.json"
    if not path.exists():
        return {"schema_version": "live-quotes-manifest/1", "snapshots": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_manifest(manifest: dict[str, Any]) -> None:
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    (LIVE_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_manifest() -> dict[str, Any]:
    return _load_manifest()


def latest_snapshot() -> dict[str, Any] | None:
    """最新一个行情快照（按价格日期）。"""
    manifest = _load_manifest()
    entries = sorted(manifest.get("snapshots") or [], key=lambda item: item["price_date"])
    if not entries:
        return None
    entry = entries[-1]
    path = LIVE_DIR / entry["file"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("content_hash") != stable_digest(
        {
            "schema_version": payload["schema_version"],
            "price_date": payload["price_date"],
            "rows": payload["rows"],
        }
    ):
        raise QuoteError(f"行情快照内容哈希不一致：{path.name}")
    return payload


def latest_quotes() -> dict[str, dict]:
    """code → 行情行；没有快照时返回空字典。"""
    payload = latest_snapshot()
    if payload is None:
        return {}
    return {row["code"]: row for row in payload["rows"]}


def expected_price_date(now: datetime | None = None) -> str:
    clock = _now(now)
    day = clock.date()
    if clock.hour < 15:
        day = _prev_trading_day(day - timedelta(days=1))
    else:
        day = _prev_trading_day(day)
    return day.isoformat()


def is_fresh(now: datetime | None = None) -> bool:
    payload = latest_snapshot()
    if payload is None:
        return False
    return payload["price_date"] >= expected_price_date(now)


def refresh(*, now: datetime | None = None, skip_network: bool = False) -> dict[str, Any]:
    """刷新全市场行情；已存在同一价格日期则跳过。返回摘要（不抛网络错误以外的异常）。"""
    clock = _now(now)
    if not is_trading_day(clock.date()):
        payload = latest_snapshot()
        return {
            "status": "skipped",
            "reason": "非交易日，跳过行情抓取",
            "price_date": payload["price_date"] if payload else None,
            "rows": len(payload["rows"]) if payload else 0,
            "file": f"quotes-{payload['price_date']}.json" if payload else None,
        }
    payload = latest_snapshot()
    expected = expected_price_date(clock)
    if payload is not None and payload["price_date"] >= expected:
        return {
            "status": "skipped",
            "reason": "已有不早于预期价格日期的快照",
            "price_date": payload["price_date"],
            "rows": len(payload["rows"]),
            "file": f"quotes-{payload['price_date']}.json",
        }
    if skip_network:
        return {"status": "skipped", "reason": "skip-network", "price_date": expected, "rows": 0}
    rows = fetch_quotes(_codes(), now=clock)
    body = _payload(rows, now=clock)
    file_name = f"quotes-{body['price_date']}.json"
    path = LIVE_DIR / file_name
    manifest = _load_manifest()
    existing = next(
        (item for item in manifest.get("snapshots") or [] if item["price_date"] == body["price_date"]),
        None,
    )
    if existing is not None:
        if existing.get("content_hash") != body["content_hash"]:
            return {
                "status": "conflict",
                "reason": f"{body['price_date']} 已有不同内容的行情快照，拒绝覆盖",
                "price_date": body["price_date"],
                "rows": len(body["rows"]),
                "file": file_name,
            }
        return {
            "status": "skipped",
            "reason": "同一价格日期内容一致",
            "price_date": body["price_date"],
            "rows": len(body["rows"]),
            "file": file_name,
        }
    if path.exists():
        return {
            "status": "conflict",
            "reason": f"{file_name} 已存在但不在清单中，拒绝覆盖",
            "price_date": body["price_date"],
            "rows": len(body["rows"]),
            "file": file_name,
        }
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    manifest.setdefault("snapshots", []).append(
        {
            "file": file_name,
            "price_date": body["price_date"],
            "rows": len(body["rows"]),
            "content_hash": body["content_hash"],
            "fetched_at": body["fetched_at"],
        }
    )
    manifest["snapshots"].sort(key=lambda item: item["price_date"])
    _write_manifest(manifest)
    return {
        "status": "appended",
        "reason": "新行情快照",
        "price_date": body["price_date"],
        "rows": len(body["rows"]),
        "file": file_name,
    }


__all__ = [
    "LIVE_DIR",
    "QuoteError",
    "expected_price_date",
    "fetch_quotes",
    "is_fresh",
    "latest_quotes",
    "latest_snapshot",
    "load_manifest",
    "refresh",
]
