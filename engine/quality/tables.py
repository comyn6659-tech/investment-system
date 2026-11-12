"""全市场表格的压缩存取与表级内容哈希。

施工单（2026-11-12 补充）要求：全市场数据按“报告期 × 报表类型”存成压缩表格
（csv.gz），来源与接口在每张表的元数据里统一记录一次，内容哈希按表计算；
不再使用“一只股票一个 JSON、每个数字带来源”的快照格式。
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

TABLE_SCHEMA_VERSION = "quality-pool-table/1"


class TableError(RuntimeError):
    """表格缺失、哈希不一致或元数据不合法。"""


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_table(
    path: Path,
    rows: Iterable[Mapping[str, Any]],
    columns: list[str],
    *,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """写 csv.gz + 同名 `.meta.json`；返回表元数据（含哈希与行数）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    materialised = list(rows)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in materialised:
            writer.writerow({column: row.get(column) for column in columns})
    meta = {
        "schema_version": TABLE_SCHEMA_VERSION,
        "table_id": path.stem.replace(".csv", ""),
        "file": path.name,
        "rows": len(materialised),
        "columns": list(columns),
        "content_hash": file_hash(path),
        "size_bytes": path.stat().st_size,
        **dict(metadata),
    }
    meta_path = path.with_name(path.name + ".meta.json")
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return meta


def read_table(path: Path) -> list[dict[str, str]]:
    path = Path(path)
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def verify_table(path: Path, metadata: Mapping[str, Any]) -> None:
    path = Path(path)
    if not path.exists():
        raise TableError(f"表不存在：{path}")
    actual = file_hash(path)
    if metadata.get("content_hash") != actual:
        raise TableError(f"表内容哈希不一致：{path.name}")
    if metadata.get("rows") != sum(1 for _ in gzip.open(path, "rt", encoding="utf-8")) - 1:
        raise TableError(f"表行数与元数据不一致：{path.name}")
