#!/usr/bin/env python
"""池成员红线监测：周度扫描、状态与账本检查。

用法：
    .venv/bin/python tools/run_monitoring.py scan            # 立即扫描一次（联网）
    .venv/bin/python tools/run_monitoring.py scan --dry-run  # 只算不写
    .venv/bin/python tools/run_monitoring.py status          # 最近扫描/出池/变动
    .venv/bin/python tools/run_monitoring.py check           # 账本结构检查
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.monitoring.changes import changes_data  # noqa: E402
from engine.monitoring.ledger import MonitoringStore  # noqa: E402
from engine.monitoring.policy import load_policy  # noqa: E402
from engine.monitoring.pools import default_store  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(TZ)


def status_payload(store: MonitoringStore) -> dict:
    last = store.last_scan() or {}
    exclusions = store.all_exclusions()
    changes = changes_data(store)
    return {
        "policy_id": load_policy().policy_id,
        "last_scan": {
            "scan_date": last.get("scan_date"),
            "week": last.get("week"),
            "coverage": last.get("coverage"),
            "counts": last.get("counts"),
            "live_checked": last.get("live_checked"),
            "pending": last.get("pending")
            if last.get("pending") is not None
            else sum(
                1
                for member in last.get("members") or []
                if any(event.get("pending") for event in (member.get("evidence") or {}).get("penalty", {}).get("events") or [])
            ),
            "schema_version": last.get("schema_version"),
        },
        "exclusions": [
            {
                "code": item.get("code"),
                "name": item.get("name"),
                "status": item.get("status"),
                "as_of": item.get("as_of"),
                "pools": item.get("pools"),
            }
            for item in sorted(exclusions.values(), key=lambda row: str(row.get("code")))
        ],
        "changes": changes,
    }


def check(store: MonitoringStore) -> int:
    problems = []
    for row in store.scans():
        if not row.get("scan_date") or not row.get("counts"):
            problems.append(f"扫描记录缺字段：{row.get('record_id')}")
        if row.get("schema_version") == "monitoring-scan/2":
            for member in row.get("members") or []:
                evidence = member.get("evidence") or {}
                if not all(key in evidence for key in ("pledge", "audit", "penalty")):
                    problems.append(f"扫描记录缺证据：{member.get('code')}（{row.get('record_id')}）")
                    break
    for pool in ("quality", "income", "bank"):
        membership = store.membership(pool)
        if membership is None:
            problems.append(f"{pool}-pool.jsonl 没有名单记录")
            continue
        codes = {str(code).zfill(6) for code in membership.get("pool_codes") or []}
        for row in store.exclusion_records(pool):
            code = str(row.get("code") or "").zfill(6)
            if code not in codes:
                problems.append(f"{pool} 出池记录不在名单内：{code}")
            if not row.get("status") or not row.get("red_lines"):
                problems.append(f"{pool} 出池记录缺状态或红线：{code}")
    print(
        json.dumps(
            {
                "scans": len(store.scans()),
                "exclusions": len(store.all_exclusions()),
                "changes": len(store.changes()),
                "problems": problems,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if problems else 0


def summary_text(payload: dict) -> str:
    counts = payload.get("counts") or {}
    lines = [
        f"红线扫描：{payload.get('status')}（{payload.get('as_of')}，第 {payload.get('week')} 周）",
        f"- 覆盖 {counts.get('covered')}/{counts.get('members')}，"
        f"命中 {counts.get('hit')}，未完成 {counts.get('incomplete')}，"
        f"待定 {payload.get('pending', 0)}，质押明细已查 {payload.get('live_checked', 0)}，"
        f"通过 {counts.get('ok')}",
    ]
    for item in payload.get("hits") or []:
        lines.append(f"- 命中：{item['code']} {item['name']}：{item['status_text']}")
    for item in payload.get("incomplete") or []:
        lines.append(f"- 未完成：{item['code']} {item['name']}：{'；'.join(item.get('missing_notes') or [])}")
    if payload.get("added_exclusions"):
        lines.append(f"- 新增出池：{len(payload['added_exclusions'])} 家")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="scan", choices=("scan", "status", "check"))
    parser.add_argument("--dry-run", action="store_true", help="只算不写")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    parser.add_argument("--now", default="", help="模拟时间（ISO 8601，测试用）")
    args = parser.parse_args(argv)
    store = default_store()
    if args.command == "status":
        payload = status_payload(store)
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else summary_text({
            "status": "status",
            "as_of": (payload["last_scan"] or {}).get("scan_date"),
            "week": (payload["last_scan"] or {}).get("week"),
            "counts": (payload["last_scan"] or {}).get("counts"),
            "hits": [],
            "incomplete": [],
        }))
        if not args.json:
            print(f"- 已出池 {len(payload['exclusions'])} 家；最近 365 天变动 {payload['changes']['count']} 条")
        return 0
    if args.command == "check":
        return check(store)
    from engine.monitoring.scanner import run_scan

    clock = datetime.fromisoformat(args.now) if args.now else _now()
    payload = run_scan(store=store, now=clock, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(summary_text(payload))
    return 1 if payload.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
