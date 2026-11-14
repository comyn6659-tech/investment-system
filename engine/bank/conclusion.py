"""银行 v2.1 正式结论写入（复用个股只追加账本规则，不改旧记录）。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from engine.bank.valuation_v2 import BankValuationInputs, load_policy, value_bank
from engine.policy.conclusion import append_evaluation
from engine.policy.conclusion_ledger import ConclusionLedger
from engine.policy.equity import load_policy as load_equity_policy
from engine.policy.valuation import load_macro_series
from engine.quality.tables import read_table

ROOT = Path(__file__).resolve().parent.parent.parent
POLICY = ROOT / "data" / "policies" / "bank-valuation-v2.1.json"
INPUTS = ROOT / "data" / "bank-pool" / "valuation-v2" / "inputs.csv.gz"
BANK_REPORT = ROOT / "data" / "bank-pool" / "report.json"
EQUITY_POLICY = ROOT / "data" / "policies" / "equity-policy-v1.1.json"
BINDING_TEXT = {
    "margin_of_safety": "安全边际（基准价值 × (1 − M)）",
    "pessimistic_value": "悲观价值",
}


class BankPolicyRef:
    """append_evaluation 只需要政策 id 与 digest；正式结论参数在 v2.1 文件里。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        self.policy_id = self.data["policy_id"]
        self.policy_digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.status = self.data["status"]


def _rows() -> dict[str, dict[str, Any]]:
    return {str(row["code"]).zfill(6): row for row in read_table(INPUTS)}


def bank_pool_codes() -> set[str]:
    report = json.loads(BANK_REPORT.read_text(encoding="utf-8"))
    return {str(row["code"]).zfill(6) for row in report["in_pool"]}


def valuation_for(
    code: str,
    *,
    price: float | None = None,
    price_date: str | None = None,
):
    policy = load_policy(POLICY)
    equity_policy = load_equity_policy(EQUITY_POLICY)
    series = Path(equity_policy.node("discount_rate.risk_free")["series_file"])
    if not series.is_absolute():
        series = ROOT / series
    macro = load_macro_series(series)
    row = _rows().get(str(code).zfill(6))
    if row is None:
        return None
    item = BankValuationInputs.from_row(row, in_bank_pool=str(code).zfill(6) in bank_pool_codes())
    if price is not None:
        item = replace(item, price=float(price), price_date=str(price_date or item.price_date or ""))
    return value_bank(item, policy, equity_policy, macro)


def ledger_record(valuation: dict[str, Any], *, analysis_date: str) -> dict[str, Any]:
    code = str(valuation["code"]).zfill(6)
    return {
        "instrument_id": f"EQUITY:{code}.{'SH' if code.startswith('6') else 'SZ'}",
        "code": code,
        "analysis_date": analysis_date,
        "price": valuation["price"],
        "price_as_of": valuation["price_date"],
        "values": valuation["values"],
        "grade": {"grade": "B", "margin_of_safety": valuation["margin_of_safety"]},
        "status": "complete",
        "snapshot_id": f"bank-valuation-v2.1:{hashlib.sha256(POLICY.read_bytes()).hexdigest()[:12]}",
        "trial_conclusion": {
            "conclusion": valuation["conclusion"],
            "thresholds": {
                "binding_condition_text": BINDING_TEXT.get(valuation.get("binding_condition"), ""),
                "binding_price": valuation.get("threshold_price"),
            },
        },
        "reason_codes": [],
    }


def append_bank_formal(
    valuation: dict[str, Any], *, ledger_path: Path, analysis_date: str
) -> dict[str, Any]:
    record = ledger_record(valuation, analysis_date=analysis_date)
    return append_evaluation(
        ConclusionLedger(ledger_path),
        record,
        policy=BankPolicyRef(POLICY),
        analysis_date=analysis_date,
    )


__all__ = [
    "BINDING_TEXT",
    "BankPolicyRef",
    "append_bank_formal",
    "bank_pool_codes",
    "ledger_record",
    "valuation_for",
]
