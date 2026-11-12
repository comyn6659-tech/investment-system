"""个股正式结论的只追加账本。

规则（施工单 P6）：
- 每条记录：分析日期、代码、政策版本、快照 ID、价格及日期、三档价值、
  等级、安全边际、结论、起约束条件、记录哈希；
- 同一标的、同一分析日、同一快照重复运行且内容一致 → 跳过；
- 内容不同 → 冲突，拒绝覆盖；
- `--check` 重新计算哈希，发现历史被改写即报错；
- `undetermined` 不写入。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from engine.data.snapshot import stable_digest

LEDGER_SCHEMA_VERSION = "equity-conclusion-ledger/1"

_CORE_FIELDS = (
    "instrument_id",
    "code",
    "analysis_date",
    "policy_version",
    "policy_digest",
    "snapshot_id",
    "price",
    "price_date",
    "values",
    "grade",
    "margin_of_safety",
    "conclusion",
    "binding_condition",
)


class ConclusionLedgerError(RuntimeError):
    """账本不可用或格式非法。"""


class ConclusionConflict(ConclusionLedgerError):
    """同一标的、分析日、快照已有不同内容：拒绝覆盖。"""


@dataclass(frozen=True)
class ConclusionRecord:
    instrument_id: str
    code: str
    analysis_date: str
    policy_version: str
    policy_digest: str
    snapshot_id: str
    price: float
    price_date: str
    values: dict[str, float]
    grade: str
    margin_of_safety: float | None
    conclusion: str
    binding_condition: str
    recorded_at: str = ""

    def core(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "code": self.code,
            "analysis_date": self.analysis_date,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "snapshot_id": self.snapshot_id,
            "price": self.price,
            "price_date": self.price_date,
            "values": dict(self.values),
            "grade": self.grade,
            "margin_of_safety": self.margin_of_safety,
            "conclusion": self.conclusion,
            "binding_condition": self.binding_condition,
        }

    @property
    def record_id(self) -> str:
        return "concl-" + stable_digest(self.core())

    def key(self) -> tuple[str, str, str]:
        return (self.instrument_id, self.analysis_date, self.snapshot_id)

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.core())
        payload["record_id"] = self.record_id
        payload["schema_version"] = LEDGER_SCHEMA_VERSION
        payload["recorded_at"] = self.recorded_at
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConclusionRecord":
        missing = [key for key in _CORE_FIELDS if key not in data]
        if missing:
            raise ConclusionLedgerError(f"账本记录缺少字段 {missing}")
        values = data.get("values") or {}
        if not isinstance(values, dict) or not {"pessimistic", "base", "optimistic"} <= set(values):
            raise ConclusionLedgerError("账本 values 必须含 pessimistic/base/optimistic")
        return cls(
            instrument_id=str(data["instrument_id"]),
            code=str(data["code"]),
            analysis_date=str(data["analysis_date"]),
            policy_version=str(data["policy_version"]),
            policy_digest=str(data["policy_digest"]),
            snapshot_id=str(data["snapshot_id"]),
            price=float(data["price"]),
            price_date=str(data["price_date"]),
            values={key: float(value) for key, value in values.items()},
            grade=str(data["grade"]),
            margin_of_safety=(
                None if data.get("margin_of_safety") is None else float(data["margin_of_safety"])
            ),
            conclusion=str(data["conclusion"]),
            binding_condition=str(data["binding_condition"]),
            recorded_at=str(data.get("recorded_at", "")),
        )


class ConclusionLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

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
                raise ConclusionLedgerError(f"账本第 {number} 行不是合法 JSON：{exc}") from exc
            if not isinstance(data, dict):
                raise ConclusionLedgerError(f"账本第 {number} 行不是对象")
            if data.get("schema_version") != LEDGER_SCHEMA_VERSION:
                raise ConclusionLedgerError(
                    f"账本第 {number} 行 schema 非法：{data.get('schema_version')!r}"
                )
            record = ConclusionRecord.from_dict(data)
            declared = data.get("record_id")
            if declared is not None and declared != record.record_id:
                raise ConclusionLedgerError(
                    f"账本第 {number} 行声明 {declared}，内容哈希为 {record.record_id}：历史被改写"
                )
            records.append(data)
        return records

    def append(self, records: Iterable[ConclusionRecord]) -> dict[str, list[str]]:
        existing = self.load()
        index = {(r["instrument_id"], r["analysis_date"], r["snapshot_id"]): r for r in existing}
        added: list[str] = []
        skipped: list[str] = []
        rows: list[dict[str, Any]] = []
        for record in records:
            if record.conclusion == "undetermined":
                raise ConclusionLedgerError("undetermined 不得写入结论账本")
            current = index.get(record.key())
            if current is not None:
                if current["record_id"] == record.record_id:
                    skipped.append(record.record_id)
                    continue
                raise ConclusionConflict(
                    f"{record.instrument_id} 在 {record.analysis_date} / {record.snapshot_id} "
                    f"已有记录 {current['record_id']}，与本次 {record.record_id} 不一致："
                    "账本只追加，不允许改写历史"
                )
            rows.append(record.to_dict())
            added.append(record.record_id)
        if rows:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return {"added": added, "skipped": skipped}

    def check(self) -> list[str]:
        problems: list[str] = []
        try:
            records = self.load()
        except ConclusionLedgerError as exc:
            return [str(exc)]
        seen: dict[tuple[str, str, str], str] = {}
        for number, record in enumerate(records, start=1):
            key = (
                str(record["instrument_id"]),
                str(record["analysis_date"]),
                str(record["snapshot_id"]),
            )
            if key in seen:
                problems.append(f"第 {number} 行与已有记录重复：{key}")
            seen[key] = str(record["record_id"])
            if record.get("conclusion") == "undetermined":
                problems.append(f"第 {number} 行是 undetermined，不应写入账本")
        return problems


__all__ = [
    "ConclusionConflict",
    "ConclusionLedger",
    "ConclusionLedgerError",
    "ConclusionRecord",
    "LEDGER_SCHEMA_VERSION",
]
