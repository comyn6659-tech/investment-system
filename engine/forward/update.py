"""前向记录更新命令：行情快照、候选 A 周度信号、官方当日估值、恒生科技月度估值。

设计约束：
- 每一步都只追加、重复跳过；同一日期/周/月份内容不同时报冲突，不覆盖；
- 单步失败不阻断其他步骤，结果如实记录；网络失败不抛出到调用方（除非全部失败）；
- 每次运行写一条只追加的运行日志 `data/ledger/update-log.jsonl`；
- 不自动安装定时任务；安装脚本在 `scripts/`，需股东手动执行。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from engine.forward import hstech, official, quotes, series

TZ = ZoneInfo("Asia/Singapore")
ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "data" / "ledger" / "update-log.jsonl"
SCHEMA_VERSION = "forward-update/1"


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(TZ)


def _step(name: str, func: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        result = func()
        result.setdefault("status", "ok")
        return {"step": name, **result}
    except Exception as exc:  # noqa: BLE001 - 单步失败不阻断，如实记录
        return {
            "step": name,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }


def last_run() -> dict[str, Any]:
    """最近一次 update 的运行记录（只读已提交的只追加日志）。"""
    if not LOG.exists():
        return {}
    lines = [line for line in LOG.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return {}
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def should_attempt(now: datetime | None = None) -> bool:
    """当天已成功更新过就不再重复联网；失败或未更新过则尝试。"""
    clock = _now(now)
    last = last_run()
    return not (
        last.get("status") == "ok"
        and str(last.get("run_at") or "").startswith(clock.date().isoformat())
    )


def run(
    *,
    now: datetime | None = None,
    skip_network: bool = False,
    dry_run: bool = False,
    light: bool = False,
) -> dict[str, Any]:
    """执行一次前向更新。

    light=True 供「每次提问前」使用：跳过耗时的官方长序列刷新，只做本地周度信号
    追加、行情、官方当日估值与恒生科技月度估值；完整刷新由每日定时任务执行。
    """
    clock = _now(now)
    steps = []
    if not light:
        steps.append(
            _step(
                "official_series",
                lambda: series.refresh_official_series(now=clock, skip_network=skip_network),
            )
        )
    steps.extend(
        [
            _step(
                "signal_ledger",
                lambda: series.refresh_signal_ledger(now=clock, skip_network=skip_network),
            ),
            _step("quotes", lambda: quotes.refresh(now=clock, skip_network=skip_network)),
            _step(
                "official_valuations",
                lambda: official.run(now=clock, skip_network=skip_network, dry_run=dry_run),
            ),
            _step(
                "hstech_monthly",
                lambda: hstech.run(now=clock, skip_network=skip_network, dry_run=dry_run),
            ),
        ]
    )
    failed = [item for item in steps if item["status"] == "failed"]
    conflicts = [item for item in steps if item["status"] == "conflict"]
    status = "ok" if not failed and not conflicts else ("failed" if failed else "conflict")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_at": clock.isoformat(timespec="seconds"),
        "status": status,
        "dry_run": dry_run,
        "skip_network": skip_network,
        "light": light,
        "steps": steps,
    }
    if not dry_run:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    return payload


def summary_text(payload: dict[str, Any]) -> str:
    labels = {
        "official_series": "官方日频序列",
        "signal_ledger": "候选 A 周度信号",
        "quotes": "全市场行情",
        "official_valuations": "官方当日估值",
        "hstech_monthly": "恒生科技月度估值",
    }
    lines = [f"前向更新：{payload['status']}（{payload['run_at']}）"]
    for item in payload["steps"]:
        label = labels.get(item["step"], item["step"])
        detail = item.get("reason") or item.get("error") or ""
        if item["step"] == "quotes" and item.get("price_date"):
            detail = f"价格日期 {item['price_date']}，{item.get('rows')} 行"
        if item["step"] == "official_valuations":
            detail = f"新写 {len(item.get('added') or [])} 条，跳过 {len(item.get('skipped') or [])} 条"
        if item["step"] == "hstech_monthly":
            detail = f"{item.get('status')} {item.get('date') or ''}".strip()
        if item["step"] == "signal_ledger":
            detail = f"新写 {item.get('added')} 条，跳过 {item.get('skipped')} 条"
        lines.append(f"- {label}：{item['status']} {detail}".rstrip())
    return "\n".join(lines)


__all__ = ["LOG", "SCHEMA_VERSION", "last_run", "run", "should_attempt", "summary_text"]
