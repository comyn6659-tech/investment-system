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
from engine.data.timeutil import parse_instant
from engine.version import SNAPSHOT_SCHEMA_VERSION

_ALLOWED_ASSET_CLASSES = {"equity", "index", "etf"}


class SnapshotError(RuntimeError):
    """快照不可用（不存在、内容损坏、哈希不符、结构与请求不匹配）。

    `code` 用于把失败原因映射成结果里的专用原因码，
    避免所有问题都写成「文件不存在」。
    """

    def __init__(self, message: str, *, code: str = "SNAPSHOT_INVALID_STRUCTURE") -> None:
        self.code = code
        super().__init__(message)


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

    # --- 内容哈希 -------------------------------------------------------
    def _content_payload(self) -> dict[str, Any]:
        """参与内容哈希的全部字段。

        规则：**文件里除了 snapshot_id 本身，其余字段全部纳入哈希**，
        包括 fetched_at（版本排序依据）与 notes。这样任何影响准入、排序、
        时点判断的元信息改动都会改变身份，无法在 ID 不变的情况下生效。
        """
        return {
            "schema_version": self.schema_version,
            "instrument_id": self.instrument_id,
            "asset_class": self.asset_class,
            "as_of": self.as_of,
            "available_at": self.available_at,
            "fetched_at": self.fetched_at,
            "facts": [f.to_dict() for f in sorted(self.facts, key=lambda f: f.fact_id)],
            "coverage": self.coverage,
            "producers": sorted(self.producers),
            "notes": self.notes,
        }

    @property
    def snapshot_id(self) -> str:
        return "snap-" + stable_digest(self._content_payload())

    # --- 序列化 ---------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        payload = self._content_payload()
        payload["snapshot_id"] = self.snapshot_id
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, expected_id: str | None = None) -> "Snapshot":
        """反序列化并强制校验。

        规则（缺一不可）：
        1. 必须带 snapshot_id，不能「没有声明就算通过」；
        2. 声明 ID 必须等于内容哈希；
        3. 若调用方指定了请求 ID，声明 ID 必须与之相同（防止文件串号）；
        4. 通过结构校验（重复事实、非法状态与值都要拒绝）。
        """
        for required in ("instrument_id", "asset_class", "as_of", "snapshot_id", "facts"):
            if required not in data:
                raise SnapshotError(
                    f"快照缺少必填字段 {required}", code="SNAPSHOT_INVALID_STRUCTURE"
                )

        declared = data["snapshot_id"]
        if not isinstance(declared, str) or not declared.startswith("snap-"):
            raise SnapshotError(f"非法 snapshot_id: {declared!r}", code="SNAPSHOT_INVALID_STRUCTURE")

        try:
            facts = [Fact.from_dict(f) for f in data.get("facts", [])]
        except Exception as exc:
            raise SnapshotError(
                f"快照事实不合法: {type(exc).__name__}: {exc}",
                code="SNAPSHOT_INVALID_STRUCTURE",
            ) from exc

        snap = cls(
            instrument_id=data["instrument_id"],
            asset_class=data["asset_class"],
            as_of=data["as_of"],
            available_at=data.get("available_at", ""),
            fetched_at=data.get("fetched_at", ""),
            facts=facts,
            coverage=data.get("coverage", {}),
            producers=list(data.get("producers", [])),
            notes=data.get("notes", ""),
            schema_version=data.get("schema_version", SNAPSHOT_SCHEMA_VERSION),
        )

        if declared != snap.snapshot_id:
            raise SnapshotError(
                f"快照 {declared} 内容校验失败：重算得到 {snap.snapshot_id}",
                code="SNAPSHOT_HASH_MISMATCH",
            )
        if expected_id is not None and expected_id != declared:
            raise SnapshotError(
                f"请求的快照 {expected_id} 与文件内容 {declared} 不一致",
                code="SNAPSHOT_ID_MISMATCH",
            )

        problems = snap.validate()
        if problems:
            raise SnapshotError(
                "快照结构校验失败: " + "; ".join(problems), code="SNAPSHOT_INVALID_STRUCTURE"
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
        if str(self.asset_class) not in _ALLOWED_ASSET_CLASSES:
            problems.append(f"未知的 asset_class: {self.asset_class!r}")
        if not str(self.instrument_id).startswith(("EQUITY:", "INDEX:", "ETF:")):
            problems.append(f"instrument_id 格式非法: {self.instrument_id!r}")
        if not self.as_of:
            problems.append("as_of 为空")
        elif len(str(self.as_of)) != 10 or parse_instant(str(self.as_of)) is None:
            problems.append(f"as_of 必须是 YYYY-MM-DD: {self.as_of!r}")

        ids = [f.fact_id for f in self.facts]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            problems.append(f"事实 id 重复: {sorted(dupes)}")
        for f in self.facts:
            problems.extend(f.validate())
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

    @staticmethod
    def _version_key(entry: dict[str, Any]) -> tuple:
        """版本排序：as_of → fetched_at → snapshot_id。

        哈希只用于身份与校验，不能替代修订时间；同一 as_of 的多个版本
        必须按抓取/修订时间排序，否则「数据补齐后仍选旧值」。
        """
        as_of = parse_instant(str(entry.get("as_of") or ""))
        fetched = parse_instant(entry.get("fetched_at"))
        return (
            as_of.value if as_of else 0,
            fetched.value if fetched else 0,
            str(entry.get("snapshot_id", "")),
        )

    def latest_for(self, instrument_id: str) -> str | None:
        """取该标的「最新一版」：先比 as_of，再比抓取时间。"""
        entries = [e for e in self.load_manifest() if e["instrument_id"] == instrument_id]
        if not entries:
            return None
        entries.sort(key=self._version_key)
        return entries[-1]["snapshot_id"]

    def load(self, snapshot_id: str) -> Snapshot:
        if not isinstance(snapshot_id, str) or not snapshot_id.startswith("snap-"):
            raise SnapshotError(f"非法 snapshot_id: {snapshot_id!r}", code="SNAPSHOT_INVALID_STRUCTURE")
        path = self.root / f"{snapshot_id}.json"
        if not path.exists():
            raise SnapshotError(f"找不到快照文件: {path.name}", code="SNAPSHOT_NOT_FOUND")
        data = json.loads(path.read_text(encoding="utf-8"))
        snapshot = Snapshot.from_dict(data, expected_id=snapshot_id)
        self._check_manifest_entry(snapshot)
        return snapshot

    def _check_manifest_entry(self, snapshot: Snapshot) -> None:
        """manifest 与实际快照必须一致（as_of / fetched_at / instrument_id）。"""
        entries = [e for e in self.load_manifest() if e["snapshot_id"] == snapshot.snapshot_id]
        if not entries:
            return
        entry = entries[0]
        for field in ("instrument_id", "as_of", "fetched_at"):
            declared = entry.get(field)
            actual = getattr(snapshot, field)
            if declared is not None and str(declared) != str(actual):
                raise SnapshotError(
                    f"manifest 中的 {field}={declared!r} 与快照实际值 {actual!r} 不一致",
                    code="SNAPSHOT_MANIFEST_MISMATCH",
                )

    def check_consistency(self) -> list[str]:
        """整目录一致性自检：manifest 条目、文件内容、ID、抓取时间是否互相对得上。"""
        problems: list[str] = []
        entries = self.load_manifest()
        seen: set[str] = set()
        for entry in entries:
            sid = entry["snapshot_id"]
            if sid in seen:
                problems.append(f"manifest 中 snapshot_id 重复: {sid}")
            seen.add(sid)
            path = self.root / entry["file"]
            if not path.exists():
                problems.append(f"manifest 指向的文件不存在: {entry['file']}")
                continue
            try:
                snap = Snapshot.from_dict(
                    json.loads(path.read_text(encoding="utf-8")), expected_id=sid
                )
            except SnapshotError as exc:
                problems.append(f"{sid}: {exc}")
                continue
            for field in ("instrument_id", "as_of", "fetched_at"):
                declared = entry.get(field)
                actual = getattr(snap, field)
                if declared is not None and str(declared) != str(actual):
                    problems.append(f"{sid}: manifest {field} 与快照不一致")
        files = {p.name for p in self.root.glob("snap-*.json")}
        for name in sorted(files - {e["file"] for e in entries}):
            problems.append(f"快照文件未登记在 manifest: {name}")
        return problems

    def save(self, snapshot: Snapshot) -> Path:
        problems = snapshot.validate()
        if problems:
            raise SnapshotError(
                "拒绝写入不合法快照: " + "; ".join(problems), code="SNAPSHOT_INVALID_STRUCTURE"
            )
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
                    "fetched_at": data.get("fetched_at", ""),
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

    - 先解析成带时区的时间再比较（不再逐字符比字符串）；
    - 只有日期的公告按「当日结束」处理，因此不会被当成当天 00:00 已知；
    - 无法确定可获得时间（available_at 为空或无法解析）的事实一律不放行。
    """
    from engine.data.timeutil import is_available_by

    out: list[Fact] = []
    for fact in snapshot.facts:
        if is_available_by(fact.available_at, cutoff):
            out.append(fact)
    return out
