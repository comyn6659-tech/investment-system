"""当前有效池名单：年度名单快照减去红线出池记录。

`./ask`、页面与监测扫描共用这一处口径；名单文件本身不改。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from engine import ledger_paths
from engine.monitoring.ledger import MonitoringStore, POOLS

ROOT = Path(__file__).resolve().parent.parent.parent
POOL_REPORTS = {
    "quality": ROOT / "data" / "quality-pool" / "report.json",
    "income": ROOT / "data" / "income-pool" / "report.json",
    "bank": ROOT / "data" / "bank-pool" / "report.json",
}


def report_sets(paths: Mapping[str, Path] | None = None) -> dict[str, set[str]]:
    paths = paths or POOL_REPORTS
    output: dict[str, set[str]] = {}
    for pool, path in paths.items():
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        output[pool] = {str(row.get("code")).zfill(6) for row in payload.get("in_pool") or []}
    return output


def report_rows(paths: Mapping[str, Path] | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    paths = paths or POOL_REPORTS
    output: dict[str, dict[str, dict[str, Any]]] = {}
    for pool, path in paths.items():
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        output[pool] = {
            str(row.get("code")).zfill(6): row for row in payload.get("in_pool") or []
        }
    return output


def default_store() -> MonitoringStore:
    return MonitoringStore(ledger_paths.ledger_dir())


def effective_sets(
    *,
    store: MonitoringStore | None = None,
    base: Mapping[str, set[str]] | None = None,
) -> dict[str, set[str]]:
    store = store or default_store()
    return store.effective_sets(base or report_sets())


def exclusions(store: MonitoringStore | None = None) -> dict[str, dict[str, Any]]:
    store = store or default_store()
    return store.all_exclusions()


def excluded_codes(store: MonitoringStore | None = None) -> set[str]:
    return set(exclusions(store))


__all__ = [
    "POOL_REPORTS",
    "POOLS",
    "default_store",
    "effective_sets",
    "excluded_codes",
    "exclusions",
    "report_rows",
    "report_sets",
]
