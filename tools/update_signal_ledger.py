#!/usr/bin/env python
"""更新候选 A 只追加信号账本。

规则（施工单 B.2）：
- 只追加已结束自然周的周度观察点；同一标的同一周不重复写入；
- 已有记录不可修改：内容哈希不一致时报冲突并退出，不覆盖；
- 首次运行从「最新一个已结束的周度观察点」开始，不回溯全部历史。

用法：
    .venv/bin/python tools/update_signal_ledger.py            # 追加新观察点
    .venv/bin/python tools/update_signal_ledger.py --dry-run  # 只显示将追加什么
    .venv/bin/python tools/update_signal_ledger.py --check    # 校验账本完整性
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.backtest.weekly import (  # noqa: E402
    build_weekly_observations,
    weekly_percentile_at,
)
from engine.series.models import (  # noqa: E402
    FREQ_DAILY,
    METRIC_OFFICIAL_CLOSE,
    METRIC_OFFICIAL_PE_ROLLING,
    METRIC_TOTAL_RETURN_CLOSE,
    SeriesStore,
)
from engine.signals.candidate_a import classify_signal  # noqa: E402
from engine.signals.ledger import LedgerConflict, LedgerRecord, SignalLedger  # noqa: E402
from engine.version import CODE_VERSION  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
LEDGER_PATH = ROOT / "data" / "ledger" / "candidate-a-signals.jsonl"
SERIES_DIR = ROOT / "data" / "series"

INDICES = ("INDEX:000300.SH", "INDEX:000905.SH", "INDEX:000852.SH")


def _display(path: Path) -> str:
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def _completed_last_index(ordered) -> int:
    """最后一个已结束自然周的下标。

    规则：最后一个自然周只有在观察点为周五时才视为已结束；否则排除该周，
    等下一周出现后再追加（保证账本写入的是「该周最后一个交易日」，而不是临时值）。
    """
    if not ordered:
        return -1
    last = date.fromisoformat(ordered[-1].date)
    if last.isoweekday() == 5:
        return len(ordered) - 1
    return len(ordered) - 2


def _observations(store: SeriesStore, instrument_id: str):
    close = store.find(instrument_id, METRIC_OFFICIAL_CLOSE, frequency=FREQ_DAILY)
    pe = store.find(instrument_id, METRIC_OFFICIAL_PE_ROLLING, frequency=FREQ_DAILY)
    tr = store.find(instrument_id, METRIC_TOTAL_RETURN_CLOSE, frequency=FREQ_DAILY)
    if close is None or pe is None:
        return None, {
            "close": close.series_id if close else None,
            "pe": pe.series_id if pe else None,
            "total_return": tr.series_id if tr else None,
        }
    observations = sorted(
        build_weekly_observations(close.observations, pe.observations),
        key=lambda obs: obs.date,
    )
    series_ids = {
        "close": close.series_id,
        "pe": pe.series_id,
        "total_return": tr.series_id if tr else None,
    }
    return observations, series_ids


def _records_for(
    store: SeriesStore, instrument_id: str, *, after_date: str | None
) -> tuple[list[LedgerRecord], dict]:
    observations, series_ids = _observations(store, instrument_id)
    if observations is None:
        return [], {
            "instrument_id": instrument_id,
            "status": "missing_series",
            "series_ids": series_ids,
        }
    last_complete = _completed_last_index(observations)
    if last_complete < 0:
        return [], {
            "instrument_id": instrument_id,
            "status": "no_completed_week",
            "series_ids": series_ids,
        }
    if after_date is None:
        indices = [last_complete]
    else:
        indices = [
            index
            for index, obs in enumerate(observations)
            if obs.date > after_date and index <= last_complete
        ]
    records: list[LedgerRecord] = []
    for index in indices:
        point = weekly_percentile_at(observations, index)
        signal = classify_signal(point.percentile, point.evaluable)
        records.append(
            LedgerRecord(
                instrument_id=instrument_id,
                week=point.week,
                date=point.date,
                pe=point.pe,
                percentile=point.percentile,
                evaluable=point.evaluable,
                signal=signal,
                series_ids=series_ids,
                engine_version=CODE_VERSION,
                recorded_at=datetime.now(TZ).isoformat(timespec="seconds"),
            )
        )
    return records, {
        "instrument_id": instrument_id,
        "status": "ok" if records else "up_to_date",
        "last_completed_observation": observations[last_complete].date,
        "series_ids": series_ids,
        "candidates": len(records),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="更新候选 A 只追加信号账本")
    parser.add_argument("--dry-run", action="store_true", help="只显示将追加的记录，不写文件")
    parser.add_argument("--check", action="store_true", help="只校验账本完整性")
    parser.add_argument("--ledger", default=None, help="覆盖账本路径（默认 data/ledger/candidate-a-signals.jsonl）")
    parser.add_argument("--series-dir", default=None, help="覆盖序列目录（默认 data/series）")
    args = parser.parse_args(argv)

    ledger_path = Path(args.ledger) if args.ledger else LEDGER_PATH
    ledger = SignalLedger(ledger_path)
    if args.check:
        problems = ledger.check()
        records = ledger.load() if not problems else []
        print(
            json.dumps(
                {
                    "ledger": _display(ledger_path),
                    "records": len(records),
                    "problems": problems,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if problems else 0

    store = SeriesStore(Path(args.series_dir) if args.series_dir else SERIES_DIR)
    summary: dict = {
        "ledger": _display(ledger_path),
        "dry_run": args.dry_run,
        "indices": [],
        "added": 0,
        "skipped": 0,
    }
    for instrument_id in INDICES:
        last_date = ledger.last_date(instrument_id)
        records, info = _records_for(store, instrument_id, after_date=last_date)
        info["last_recorded_before"] = last_date
        info["records"] = [record.to_dict() for record in records]
        if records and not args.dry_run:
            try:
                result = ledger.append(records)
                info["append"] = result
                summary["added"] += len(result["added"])
                summary["skipped"] += len(result["skipped"])
            except LedgerConflict as exc:
                info["status"] = "conflict"
                info["error"] = str(exc)
                summary["indices"].append(info)
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                return 2
        elif records:
            summary["added"] += len(records)
        summary["indices"].append(info)

    summary["ledger_records_after"] = len(ledger.load()) if not args.dry_run else None
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
