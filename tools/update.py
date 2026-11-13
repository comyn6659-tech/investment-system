#!/usr/bin/env python
"""`update` 命令入口：刷新前向记录（只追加、重复跳过）。

用法：
    .venv/bin/python tools/update.py                 # 执行一次
    .venv/bin/python tools/update.py --skip-network  # 只做本地可完成的部分
    .venv/bin/python tools/update.py --dry-run       # 不写账本/日志（行情仍可能联网）
    .venv/bin/python tools/update.py --check         # 校验各账本完整性
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.forward import hstech, official, quotes  # noqa: E402
from engine.forward.update import run, summary_text  # noqa: E402
from engine.signals.ledger import SignalLedger  # noqa: E402


def check() -> int:
    problems: list[str] = []
    signal = SignalLedger(ROOT / "data" / "ledger" / "candidate-a-signals.jsonl")
    problems.extend(signal.check())
    official_count, official_problems = official.check()
    problems.extend(official_problems)
    hstech_count, hstech_problems = hstech.check()
    problems.extend(hstech_problems)
    quote_manifest = quotes.load_manifest()
    print(
        json.dumps(
            {
                "candidate_a_signals": len(signal.load()),
                "official_valuation_daily": official_count,
                "hstech_monthly_valuation": hstech_count,
                "quote_snapshots": len(quote_manifest.get("snapshots") or []),
                "problems": problems,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-network", action="store_true", help="不联网，只做本地信号与账本校验")
    parser.add_argument("--dry-run", action="store_true", help="不写账本与日志")
    parser.add_argument("--check", action="store_true", help="只校验账本完整性")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args(argv)
    if args.check:
        return check()
    payload = run(skip_network=args.skip_network, dry_run=args.dry_run)
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else summary_text(payload))
    return 1 if payload["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
