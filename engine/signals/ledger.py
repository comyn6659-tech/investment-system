"""只追加的指数研究信号账本。

规则（施工单 B.2）：
- 每次运行只追加「新出现的周度观察点」；已有记录不可修改；
- 同一标的同一周只允许一条记录；重复运行内容一致则跳过，不一致则报冲突；
- `record_id` 由记录内容（不含追加时刻）哈希得到，可用于校验历史未被改写。

文件格式：JSON Lines，一行一条记录，按追加顺序排列。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from engine.data.snapshot import stable_digest

LEDGER_SCHEMA_VERSION = "signal-ledger/1"
RULE_VERSION = "candidate-a/1"

# 参与内容哈希的字段：改动其中任何一个都视为改写历史
_CORE_FIELDS = (
    "instrument_id",
    "week",
    "date",
    "pe",
    "percentile",
    "evaluable",
    "signal",
    "series_ids",
    "rule_version",
    "engine_version",
)


class LedgerError(RuntimeError):
    """账本不可用或格式非法。"""


class LedgerConflict(LedgerError):
    """同一标的同一周已存在不同内容的记录：拒绝覆盖。"""


@dataclass(frozen=True)
class LedgerRecord:
    instrument_id: str
    week: str
    date: str
    pe: float | None
    percentile: float | None
    evaluable: bool
    signal: str
    series_ids: dict[str, str | None]
    rule_version: str = RULE_VERSION
    engine_version: str = ""
    recorded_at: str = ""

    def core(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "week": self.week,
            "date": self.date,
            "pe": self.pe,
            "percentile": self.percentile,
            "evaluable": self.evaluable,
            "signal": self.signal,
            "series_ids": dict(self.series_ids),
            "rule_version": self.rule_version,
            "engine_version": self.engine_version,
        }

    @property
    def record_id(self) -> str:
        return "sig-" + stable_digest(self.core())

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.core())
        payload["record_id"] = self.record_id
        payload["schema_version"] = LEDGER_SCHEMA_VERSION
        payload["recorded_at"] = self.recorded_at
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LedgerRecord":
        missing = [key for key in _CORE_FIELDS if key not in data]
        if missing:
            raise LedgerError(f"账本记录缺少字段 {missing}: {data}")
        return cls(
            instrument_id=str(data["instrument_id"]),
            week=str(data["week"]),
            date=str(data["date"]),
            pe=data["pe"],
            percentile=data["percentile"],
            evaluable=bool(data["evaluable"]),
            signal=str(data["signal"]),
            series_ids=dict(data.get("series_ids") or {}),
            rule_version=str(data.get("rule_version", RULE_VERSION)),
            engine_version=str(data.get("engine_version", "")),
            recorded_at=str(data.get("recorded_at", "")),
        )


class SignalLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # --- 读取 -----------------------------------------------------------
    def load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            text = line.strip()
            if not text:
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise LedgerError(f"账本第 {number} 行不是合法 JSON：{exc}") from exc
            if not isinstance(data, dict):
                raise LedgerError(f"账本第 {number} 行不是对象")
            if data.get("schema_version") != LEDGER_SCHEMA_VERSION:
                raise LedgerError(f"账本第 {number} 行 schema 非法：{data.get('schema_version')!r}")
            record = LedgerRecord.from_dict(data)
            declared = data.get("record_id")
            if declared is not None and declared != record.record_id:
                raise LedgerError(
                    f"账本第 {number} 行声明 {declared}，内容哈希为 {record.record_id}：历史被改写"
                )
            records.append(data)
        return records

    def last_date(self, instrument_id: str) -> str | None:
        dates = [
            str(r["date"]) for r in self.load() if r.get("instrument_id") == instrument_id
        ]
        return max(dates) if dates else None

    # --- 追加 -----------------------------------------------------------
    def append(self, records: Iterable[LedgerRecord]) -> dict[str, list[str]]:
        existing = self.load()
        index = {(r["instrument_id"], r["week"]): r for r in existing}
        added: list[str] = []
        skipped: list[str] = []
        new_rows: list[dict[str, Any]] = []
        for record in records:
            key = (record.instrument_id, record.week)
            if key in index:
                if index[key]["record_id"] == record.record_id:
                    skipped.append(record.record_id)
                    continue
                raise LedgerConflict(
                    f"{record.instrument_id} 的 {record.week} 已有记录 "
                    f"{index[key]['record_id']}，与本次 {record.record_id} 不一致："
                    "账本只追加，不允许改写历史"
                )
            new_rows.append(record.to_dict())
            added.append(record.record_id)
        if new_rows:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                for row in new_rows:
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return {"added": added, "skipped": skipped}

    # --- 自检 -----------------------------------------------------------
    def check(self) -> list[str]:
        problems: list[str] = []
        try:
            records = self.load()
        except LedgerError as exc:
            return [str(exc)]
        seen: dict[tuple[str, str], str] = {}
        last_date: dict[str, str] = {}
        for number, record in enumerate(records, start=1):
            key = (record["instrument_id"], record["week"])
            if key in seen:
                problems.append(f"第 {number} 行与已有记录重复：{key}")
            seen[key] = record["record_id"]
            previous = last_date.get(record["instrument_id"])
            if previous is not None and str(record["date"]) <= previous:
                problems.append(
                    f"第 {number} 行日期 {record['date']} 未晚于上一条 {previous}：追加顺序异常"
                )
            last_date[record["instrument_id"]] = str(record["date"])
        return problems


__all__ = [
    "LEDGER_SCHEMA_VERSION",
    "LedgerConflict",
    "LedgerError",
    "LedgerRecord",
    "RULE_VERSION",
    "SignalLedger",
]
