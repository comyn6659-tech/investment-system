"""结论变化：财报或价格更新后，重跑已有正式结论的公司；只有结论改变才追加记录。

- 正式结论 → 另一个正式结论：按既有规则追加新记录（新的分析日/快照，不改旧记录）；
- 正式结论 → 无法判断：按既有改判规则追加 `record_type=reversal`；
- 结论不变：不写任何记录（避免每日重复追加）。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from engine.bank.conclusion import valuation_for as bank_valuation_for
from engine.monitoring.ledger import MonitoringStore
from engine.monitoring.notify import NotifyResult, notify
from engine.policy.conclusion_ledger import ConclusionLedger

TZ = ZoneInfo("Asia/Singapore")
LABELS = {
    "candidate_buy_point": "候选买点",
    "wait": "等待",
    "avoid": "回避",
    "undetermined": "无法判断",
}


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(TZ)


def _label(value: str | None) -> str:
    return LABELS.get(str(value or ""), str(value or "—"))


def formal_latest(ledger_path: Path) -> dict[str, dict[str, Any]]:
    records = ConclusionLedger(ledger_path).load()
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        instrument = str(record.get("instrument_id") or "")
        if instrument:
            latest[instrument] = record
    return {
        instrument: record
        for instrument, record in latest.items()
        if record.get("record_type", "formal") == "formal"
        and record.get("conclusion") in ("candidate_buy_point", "wait", "avoid")
    }


def _stock_change(service: Any, code: str, old: str) -> dict[str, Any]:
    result = service._evaluate_stock(code, write_ledger=False)
    new = str((result.get("block") or {}).get("conclusion") or "undetermined")
    if new == old:
        return {"code": code, "changed": False, "old": old, "new": new}
    outcome = service._write_ledger(result["record"], code)
    return {
        "code": code,
        "changed": True,
        "old": old,
        "new": new,
        "outcome": outcome,
        "result": result,
    }


def _bank_change(service: Any, code: str, old: str) -> dict[str, Any]:
    target = service.target_index.match(code)
    if target is None:
        return {"code": code, "changed": False, "old": old, "new": old, "error": "找不到标的"}
    quote = (service.quote_rows.get(code) or {}) if service.use_live_quotes else {}
    valuation = bank_valuation_for(
        code, price=quote.get("price"), price_date=quote.get("price_date")
    )
    if valuation is None:
        return {"code": code, "changed": False, "old": old, "new": old, "error": "没有银行估值输入"}
    new = str(valuation.get("conclusion") or "undetermined")
    if new == old:
        return {"code": code, "changed": False, "old": old, "new": new}
    card = service._bank_card(target)
    return {
        "code": code,
        "changed": True,
        "old": old,
        "new": new,
        "outcome": (card.get("data") or {}).get("ledger"),
        "result": card,
    }


def refresh_conclusions(
    *,
    service: Any,
    store: MonitoringStore | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
    notifier: Callable[[str, str], Any] | None = None,
) -> dict[str, Any]:
    clock = _now(now)
    ledger_path = Path(service.ledger_path)
    if not ledger_path.exists():
        return {"status": "skipped", "reason": "结论账本不存在", "changed": 0}
    latest = formal_latest(ledger_path)
    if not latest:
        return {"status": "skipped", "reason": "没有正式结论", "changed": 0}
    changes: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for instrument, record in sorted(latest.items()):
        code = str(record.get("code") or "").zfill(6)
        old = str(record.get("conclusion") or "")
        try:
            if code in (service.pool_sets.get("bank") or set()):
                item = _bank_change(service, code, old)
            else:
                item = _stock_change(service, code, old)
        except Exception as exc:  # noqa: BLE001 - 单家失败不阻断
            errors.append({"code": code, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not item.get("changed"):
            continue
        item["name"] = service.batch.name_for(code)
        changes.append(item)

    entries: list[dict[str, Any]] = []
    notifications: list[NotifyResult] = []
    for item in changes:
        detail = f"{_label(item['old'])} → {_label(item['new'])}"
        outcome = item.get("outcome") or {}
        if outcome.get("status") == "reversal":
            detail = f"{_label(item['old'])} → 无法判断（改判）"
        entry = {
            "date": clock.date().isoformat(),
            "type": "结论变化",
            "code": item["code"],
            "name": str(item.get("name") or ""),
            "detail": detail,
            "evidence": {
                "old": item["old"],
                "new": item["new"],
                "outcome": outcome,
                "policy": "equity-policy-v1.1 / bank-valuation-v2.1",
            },
        }
        entries.append(entry)
        if "candidate_buy_point" in (item["old"], item["new"]) and item["old"] != item["new"]:
            call = notifier if notifier is not None else (lambda title, message: notify(title, message))
            try:
                notifications.append(call("结论变化", f"{item['code']} {item.get('name') or ''}：{detail}"))
            except Exception as exc:  # noqa: BLE001
                notifications.append(NotifyResult(status="failed", error=f"{type(exc).__name__}: {exc}"))
    added_changes: list[str] = []
    if entries and not dry_run:
        if store is None:
            from engine.monitoring.pools import default_store

            store = default_store()
        added_changes = store.append_changes(entries, now=clock)
    return {
        "status": "changed" if changes else "unchanged",
        "dry_run": dry_run,
        "checked": len(latest),
        "changed": len(changes),
        "changes": [
            {"code": item["code"], "old": item["old"], "new": item["new"]} for item in changes
        ],
        "errors": errors,
        "added_changes": added_changes,
        "notifications": [item.__dict__ for item in notifications],
    }


__all__ = ["formal_latest", "refresh_conclusions"]
