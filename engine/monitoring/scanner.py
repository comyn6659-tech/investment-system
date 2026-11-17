"""周度红线扫描的执行：取三池成员 → 逐家取数判定 → 追加出池/扫描/变动记录 → 通知。

所有写入只追加；`dry_run` 只算不写。扫描按 ISO 周去重：同一周已有扫描记录就跳过。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from engine.monitoring import ledger as monitoring_ledger
from engine.monitoring.changes import changes_data
from engine.monitoring.ledger import MonitoringStore, iso_week
from engine.monitoring.models import Member, MemberScan, STATUS_HIT, STATUS_INCOMPLETE
from engine.monitoring.notify import NotifyResult, notify
from engine.monitoring.policy import MonitoringPolicy, load_policy
from engine.monitoring.redline import GovernanceSource, ScanReport, scan_members

TZ = ZoneInfo("Asia/Singapore")
ROOT = Path(__file__).resolve().parent.parent.parent
POOL_REPORT_PATHS = {
    "quality": ROOT / "data" / "quality-pool" / "report.json",
    "income": ROOT / "data" / "income-pool" / "report.json",
    "bank": ROOT / "data" / "bank-pool" / "report.json",
}


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(TZ)


def pool_members(*, report_paths: dict[str, Path] | None = None) -> list[Member]:
    """三池成员并集；同一公司保留其所在全部池。"""
    paths = report_paths or POOL_REPORT_PATHS
    members: dict[str, dict[str, Any]] = {}
    for pool, path in paths.items():
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        for row in report.get("in_pool") or []:
            code = str(row.get("code") or "").zfill(6)
            if not code:
                continue
            item = members.setdefault(
                code, {"code": code, "name": str(row.get("name") or code), "pools": []}
            )
            if not item["name"] or item["name"] == code:
                item["name"] = str(row.get("name") or code)
            item["pools"].append(pool)
    return [
        Member(code=item["code"], name=item["name"], pools=tuple(sorted(item["pools"])))
        for item in sorted(members.values(), key=lambda value: value["code"])
    ]


def _change_entries(report: ScanReport, *, as_of: date, policy: MonitoringPolicy) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in report.hits:
        entries.append(
            {
                "date": as_of.isoformat(),
                "type": "出池",
                "code": item.code,
                "name": item.member.name,
                "detail": item.exclusion_status(),
                "evidence": {
                    "pools": list(item.member.pools),
                    "red_lines": [hit.to_dict() for hit in item.hits],
                    "policy_id": policy.policy_id,
                },
            }
        )
    incomplete = report.incomplete
    if incomplete:
        entries.append(
            {
                "date": as_of.isoformat(),
                "type": "扫描失败",
                "detail": (
                    f"红线周度扫描：{len(incomplete)}/{report.total} 家本周未完成，"
                    "不默认通过、也不剔除，下次重试"
                ),
                "evidence": {
                    "policy_id": policy.policy_id,
                    "incomplete": [
                        {
                            "code": item.code,
                            "name": item.member.name,
                            "missing": list(item.missing),
                            "notes": item.missing_notes(),
                        }
                        for item in incomplete
                    ],
                },
            }
        )
    return entries


def run_scan(
    *,
    store: MonitoringStore | None = None,
    source: GovernanceSource | None = None,
    policy: MonitoringPolicy | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    notifier: Any = None,
    members: list[Member] | None = None,
) -> dict[str, Any]:
    clock = _now(now)
    as_of = clock.date()
    policy = policy or load_policy()
    store = store or MonitoringStore(monitoring_ledger_monitoring_dir())
    if source is None:
        from engine.monitoring.sources import EastmoneySource

        source = EastmoneySource(now=clock)
    members = list(members) if members is not None else pool_members()
    report = scan_members(members, source, policy, as_of=as_of, workers=4 if len(members) > 20 else 1)
    entries = _change_entries(report, as_of=as_of, policy=policy)

    exclusion_entries = [
        {
            "as_of": as_of.isoformat(),
            "policy_id": policy.policy_id,
            "policy_digest": policy.digest,
            "code": item.code,
            "name": item.member.name,
            "pools": list(item.member.pools),
            "status": item.exclusion_status(),
            "red_lines": [hit.to_dict() for hit in item.hits],
        }
        for item in report.hits
    ]
    added_exclusions: list[str] = []
    added_changes: list[str] = []
    if not dry_run:
        outcome = store.append_exclusions(exclusion_entries, now=clock)
        added_exclusions = outcome["added"]
        added_changes = store.append_changes(entries, now=clock)
        store.append_scan(
            {
                "schema_version": "monitoring-scan/2",
                "scan_date": as_of.isoformat(),
                "as_of": as_of.isoformat(),
                "week": iso_week(as_of),
                "policy_id": policy.policy_id,
                "policy_digest": policy.digest,
                "counts": report.counts(),
                "coverage": round(report.coverage, 4),
                "pending": len(report.pending),
                "live_checked": report.live_checked,
                "evidence_schema": "monitoring-evidence/1",
                "members": [item.to_dict() for item in report.members],
                "hits": [item.to_dict() for item in report.hits],
                "incomplete": [item.to_dict() for item in report.incomplete],
            },
            now=clock,
        )

    notifications: list[NotifyResult] = []
    if report.hits:
        call = notifier if notifier is not None else (lambda title, message: notify(title, message))
        for item in report.hits:
            try:
                result = call("池成员出池", f"{item.code} {item.member.name}：{item.exclusion_status()}")
            except Exception as exc:  # noqa: BLE001 - 通知失败不影响任务
                result = NotifyResult(status="failed", error=f"{type(exc).__name__}: {exc}")
            notifications.append(result)

    return {
        "status": "hit" if report.hits else ("incomplete" if report.incomplete else "ok"),
        "as_of": as_of.isoformat(),
        "week": iso_week(as_of),
        "counts": report.counts(),
        "coverage": round(report.coverage, 4),
        "pending": len(report.pending),
        "live_checked": report.live_checked,
        "hits": [item.to_dict() for item in report.hits],
        "incomplete": [item.to_dict() for item in report.incomplete],
        "added_exclusions": added_exclusions,
        "added_changes": added_changes,
        "notifications": [
            item.__dict__ if hasattr(item, "__dict__") else item for item in notifications
        ],
        "dry_run": dry_run,
        "policy_id": policy.policy_id,
        "store": str(store.directory),
        "changes": changes_data(store, now=clock)["count"],
    }


def monitoring_ledger_monitoring_dir() -> Path:
    """生产账本目录；测试通过 `MonitoringStore(tmp_path)` 注入，不经过这里。"""
    from engine import ledger_paths

    return ledger_paths.ledger_dir()


def run_due(
    *,
    store: MonitoringStore | None = None,
    source: GovernanceSource | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    notifier: Any = None,
) -> dict[str, Any]:
    """每周第一次每日更新时执行；同一 ISO 周已扫描则跳过。"""
    clock = _now(now)
    store = store or MonitoringStore(monitoring_ledger_monitoring_dir())
    if store.scanned_in_week(clock.date()):
        last = store.last_scan() or {}
        return {
            "status": "skipped",
            "reason": "本周已扫描",
            "week": iso_week(clock.date()),
            "last_scan": last.get("scan_date"),
            "coverage": last.get("coverage"),
        }
    return run_scan(store=store, source=source, now=clock, dry_run=dry_run, notifier=notifier)


__all__ = [
    "POOL_REPORT_PATHS",
    "monitoring_ledger_monitoring_dir",
    "pool_members",
    "run_due",
    "run_scan",
]
