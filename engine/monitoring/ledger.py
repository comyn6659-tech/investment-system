"""监测账本：池剔除记录、扫描记录、变动记录。

只追加、不改旧记录；读取时按池账本的最后一条名单记录计算「当前有效名单」：
名单快照之后追加的 `record_type=exclusion` 记录即红线出池。

`MonitoringStore` 的目录就是账本目录（生产为 `data/ledger`，测试传临时目录），
因此测试不会写真实账本。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Singapore")
EXCLUSION_SCHEMA = "pool-exclusion/1"
CHANGE_SCHEMA = "monitoring-change/1"
SCAN_SCHEMA = "monitoring-scan/1"
POOLS = ("quality", "income", "bank")
CHANGE_TYPES = ("出池", "结论变化", "财报更新", "扫描失败")


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(TZ)


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_id(prefix: str, payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key not in ("record_id", "recorded_at")}
    return prefix + hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()[:16]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} 第 {number} 行不是合法 JSON：{exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path} 第 {number} 行不是对象")
        rows.append(row)
    return rows


def _append_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def iso_week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


class MonitoringStore:
    """一个账本目录上的监测读写。"""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    # --- 池账本 ---------------------------------------------------------
    def pool_path(self, pool: str) -> Path:
        if pool not in POOLS:
            raise ValueError(f"未知的池：{pool}")
        return self.directory / f"{pool}-pool.jsonl"

    def pool_records(self, pool: str) -> list[dict[str, Any]]:
        return _read_jsonl(self.pool_path(pool))

    def membership(self, pool: str) -> dict[str, Any] | None:
        for row in reversed(self.pool_records(pool)):
            if isinstance(row.get("pool_codes"), list):
                return row
        return None

    def exclusion_records(self, pool: str) -> list[dict[str, Any]]:
        rows = self.pool_records(pool)
        latest = -1
        for index, row in enumerate(rows):
            if isinstance(row.get("pool_codes"), list):
                latest = index
        return [
            row
            for row in rows[latest + 1 :]
            if row.get("record_type") == "exclusion"
        ]

    def all_exclusions(self) -> dict[str, dict[str, Any]]:
        """按代码汇总出池记录；同一公司出现在多个池时合并 pools。"""
        merged: dict[str, dict[str, Any]] = {}
        for pool in POOLS:
            for row in self.exclusion_records(pool):
                code = str(row.get("code") or "").zfill(6)
                if not code:
                    continue
                current = merged.get(code)
                if current is None:
                    merged[code] = dict(row, pools=[pool])
                else:
                    pools = set(current.get("pools") or [])
                    pools.add(pool)
                    current["pools"] = sorted(pools)
        return merged

    def effective_sets(self, report_sets: Mapping[str, set[str]]) -> dict[str, set[str]]:
        output: dict[str, set[str]] = {}
        excluded = self.all_exclusions()
        for pool in POOLS:
            record = self.membership(pool)
            base = (
                {str(code).zfill(6) for code in record.get("pool_codes") or []}
                if record is not None
                else set(report_sets.get(pool) or set())
            )
            output[pool] = {code for code in base if code not in excluded}
        return output

    def append_exclusions(
        self,
        entries: Iterable[Mapping[str, Any]],
        *,
        now: datetime | None = None,
    ) -> dict[str, list[str]]:
        """把出池记录追加到公司所在的每个池账本；重复（已出池）跳过。"""
        added: list[str] = []
        skipped: list[str] = []
        existing = self.all_exclusions()
        stamp = _now(now).isoformat(timespec="seconds")
        by_pool: dict[str, list[dict[str, Any]]] = {pool: [] for pool in POOLS}
        for entry in entries:
            code = str(entry.get("code") or "").zfill(6)
            pools = [pool for pool in entry.get("pools") or [] if pool in POOLS]
            if not code or not pools:
                continue
            if code in existing:
                skipped.append(code)
                continue
            record: dict[str, Any] = {
                "schema_version": EXCLUSION_SCHEMA,
                "record_type": "exclusion",
                "as_of": str(entry.get("as_of") or _now(now).date().isoformat()),
                "policy_id": str(entry.get("policy_id") or ""),
                "policy_digest": str(entry.get("policy_digest") or ""),
                "code": code,
                "name": str(entry.get("name") or ""),
                "pools": sorted(pools),
                "status": str(entry.get("status") or ""),
                "red_lines": list(entry.get("red_lines") or []),
                "recorded_at": stamp,
            }
            record["record_id"] = stable_id("pex-", record)
            for pool in pools:
                by_pool[pool].append(record)
            existing[code] = record
            added.append(code)
        for pool, rows in by_pool.items():
            _append_jsonl(self.pool_path(pool), rows)
        return {"added": added, "skipped": skipped}

    # --- 变动记录 -------------------------------------------------------
    def changes_path(self) -> Path:
        return self.directory / "changes.jsonl"

    def changes(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.changes_path())

    def append_changes(
        self,
        entries: Iterable[Mapping[str, Any]],
        *,
        now: datetime | None = None,
    ) -> list[str]:
        stamp = _now(now)
        existing = {str(row.get("record_id")) for row in self.changes()}
        rows: list[dict[str, Any]] = []
        added: list[str] = []
        for entry in entries:
            row: dict[str, Any] = {
                "schema_version": CHANGE_SCHEMA,
                "date": str(entry.get("date") or stamp.date().isoformat()),
                "type": str(entry.get("type") or ""),
                "code": str(entry.get("code") or "").zfill(6) if entry.get("code") else "",
                "name": str(entry.get("name") or ""),
                "detail": str(entry.get("detail") or ""),
                "evidence": dict(entry.get("evidence") or {}),
                "recorded_at": stamp.isoformat(timespec="seconds"),
            }
            if row["type"] not in CHANGE_TYPES:
                raise ValueError(f"未知的变动类型：{row['type']}")
            row["record_id"] = stable_id("chg-", row)
            if row["record_id"] in existing:
                continue
            rows.append(row)
            existing.add(row["record_id"])
            added.append(row["record_id"])
        _append_jsonl(self.changes_path(), rows)
        return added

    # --- 扫描记录 -------------------------------------------------------
    def scans_path(self) -> Path:
        return self.directory / "monitoring-scans.jsonl"

    def scans(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.scans_path())

    def last_scan(self) -> dict[str, Any] | None:
        rows = self.scans()
        return rows[-1] if rows else None

    def scanned_in_week(self, day: date) -> bool:
        week = iso_week(day)
        return any(str(row.get("week")) == week for row in self.scans())

    def append_scan(self, record: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        row = dict(record)
        row.setdefault("schema_version", SCAN_SCHEMA)
        row.setdefault("recorded_at", _now(now).isoformat(timespec="seconds"))
        row["record_id"] = stable_id("mss-", row)
        if any(existing.get("record_id") == row["record_id"] for existing in self.scans()):
            return row
        _append_jsonl(self.scans_path(), [row])
        return row


__all__ = [
    "CHANGE_TYPES",
    "MonitoringStore",
    "POOLS",
    "iso_week",
    "stable_id",
]
