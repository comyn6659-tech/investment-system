"""个股正式结论的只追加账本。

规则（施工单 P6 + 第十一批收尾 T2）：
- 每条记录：分析日期、代码、政策版本、快照 ID、价格及日期、三档价值、
  等级、安全边际、结论、起约束条件、记录哈希；
- 同一标的、同一分析日、同一快照的同类记录重复运行且内容一致 → 跳过；
- 内容不同 → 冲突，拒绝覆盖；
- `--check` 重新计算哈希，发现历史被改写即报错；
- `undetermined` 不写入，除非是「改判为无法判断」：该标的账本中最新一条正式结论
  （候选买点/等待/回避）在本次评估中变为无法判断时，追加一条
  `record_type="reversal"` 的记录，带原因码、中文原因、政策版本、快照 ID、分析日
  与 `reversal_of`（被改判的正式记录 id）；旧记录不改。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from engine.data.snapshot import stable_digest

LEDGER_SCHEMA_VERSION = "equity-conclusion-ledger/1"
FORMAL_CONCLUSIONS = ("candidate_buy_point", "wait", "avoid")
REVERSAL_TYPE = "reversal"
FORMAL_TYPE = "formal"
_REQUIRED_COMMON = (
    "instrument_id",
    "code",
    "analysis_date",
    "policy_version",
    "policy_digest",
    "snapshot_id",
    "conclusion",
)
_FORMAL_FIELDS = (
    "price",
    "price_date",
    "values",
    "grade",
    "margin_of_safety",
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
    price: float | None
    price_date: str
    values: dict[str, float]
    grade: str
    margin_of_safety: float | None
    conclusion: str
    binding_condition: str
    recorded_at: str = ""
    record_type: str = FORMAL_TYPE
    reason_codes: tuple[str, ...] = ()
    reason: str = ""
    reversal_of: str = ""

    def core(self) -> dict[str, Any]:
        payload = {
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
        # 旧记录不带这些字段；只有改判记录才写入，保证既有 record_id 不变。
        if self.record_type != FORMAL_TYPE or self.reason_codes or self.reason or self.reversal_of:
            payload.update(
                {
                    "record_type": self.record_type,
                    "reason_codes": list(self.reason_codes),
                    "reason": self.reason,
                    "reversal_of": self.reversal_of,
                }
            )
        return payload

    @property
    def record_id(self) -> str:
        return "concl-" + stable_digest(self.core())

    def key(self) -> tuple[str, str, str, str, str]:
        return (
            self.instrument_id,
            self.analysis_date,
            self.snapshot_id,
            self.record_type,
            self.reversal_of,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.core())
        payload["record_id"] = self.record_id
        payload["schema_version"] = LEDGER_SCHEMA_VERSION
        payload["recorded_at"] = self.recorded_at
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConclusionRecord":
        missing = [key for key in _REQUIRED_COMMON if key not in data]
        if missing:
            raise ConclusionLedgerError(f"账本记录缺少字段 {missing}")
        record_type = str(data.get("record_type") or FORMAL_TYPE)
        if record_type not in (FORMAL_TYPE, REVERSAL_TYPE):
            raise ConclusionLedgerError(f"账本 record_type 非法：{record_type!r}")
        conclusion = str(data["conclusion"])
        values = data.get("values") or {}
        if record_type == REVERSAL_TYPE:
            if conclusion != "undetermined":
                raise ConclusionLedgerError("改判记录结论必须为 undetermined")
            if not isinstance(values, dict):
                raise ConclusionLedgerError("改判记录 values 必须是对象")
            return cls(
                instrument_id=str(data["instrument_id"]),
                code=str(data["code"]),
                analysis_date=str(data["analysis_date"]),
                policy_version=str(data["policy_version"]),
                policy_digest=str(data["policy_digest"]),
                snapshot_id=str(data["snapshot_id"]),
                price=None if data.get("price") is None else float(data["price"]),
                price_date=str(data.get("price_date") or ""),
                values={key: float(value) for key, value in values.items()},
                grade=str(data.get("grade") or ""),
                margin_of_safety=(
                    None if data.get("margin_of_safety") is None else float(data["margin_of_safety"])
                ),
                conclusion=conclusion,
                binding_condition=str(data.get("binding_condition") or ""),
                recorded_at=str(data.get("recorded_at", "")),
                record_type=record_type,
                reason_codes=tuple(str(item) for item in (data.get("reason_codes") or ())),
                reason=str(data.get("reason") or ""),
                reversal_of=str(data.get("reversal_of") or ""),
            )

        missing_formal = [key for key in _FORMAL_FIELDS if key not in data]
        if missing_formal:
            raise ConclusionLedgerError(f"账本记录缺少字段 {missing_formal}")
        if not isinstance(values, dict) or not {"pessimistic", "base", "optimistic"} <= set(values):
            raise ConclusionLedgerError("账本 values 必须含 pessimistic/base/optimistic")
        if data.get("price") is None:
            raise ConclusionLedgerError("正式结论记录 price 不能为空")
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
            conclusion=conclusion,
            binding_condition=str(data["binding_condition"]),
            recorded_at=str(data.get("recorded_at", "")),
            record_type=record_type,
            reason_codes=tuple(str(item) for item in (data.get("reason_codes") or ())),
            reason=str(data.get("reason") or ""),
            reversal_of=str(data.get("reversal_of") or ""),
        )


def latest_for_instrument(records: list[dict[str, Any]], instrument_id: str) -> dict[str, Any] | None:
    """按账本行顺序返回该标的最新一条记录（最后追加的）。"""
    for row in reversed(records):
        if str(row.get("instrument_id")) == instrument_id:
            return row
    return None


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
        index = {
            (
                r["instrument_id"],
                r["analysis_date"],
                r["snapshot_id"],
                r.get("record_type", FORMAL_TYPE),
                r.get("reversal_of", ""),
            ): r
            for r in existing
        }
        added: list[str] = []
        skipped: list[str] = []
        rows: list[dict[str, Any]] = []
        reversed_targets = {
            str(r.get("reversal_of")) for r in existing if r.get("record_type") == REVERSAL_TYPE
        }
        for record in records:
            if record.record_type == REVERSAL_TYPE:
                if not record.reason_codes or not record.reason or not record.reversal_of:
                    raise ConclusionLedgerError("改判记录必须带原因码、中文原因与 reversal_of")
                target = next(
                    (r for r in existing if str(r.get("record_id")) == record.reversal_of), None
                )
                if target is None:
                    raise ConclusionLedgerError(f"找不到被改判记录：{record.reversal_of}")
                if str(target.get("instrument_id")) != record.instrument_id:
                    raise ConclusionLedgerError("改判记录的标的与被改判记录不一致")
                if target.get("conclusion") not in FORMAL_CONCLUSIONS:
                    raise ConclusionLedgerError("只能改判正式结论（候选买点/等待/回避）")
                if record.reversal_of in reversed_targets:
                    raise ConclusionConflict(f"记录 {record.reversal_of} 已有改判记录")
            elif record.conclusion == "undetermined":
                raise ConclusionLedgerError("undetermined 不得写入结论账本（除非是改判记录）")
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
            if record.record_type == REVERSAL_TYPE:
                reversed_targets.add(record.reversal_of)
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
        seen: dict[tuple[str, str, str, str, str], str] = {}
        by_id = {str(record["record_id"]): record for record in records}
        reversed_targets: dict[str, str] = {}
        for number, record in enumerate(records, start=1):
            key = (
                str(record["instrument_id"]),
                str(record["analysis_date"]),
                str(record["snapshot_id"]),
                str(record.get("record_type", FORMAL_TYPE)),
                str(record.get("reversal_of", "")),
            )
            if key in seen:
                problems.append(f"第 {number} 行与已有记录重复：{key}")
            seen[key] = str(record["record_id"])
            conclusion = record.get("conclusion")
            record_type = record.get("record_type", FORMAL_TYPE)
            if conclusion == "undetermined":
                if record_type != REVERSAL_TYPE:
                    problems.append(f"第 {number} 行是 undetermined，但不是改判记录")
                    continue
                if not record.get("reason_codes"):
                    problems.append(f"第 {number} 行改判记录缺原因码")
                if not record.get("reason"):
                    problems.append(f"第 {number} 行改判记录缺中文原因")
                target_id = str(record.get("reversal_of") or "")
                target = by_id.get(target_id)
                if target is None:
                    problems.append(f"第 {number} 行改判记录找不到被改判记录 {target_id}")
                    continue
                if str(target.get("instrument_id")) != str(record.get("instrument_id")):
                    problems.append(f"第 {number} 行改判记录与被改判记录标的不一致")
                if target.get("conclusion") not in FORMAL_CONCLUSIONS:
                    problems.append(f"第 {number} 行只能改判正式结论")
                if target_id in reversed_targets:
                    problems.append(
                        f"第 {number} 行重复改判 {target_id}（首次在第 {reversed_targets[target_id]} 行）"
                    )
                reversed_targets[target_id] = str(number)
            elif record_type == REVERSAL_TYPE:
                problems.append(f"第 {number} 行标记为改判但结论不是 undetermined")
            elif conclusion not in FORMAL_CONCLUSIONS:
                problems.append(f"第 {number} 行结论非法：{conclusion!r}")
        return problems


__all__ = [
    "ConclusionConflict",
    "ConclusionLedger",
    "ConclusionLedgerError",
    "ConclusionRecord",
    "FORMAL_CONCLUSIONS",
    "LEDGER_SCHEMA_VERSION",
    "latest_for_instrument",
]
