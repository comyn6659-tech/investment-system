"""好公司池固定快照：按“报告期 × 报表类型”保存的压缩表 + 表级清单。

施工单补充要求（2026-11-12）：
- 全市场数据用 csv.gz 表格，不用逐只 JSON；
- 来源与接口在每张表的元数据里记录一次；
- 内容哈希按表计算，清单汇总后得到快照 id；
- 原始下载缓存不入 git，只提交快照目录与清单。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from engine.quality.build import build_companies
from engine.quality.models import GovernancePenalty
from engine.quality.tables import TableError, read_table, verify_table

SNAPSHOT_SCHEMA_VERSION = "quality-pool-snapshot/2"
SNAPSHOT_ID_PREFIX = "qps-"


class SnapshotError(RuntimeError):
    """快照清单、表哈希或结构不合法。"""


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes", "y")


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def manifest_hash(manifest: Mapping[str, Any]) -> str:
    body = {key: value for key, value in manifest.items() if key not in ("snapshot_id", "content_hash")}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def snapshot_id_for(manifest: Mapping[str, Any]) -> str:
    return SNAPSHOT_ID_PREFIX + manifest_hash(manifest)[:16]


def build_snapshot_dir(
    table_paths: Iterable[Path],
    *,
    parent: Path,
    as_of: str,
    window: Mapping[str, Any],
    scope: Mapping[str, Any],
    sources: list[Mapping[str, Any]],
    untested_conditions: list[Mapping[str, Any]],
    build_stats: Mapping[str, Any] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """复制压缩表到固定快照目录，写表级清单并返回 (目录, manifest)。"""
    entries = []
    for path in table_paths:
        path = Path(path)
        meta_path = path.with_name(path.name + ".meta.json")
        if not path.exists() or not meta_path.exists():
            raise SnapshotError(f"表或元数据缺失：{path}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        verify_table(path, meta)
        entries.append(dict(meta, file=path.name))
    entries.sort(key=lambda item: item["file"])

    manifest: dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "as_of": as_of,
        "window": dict(window),
        "scope": dict(scope),
        "tables": entries,
        "sources": [dict(source) for source in sources],
        "untested_conditions": [dict(item) for item in untested_conditions],
        "total_bytes": sum(int(item.get("size_bytes") or 0) for item in entries),
        "build_stats": dict(build_stats or {}),
    }
    digest = manifest_hash(manifest)
    manifest["content_hash"] = digest
    manifest["snapshot_id"] = SNAPSHOT_ID_PREFIX + digest[:16]

    target = Path(parent) / f"snapshot-{manifest['snapshot_id']}"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for path in table_paths:
        shutil.copy2(path, target / Path(path).name)
        shutil.copy2(
            Path(path).with_name(Path(path).name + ".meta.json"),
            target / f"{Path(path).name}.meta.json",
        )
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target, manifest


def _verify_manifest(manifest: Mapping[str, Any], directory: Path) -> None:
    if manifest.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotError("快照 schema_version 不受支持")
    expected_hash = manifest_hash(manifest)
    if manifest.get("content_hash") != expected_hash:
        raise SnapshotError("快照 content_hash 与清单不一致")
    if manifest.get("snapshot_id") != SNAPSHOT_ID_PREFIX + expected_hash[:16]:
        raise SnapshotError("快照 snapshot_id 与清单哈希不一致")
    for entry in manifest.get("tables") or []:
        path = directory / str(entry.get("file"))
        verify_table(path, entry)


def load_snapshot_dir(path: Path) -> tuple[dict[str, Any], list[Any], dict[str, Any]]:
    """校验清单与每张表，重建 Company 列表；返回 (manifest, companies, stats)。"""
    directory = Path(path)
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        raise SnapshotError(f"快照清单不存在：{manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        _verify_manifest(manifest, directory)
    except TableError as exc:
        raise SnapshotError(str(exc)) from exc

    by_kind: dict[str, dict[int, dict[str, dict]]] = {"balance": {}, "income": {}, "cashflow": {}}
    dividends: list[dict] = []
    universe: list[dict] = []
    industry: dict[str, str] = {}
    pledge: dict[str, float] = {}
    audit: dict[str, dict[int, str]] = {}
    penalties: dict[str, list[dict]] = {}
    pledge_governance: dict[str, dict] = {}
    coverage: dict[str, dict] = {}
    for entry in manifest.get("tables") or []:
        rows = read_table(directory / str(entry["file"]))
        kind = entry.get("kind")
        if kind in by_kind:
            year = int(str(entry.get("report_date"))[:4])
            by_kind[kind][year] = {str(row["SECUCODE"])[:6]: row for row in rows}
        elif kind == "dividends":
            dividends.extend(rows)
        elif kind == "universe":
            universe = rows
        elif kind == "industry":
            industry = {
                str(row["code"]).zfill(6): row["industry"]
                for row in rows
                if row.get("industry")
            }
        elif kind == "pledge":
            pledge = {
                str(row["code"]).zfill(6): float(row["pledge_ratio"])
                for row in rows
                if row.get("pledge_ratio")
            }
        elif kind == "governance_audit":
            for row in rows:
                audit.setdefault(str(row["code"]).zfill(6), {})[int(row["fiscal_year"])] = row["opinion_type"]
        elif kind == "governance_penalty":
            for row in rows:
                penalties.setdefault(str(row["code"]).zfill(6), []).append(row)
        elif kind == "governance_pledge":
            for row in rows:
                pledge_governance[str(row["code"]).zfill(6)] = row
        elif kind == "governance_coverage":
            for row in rows:
                coverage[str(row["code"]).zfill(6)] = row

    window = manifest.get("window") or {}
    scope = manifest.get("scope") or {}
    companies, stats = build_companies(
        universe,
        by_kind,
        dividends,
        pledge,
        industry,
        financial_industries=set(scope.get("exclude_financial_industries") or []),
        window_start=int(window["start_fy"]),
        window_end=int(window["end_fy"]),
        min_years=int(window["min_years"]),
    )
    if audit or penalties or pledge_governance or coverage:
        enriched = []
        for company in companies:
            cov = coverage.get(company.code) or {}
            pledge_row = pledge_governance.get(company.code) or {}
            enriched.append(
                replace(
                    company,
                    audit_opinions=audit.get(company.code, {}),
                    audit_available=_truthy(cov.get("audit_available")),
                    penalties=tuple(
                        GovernancePenalty.from_dict(row)
                        for row in penalties.get(company.code, [])
                        if _truthy(row.get("counted"))
                    ),
                    penalty_available=_truthy(cov.get("penalty_available")),
                    controlling_shareholder=pledge_row.get("controller") or None,
                    controlling_pledge_ratio=(
                        float(pledge_row["pledge_ratio"])
                        if pledge_row.get("pledge_ratio") not in (None, "")
                        else None
                    ),
                    pledge_report_date=pledge_row.get("report_date") or None,
                    pledge_available=_truthy(cov.get("pledge_available")),
                    pledge_note=pledge_row.get("note") or None,
                    governance_in_scope=bool(cov),
                )
            )
        companies = enriched
    return manifest, companies, stats
