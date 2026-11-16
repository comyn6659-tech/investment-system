"""财报季更新：5/5、9/5、11/5 之后的首次每日更新抓取新报告期并切换批量快照。

- 报告期：5/5 → 上年年报；9/5 → 当年半年报；11/5 → 当年三季报；
- 新快照生成后旧快照保留；`data/pool-valuation/manifest.json` 的 `current` 切换；
- 单个公司缺新报表时沿用旧数据，卡片由 `BatchStore.report_note()` 注明「财报未更新」。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from engine.monitoring.batch import (
    POOL_DIR,
    SnapshotBuild,
    annual_periods,
    build_earnings_snapshot,
    interim_periods,
    load_pointer,
)
from engine.monitoring.ledger import MonitoringStore

TZ = ZoneInfo("Asia/Singapore")
SCHEDULE = (
    (5, 5, "annual", "年报"),
    (9, 5, "interim", "半年报"),
    (11, 5, "q3", "三季报"),
)


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(TZ)


@dataclass(frozen=True)
class Season:
    key: str
    year: int
    kind: str
    label: str
    period: str
    anchor: date

    def to_dict(self) -> dict[str, Any]:
        return {
            "season": self.key,
            "kind": self.kind,
            "label": self.label,
            "period": self.period,
            "anchor": self.anchor.isoformat(),
        }


def _period_for(year: int, kind: str) -> str:
    if kind == "annual":
        return f"{year - 1}-12-31"
    if kind == "interim":
        return f"{year}-06-30"
    return f"{year}-09-30"


def seasons_for(year: int) -> list[Season]:
    output = []
    for month, day, kind, label in SCHEDULE:
        output.append(
            Season(
                key=f"{year}-{kind}",
                year=year,
                kind=kind,
                label=label,
                period=_period_for(year, kind),
                anchor=date(year, month, day),
            )
        )
    return output


def due_seasons(day: date, applied: Iterable[str]) -> list[Season]:
    used = set(applied)
    candidates = seasons_for(day.year - 1) + seasons_for(day.year)
    return sorted(
        [season for season in candidates if day >= season.anchor and season.key not in used],
        key=lambda season: season.anchor,
    )


def applied_seasons(manifest: dict[str, Any], *, day: date | None = None) -> set[str]:
    """已应用的财报季：清单里的报告期 + `earnings_applied` 记录。

    报告期覆盖：已有年报报告期即视为对应年报季已应用；已有不早于半年报/三季报的
    季度收入表，即视为该季已应用（后一期覆盖前一期）。
    """
    day = day or date.today()
    annual = set(annual_periods(manifest))
    interim = interim_periods(manifest)
    applied = {str(item.get("season")) for item in manifest.get("earnings_applied") or []}
    for season in seasons_for(day.year - 1) + seasons_for(day.year):
        if season.kind == "annual":
            if season.period in annual:
                applied.add(season.key)
        elif any(period >= season.period for period in interim):
            applied.add(season.key)
    return applied


def season_label(period: str) -> str:
    text = str(period or "")
    if len(text) < 10:
        return text
    year, month = text[:4], text[5:7]
    return {
        "12": f"{year} 年报",
        "06": f"{year} 半年报",
        "09": f"{year} 三季报",
        "03": f"{year} 一季报",
    }.get(month, text)


def _batch_store(directory: Path):
    from engine.pool_valuation.adapter import BatchStore

    return BatchStore(Path(directory))


def change_entry(build: SnapshotBuild, *, day: str | None = None) -> dict[str, Any]:
    missing = build.missing_companies
    detail = (
        f"{season_label(build.period)}批量快照 {build.snapshot_id}"
        f"（旧 {build.previous_snapshot_id} 保留）"
        + (f"；{missing} 家未取得新报表，沿用旧数据" if missing else "；全部公司均取得新报表")
    )
    return {
        "date": str(day or date.today().isoformat()),
        "type": "财报更新",
        "detail": detail,
        "evidence": build.to_dict(),
    }


def run_due_earnings(
    *,
    store: MonitoringStore | None = None,
    source: Any = None,
    pool_dir: Path | None = None,
    bank_dir: Path | None = None,
    refresh_bank: bool = True,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    clock = _now(now)
    pool_dir = Path(pool_dir or POOL_DIR)
    if bank_dir is None and pool_dir.resolve() != POOL_DIR.resolve():
        refresh_bank = False
    pointer_path = pool_dir / "manifest.json"
    if not pointer_path.exists():
        return {"status": "skipped", "reason": "批量快照清单不存在"}
    pointer = load_pointer(pool_dir)
    applied = applied_seasons(pointer, day=clock.date())
    due = due_seasons(clock.date(), applied)
    if not due:
        return {
            "status": "skipped",
            "reason": "当前没有到期的财报季",
            "applied": sorted(applied),
            "next": [
                season.to_dict()
                for season in seasons_for(clock.year) + seasons_for(clock.year + 1)
                if season.anchor > clock.date()
            ][:3],
        }
    if source is None:
        from engine.monitoring.earnings_source import EastmoneyEarningsSource

        source = EastmoneyEarningsSource(now=clock)
    builds: list[SnapshotBuild] = []
    bank_results: list[dict[str, Any]] = []
    for season in due:
        build = build_earnings_snapshot(
            season=season.key,
            period=season.period,
            kind=season.kind,
            source=source,
            pool_dir=pool_dir,
            now=clock,
            dry_run=dry_run,
        )
        builds.append(build)
        if refresh_bank and not dry_run:
            from engine.monitoring.bank_inputs import refresh_inputs

            try:
                bank = refresh_inputs(
                    batch=_batch_store(build.directory),
                    bank_dir=bank_dir,
                    now=clock,
                )
                bank_results.append(bank.to_dict())
            except Exception as exc:  # noqa: BLE001 - 银行输入失败不阻断个股快照，如实记录
                bank_results.append({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        if not dry_run and store is not None:
            entry = change_entry(build, day=clock.date().isoformat())
            if bank_results:
                entry["detail"] += f"；银行估值输入已切换（{bank_results[-1].get('period') or '失败'}）"
                entry["evidence"]["bank_inputs"] = bank_results[-1]
            store.append_changes([entry], now=clock)
    return {
        "status": "applied" if builds else "skipped",
        "dry_run": dry_run,
        "seasons": [season.to_dict() for season in due],
        "snapshots": [build.to_dict() for build in builds],
        "bank_inputs": bank_results,
    }


__all__ = [
    "SCHEDULE",
    "Season",
    "applied_seasons",
    "change_entry",
    "due_seasons",
    "run_due_earnings",
    "season_label",
    "seasons_for",
]
