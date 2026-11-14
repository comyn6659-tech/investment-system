#!/usr/bin/env python
"""首次写入银行池 11 家的 v2.1 正式结论（只追加；重复运行会跳过相同记录）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.bank.conclusion import append_bank_formal, bank_pool_codes, valuation_for  # noqa: E402
from engine.quality.tables import read_table  # noqa: E402

LEDGER = ROOT / "data" / "ledger" / "equity-conclusions.jsonl"
INPUTS = ROOT / "data" / "bank-pool" / "valuation-v2" / "inputs.csv.gz"
ANALYSIS_DATE = "2026-11-14"


def main() -> int:
    rows = {str(row["code"]).zfill(6): row for row in read_table(INPUTS)}
    results = []
    for code in sorted(bank_pool_codes()):
        valuation = valuation_for(code)
        if valuation is None or valuation["conclusion"] not in (
            "candidate_buy_point",
            "wait",
            "avoid",
        ):
            raise SystemExit(f"{code} 未得到正式银行结论")
        outcome = append_bank_formal(
            valuation, ledger_path=LEDGER, analysis_date=ANALYSIS_DATE
        )
        results.append(
            {
                "code": code,
                "name": valuation["name"],
                "conclusion": valuation["conclusion"],
                "type": outcome.get("type"),
                "status": outcome.get("status"),
                "added": outcome.get("added") or [],
                "record_id": outcome.get("record_id"),
            }
        )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
