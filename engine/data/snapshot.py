"""数据快照：固定、可复现、可重放。

- snapshot_id 由内容决定（规范化 JSON 的 SHA-256 前 16 位）；
- 读取时重新计算并比对，不一致就是引擎错误，绝不当正常结果返回；
- 快照之间不共享缓存、不读取旧项目目录。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engine.data.facts import Fact
from engine.data.numeric import sanitize_for_json
from engine.version import SNAPSHOT_SCHEMA_VERSION


class SnapshotError(RuntimeError):
    """快照不可用（不存在、内容损坏、哈希不匹配）。"""


def canonical_json(payload: Any) -> str:
    """确定性序列化：键排序、紧凑分隔符、保留中文。"""
    return json.dumps(
        sanitize_for_json(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_digest(payload: Any, *, length: int = 16) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:length]


@dataclass
class Snapshot:
    """某个标的在某个时点的一组事实。"""

    instrument_id: str
    asset_class: str
    as_of: str  # 数据所代表的日期（YYYY-MM-DD）
    available_at: str  # 该数据对市场公开可得的时间（ISO8601，含时区）
    fetched_at: str  # 实际抓取时间
    facts: list[Fact] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    producers: list[str] = field(default_factory=list)
    notes: str = ""
    schema_version: str = SNAPSHOT_SCHEMA_VERSION

    # --- 身份 -----------------------------------------------------------
    def _identity_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "instrument_id": self.instrument_id,
            "asset_class": self.asset_class,
            "as_of": self.as_of,
            "available_at": self.available_at,
            "facts": [f.to_dict() for f in sorted(self.facts, key=lambda f: f.fact_id)],
            "coverage": self.coverage,
        }

    @property
    def snapshot_id(self) -> str:
        return "snap-" + stable_digest(self._identity_payload())

    # --- 序列化 ---------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["snapshot_id"] = self.snapshot_id
        payload["fetched_at"] = self.fetched_at
        payload["producers"] = sorted(self.producers)
        payload["notes"] = self.notes
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Snapshot":
        snap = cls(
            instrument_id=data["instrument_id"],
            asset_class=data["asset_class"],
            as_of=data["as_of"],
            available_at=data["available_at"],
            fetched_at=data.get("fetched_at", ""),
            facts=[Fact.from_dict(f) for f in data.get("facts", [])],
            coverage=data.get("coverage", {}),
            producers=list(data.get("producers", [])),
            notes=data.get("notes", ""),
            schema_version=data.get("schema_version", SNAPSHOT_SCHEMA_VERSION),
        )
        declared = data.get("snapshot_id")
        if declared and declared != snap.snapshot_id:
            raise SnapshotError(
                f"快照 {declared} 内容校验失败：重算得到 {snap.snapshot_id}"
            )
        return snap

    # --- 查询 -----------------------------------------------------------
    def fact(self, fact_id: str) -> Fact | None:
        for f in self.facts:
            if f.fact_id == fact_id:
                return f
        return None

    def require_fact(self, fact_id: str) -> Fact:
        fact = self.fact(fact_id)
        if fact is None:
            raise SnapshotError(f"快照 {self.snapshot_id} 缺少必需事实 {fact_id}")
        return fact

    def validate(self) -> list[str]:
        problems: list[str] = []
        ids = [f.fact_id for f in self.facts]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            problems.append(f"事实 id 重复: {sorted(dupes)}")
        for f in self.facts:
            problems.extend(f.validate())
        if not self.as_of:
            problems.append("as_of 为空")
        return problems

    def facts_by_category(self) -> dict[str, list[Fact]]:
        out: dict[str, list[Fact]] = {}
        for f in self.facts:
            prefix = f.fact_id.split(".", 1)[0] if "." in f.fact_id else "other"
            out.setdefault(prefix, []).append(f)
        return out


class SnapshotStore:
    """快照目录 + manifest 的只读仓库。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.manifest_path = self.root / "manifest.json"

    def load_manifest(self) -> list[dict[str, Any]]:
        if not self.manifest_path.exists():
            return []
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))["snapshots"]

    def latest_for(self, instrument_id: str) -> str | None:
        """按 as_of 倒序取该标的最新已固定快照。"""
        entries = [e for e in self.load_manifest() if e["instrument_id"] == instrument_id]
        if not entries:
            return None
        entries.sort(key=lambda e: (e["as_of"], e["snapshot_id"]))
        return entries[-1]["snapshot_id"]

    def load(self, snapshot_id: str) -> Snapshot:
        path = self.root / f"{snapshot_id}.json"
        if not path.exists():
            raise SnapshotError(f"找不到快照文件: {path.name}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return Snapshot.from_dict(data)

    def save(self, snapshot: Snapshot) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{snapshot.snapshot_id}.json"
        path.write_text(
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def rebuild_manifest(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("snap-*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            entries.append(
                {
                    "snapshot_id": data["snapshot_id"],
                    "instrument_id": data["instrument_id"],
                    "asset_class": data["asset_class"],
                    "as_of": data["as_of"],
                    "available_at": data["available_at"],
                    "file": path.name,
                }
            )
        entries.sort(key=lambda e: (e["instrument_id"], e["as_of"]))
        self.manifest_path.write_text(
            json.dumps({"snapshots": entries}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return entries


def facts_available_by(snapshot: Snapshot, cutoff: str) -> list[Fact]:
    """按可获得时间过滤事实：cutoff 之后才公开的数据不得出现在历史分析里。

    无法确定可获得时间（available_at 为空）的事实不会被放行，
    因为「不知道何时公布」不能当作「当时已经知道」。
    """
    out: list[Fact] = []
    for fact in snapshot.facts:
        if fact.available_at and fact.available_at <= cutoff:
            out.append(fact)
    return out
