"""正式结论的追溯信息：记录所用财务快照、行情快照与政策文件哈希。

只由写入方调用；不改估值与结论逻辑。旧记录没有这些字段，`--recompute` 会明确
报告「不可复算」或按可追溯的批量快照尽力重算。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from engine.forward import quotes

ROOT = Path(__file__).resolve().parent.parent.parent
LIVE_DIR = ROOT / "data" / "pool-valuation" / "live"


class TraceError(RuntimeError):
    """追溯字段无法生成（引用文件不存在或不在已提交目录）。"""


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    path = Path(path)
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError as exc:
        raise TraceError(f"追溯文件不在仓库内：{path}") from exc


def _file_ref(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise TraceError(f"追溯引用的文件不存在：{path}")
    return {"path": _relative(path), "sha256": file_sha256(path)}


def policy_ref(policy_path: Path, policy_id: str | None = None) -> dict[str, Any]:
    """政策文件引用；要求文件存在且在仓库内。"""
    path = Path(policy_path)
    if not path.exists():
        raise TraceError(f"政策文件不存在：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "policy_id": policy_id or str(payload.get("policy_id") or ""),
        "sha256": file_sha256(path),
        "path": _relative(path),
    }


def live_quote_reference(price_date: str, *, directory: Path | None = None) -> dict[str, Any] | None:
    """按价格日期找已提交的行情快照；找不到返回 None。"""
    base = Path(directory) if directory is not None else LIVE_DIR
    manifest_path = base / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest.get("snapshots") or []:
        if str(entry.get("price_date")) != str(price_date):
            continue
        path = base / str(entry.get("file"))
        if not path.exists():
            return None
        return {
            "kind": "live_quotes",
            "snapshot_id": path.stem,
            "price_date": str(price_date),
            **_file_ref(path),
        }
    return None


def batch_reference(batch) -> dict[str, Any]:
    """批量财务快照（qps-...）引用；清单 id + manifest 哈希。"""
    directory = Path(batch.directory)
    manifest = directory / "manifest.json"
    return {
        "kind": "batch_snapshot",
        "snapshot_id": str(batch.manifest.get("snapshot_id") or ""),
        "path": _relative(directory),
        "manifest_sha256": file_sha256(manifest),
    }


def batch_price_reference(batch) -> dict[str, Any]:
    price_table = Path(batch.directory) / "price.csv.gz"
    return {
        "kind": "batch_price",
        "snapshot_id": str(batch.manifest.get("snapshot_id") or ""),
        "price_date": "",
        **_file_ref(price_table),
    }


def snapshot_file_for(snapshot_id: str) -> Path | None:
    for base in (ROOT / "data" / "policy-trial" / "snapshots", ROOT / "data" / "snapshots"):
        path = base / f"{snapshot_id}.json"
        if path.exists():
            return path
    return None


def snapshot_reference(snapshot_id: str, *, price_date: str | None = None) -> dict[str, Any]:
    path = snapshot_file_for(snapshot_id)
    if path is None:
        raise TraceError(f"找不到已提交的单标的快照文件：{snapshot_id}")
    return {
        "kind": "policy_snapshot",
        "snapshot_id": str(snapshot_id),
        "price_date": str(price_date or ""),
        **_file_ref(path),
    }


def bank_financial_reference() -> dict[str, Any]:
    from engine.bank.inputs import current_history, current_inputs

    inputs = current_inputs()
    history = current_history()
    ref = _file_ref(inputs)
    return {
        "kind": "bank_valuation_inputs",
        "snapshot_id": "bank-valuation-v2-inputs:" + ref["sha256"][:16],
        "path": ref["path"],
        "sha256": ref["sha256"],
        "files": [_file_ref(history)],
    }


def stock_trace(
    *,
    policy_path: Path,
    financial: dict[str, Any],
    quote: dict[str, Any] | None,
) -> dict[str, Any]:
    """组装个股的追溯块；缺行情引用时用财务快照内的价格事实。"""
    if quote is None:
        quote = {
            "kind": "snapshot_fact",
            "snapshot_id": str(financial.get("snapshot_id") or ""),
            "price_date": str(financial.get("price_date") or ""),
            "path": financial.get("path"),
            "sha256": financial.get("manifest_sha256") or financial.get("sha256"),
        }
    return {
        "schema_version": "conclusion-trace/1",
        "policy": policy_ref(policy_path),
        "financial": financial,
        "quote": quote,
    }


def bank_trace(
    *,
    policy_path: Path,
    price_date: str,
    quote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    quote_ref = quote or live_quote_reference(price_date)
    financial = bank_financial_reference()
    if quote_ref is None:
        quote_ref = {
            "kind": "bank_inputs_price",
            "snapshot_id": financial["snapshot_id"],
            "path": financial["path"],
            "sha256": financial["sha256"],
            "price_date": str(price_date or ""),
        }
    return {
        "schema_version": "conclusion-trace/1",
        "policy": policy_ref(policy_path),
        "financial": financial,
        "quote": quote_ref,
    }


def quote_price(path: Path, code: str) -> tuple[float | None, str | None]:
    """从已提交行情文件取某代码的价格与价格日期。"""
    import gzip

    path = Path(path)
    if not path.exists():
        return None, None
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            import csv

            for row in csv.DictReader(handle):
                if str(row.get("code") or row.get("SECURITY_CODE") or "").zfill(6) == code:
                    try:
                        return float(row.get("price") or row.get("PRICE")), str(
                            row.get("price_date") or ""
                        )[:10] or None
                    except (TypeError, ValueError):
                        return None, None
        return None, None
    payload = json.loads(path.read_text(encoding="utf-8"))
    for row in payload.get("rows") or []:
        if str(row.get("code")).zfill(6) == code:
            return float(row["price"]), str(row.get("price_date"))[:10]
    return None, None


__all__ = [
    "LIVE_DIR",
    "TraceError",
    "bank_financial_reference",
    "bank_trace",
    "batch_price_reference",
    "batch_reference",
    "file_sha256",
    "live_quote_reference",
    "policy_ref",
    "quote_price",
    "snapshot_file_for",
    "snapshot_reference",
    "stock_trace",
]
