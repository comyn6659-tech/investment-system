"""批量财务快照：按报告期增量追加新财报，生成新快照并切换 `current` 指针。

旧快照目录保留（历史账本仍引用它们）；快照清单与表哈希由
`engine.quality.snapshot.build_snapshot_dir` 统一生成，口径与池快照相同。
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from engine.quality.snapshot import build_snapshot_dir
from engine.quality.tables import read_table, write_table

TZ = ZoneInfo("Asia/Singapore")
ROOT = Path(__file__).resolve().parent.parent.parent
POOL_DIR = ROOT / "data" / "pool-valuation"

BALANCE_COLUMNS = [
    "SECUCODE", "SECURITY_CODE", "SECURITY_NAME_ABBR", "NOTICE_DATE", "REPORT_DATE",
    "TOTAL_PARENT_EQUITY", "TOTAL_EQUITY", "MONETARYFUNDS", "SHORT_LOAN",
    "NONCURRENT_LIAB_1YEAR", "LONG_LOAN", "BOND_PAYABLE", "LEASE_LIAB", "TOTAL_ASSETS",
    "TOTAL_LIABILITIES", "MINORITY_EQUITY", "INVENTORY", "NOTE_ACCOUNTS_RECE",
    "ACCOUNTS_RECE", "NOTE_ACCOUNTS_PAYABLE", "ACCOUNTS_PAYABLE", "SHARE_CAPITAL",
    "LOAN_ADVANCE", "ACCEPT_DEPOSIT_INTERBANK",
]
INCOME_COLUMNS = [
    "SECUCODE", "SECURITY_CODE", "SECURITY_NAME_ABBR", "NOTICE_DATE", "REPORT_DATE",
    "TOTAL_OPERATE_INCOME", "OPERATE_INCOME", "OPERATE_PROFIT", "TOTAL_PROFIT",
    "INCOME_TAX", "NETPROFIT", "PARENT_NETPROFIT", "FE_INTEREST_EXPENSE",
    "FINANCE_EXPENSE", "BASIC_EPS",
]
CASHFLOW_COLUMNS = [
    "SECUCODE", "SECURITY_CODE", "SECURITY_NAME_ABBR", "NOTICE_DATE", "REPORT_DATE",
    "NETCASH_OPERATE", "CONSTRUCT_LONG_ASSET", "FA_IR_DEPR", "OILGAS_BIOLOGY_DEPR",
    "IA_AMORTIZE", "LPE_AMORTIZE", "USERIGHT_ASSET_AMORTIZE", "END_CASH_EQUIVALENTS",
    "END_CCE", "INVENTORY_REDUCE", "OPERATE_RECE_REDUCE", "OPERATE_PAYABLE_ADD",
    "DEFER_TAX",
]

STATEMENT_KINDS = {
    "balance": ("balance_full", BALANCE_COLUMNS, "RPT_F10_FINANCE_GBALANCE"),
    "income": ("income_full", INCOME_COLUMNS, "RPT_F10_FINANCE_GINCOME"),
    "cashflow": ("cashflow_full", CASHFLOW_COLUMNS, "RPT_F10_FINANCE_GCASHFLOW"),
}
INTERIM_KIND = ("interim_income_full", INCOME_COLUMNS, "RPT_F10_FINANCE_GINCOME")


@dataclass(frozen=True)
class SnapshotBuild:
    snapshot_id: str
    directory: Path
    previous_snapshot_id: str
    period: str
    season: str
    kind: str
    rows: dict[str, int]
    missing_companies: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "directory": str(self.directory),
            "previous_snapshot_id": self.previous_snapshot_id,
            "period": self.period,
            "season": self.season,
            "kind": self.kind,
            "rows": dict(self.rows),
            "missing_companies": self.missing_companies,
        }


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(TZ)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, prefix=path.name + "."
    ) as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def load_pointer(pool_dir: Path) -> dict[str, Any]:
    path = Path(pool_dir) / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"批量快照清单不存在：{path}")
    return _read_json(path)


def current_snapshot_dir(pool_dir: Path) -> tuple[Path, dict[str, Any]]:
    pointer = load_pointer(pool_dir)
    current = str(pointer.get("current") or "")
    if not current:
        raise ValueError("批量快照清单缺 current")
    directory = Path(pool_dir) / current
    manifest = _read_json(directory / "manifest.json")
    return directory, manifest


def _table_entries(manifest: Mapping[str, Any], *, label: str = "批量快照清单") -> list[Any]:
    """取表清单；`manifest.json` 指针里的 `tables` 是数量，不是表清单，报错要写清字段。"""
    tables = manifest.get("tables")
    if isinstance(tables, list):
        return tables
    raise TypeError(
        f"{label}的 tables 字段应为表清单（list），实际为 {type(tables).__name__}"
        f"（{tables!r}）；顶层 manifest.json 的 tables 只是数量，"
        "请读取 current 快照目录下的 manifest.json"
    )


def report_periods(manifest: Mapping[str, Any]) -> list[str]:
    """清单里所有报告期（年度与季度），升序。"""
    periods = []
    for entry in _table_entries(manifest):
        kind = str(entry.get("kind") or "")
        period = str(entry.get("report_date") or "")
        if kind in ("balance_full", "income_full", "cashflow_full", "interim_income_full") and period:
            periods.append(period)
    return sorted(set(periods))


def annual_periods(manifest: Mapping[str, Any]) -> list[str]:
    return sorted(
        {
            str(entry.get("report_date"))
            for entry in _table_entries(manifest)
            if entry.get("kind") in ("balance_full", "income_full", "cashflow_full")
            and entry.get("report_date")
        }
    )


def interim_periods(manifest: Mapping[str, Any]) -> list[str]:
    return sorted(
        {
            str(entry.get("report_date"))
            for entry in _table_entries(manifest)
            if entry.get("kind") == "interim_income_full" and entry.get("report_date")
        }
    )


def _write_statement(
    rows: Iterable[Mapping[str, Any]],
    *,
    kind: str,
    period: str,
    columns: list[str],
    interface: str,
    cache_tables: Path,
    stamp: str,
) -> Path:
    data = []
    for row in rows:
        item = {column: row.get(column) for column in columns}
        code = str(item.get("SECURITY_CODE") or "").zfill(6)
        if not code:
            continue
        item["SECURITY_CODE"] = code
        data.append(item)
    data.sort(key=lambda item: item["SECURITY_CODE"])
    path = Path(cache_tables) / f"{kind.replace('_full', '')}-{period}.csv.gz"
    write_table(
        path,
        data,
        columns,
        metadata={
            "kind": kind,
            "report_date": period,
            "source_id": "eastmoney.f10.batch_financials",
            "publisher": "东方财富",
            "interface": interface,
            "url": "https://datacenter-web.eastmoney.com/api/data/v1/get",
            "retrieved_at": stamp,
            "definition_note": f"{period} 全市场沪深 A 股合并报表；财报季批量抓取。",
        },
    )
    return path


def build_earnings_snapshot(
    *,
    season: str,
    period: str,
    kind: str,
    source: Any,
    pool_dir: Path | None = None,
    cache_tables: Path | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> SnapshotBuild:
    """抓取一个报告期的新财报，生成新快照并切换 `current`；旧快照保留。"""
    clock = _now(now)
    pool_dir = Path(pool_dir or POOL_DIR)
    temporary_cache = None
    if cache_tables is None:
        temporary_cache = tempfile.TemporaryDirectory(prefix="earnings-tables-")
        cache_tables = Path(temporary_cache.name)
    cache_tables = Path(cache_tables)
    old_dir, old_manifest = current_snapshot_dir(pool_dir)
    old_id = str(old_manifest.get("snapshot_id") or "")
    stamp = clock.isoformat(timespec="seconds")

    if kind == "annual":
        statements = ("balance", "income", "cashflow")
    else:
        statements = ("income",)
    new_paths: list[Path] = []
    row_counts: dict[str, int] = {}
    for statement in statements:
        table_kind, columns, interface = (
            STATEMENT_KINDS[statement] if kind == "annual" else INTERIM_KIND
        )
        rows = source.fetch_statement(statement, period)
        row_counts[table_kind] = len(rows)
        new_paths.append(
            _write_statement(
                rows,
                kind=table_kind,
                period=period,
                columns=columns,
                interface=interface,
                cache_tables=cache_tables,
                stamp=stamp,
            )
        )

    old_paths = [old_dir / str(entry["file"]) for entry in old_manifest.get("tables") or []]
    table_paths = old_paths + new_paths
    if dry_run:
        return SnapshotBuild(
            snapshot_id="(dry-run)",
            directory=old_dir,
            previous_snapshot_id=old_id,
            period=period,
            season=season,
            kind=kind,
            rows=row_counts,
            missing_companies=0,
        )

    build_stats = dict(old_manifest.get("build_stats") or {})
    build_stats.update(
        {
            "earnings_season": season,
            "earnings_period": period,
            "earnings_applied_at": stamp,
        }
    )
    directory, manifest = build_snapshot_dir(
        table_paths,
        parent=pool_dir,
        as_of=clock.date().isoformat(),
        window=old_manifest.get("window") or {},
        scope=old_manifest.get("scope") or {},
        sources=list(old_manifest.get("sources") or []),
        untested_conditions=list(old_manifest.get("untested_conditions") or []),
        build_stats=build_stats,
    )
    new_id = str(manifest["snapshot_id"])
    universe_codes = _universe_codes(old_dir / "universe.csv.gz")
    new_codes = _statement_codes(new_paths)
    missing = len(universe_codes - new_codes) if universe_codes else 0

    pointer = load_pointer(pool_dir)
    applied = list(pointer.get("earnings_applied") or [])
    applied = [item for item in applied if str(item.get("season")) != season]
    applied.append(
        {
            "season": season,
            "period": period,
            "kind": kind,
            "snapshot_id": new_id,
            "previous_snapshot_id": old_id,
            "applied_at": stamp,
        }
    )
    periods = sorted(set(report_periods(manifest)) | {period})
    pointer.update(
        {
            "current": directory.name,
            "snapshot_id": new_id,
            "content_hash": manifest.get("content_hash"),
            "as_of": clock.date().isoformat(),
            "tables": len(manifest.get("tables") or []),
            "total_bytes": manifest.get("total_bytes"),
            "written_at": stamp,
            "report_periods": periods,
            "earnings_applied": applied,
            "earnings_latest_period": period,
        }
    )
    _write_json(pool_dir / "manifest.json", pointer)
    return SnapshotBuild(
        snapshot_id=new_id,
        directory=directory,
        previous_snapshot_id=old_id,
        period=period,
        season=season,
        kind=kind,
        rows=row_counts,
        missing_companies=missing,
    )


def _universe_codes(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row.get("code") or "").zfill(6) for row in read_table(path)}


def _statement_codes(paths: Iterable[Path]) -> set[str]:
    codes: set[str] = set()
    for path in paths:
        for row in read_table(path):
            codes.add(str(row.get("SECURITY_CODE") or "").zfill(6))
    return codes


__all__ = [
    "BALANCE_COLUMNS",
    "CASHFLOW_COLUMNS",
    "INCOME_COLUMNS",
    "POOL_DIR",
    "SnapshotBuild",
    "annual_periods",
    "build_earnings_snapshot",
    "current_snapshot_dir",
    "interim_periods",
    "load_pointer",
    "report_periods",
]
