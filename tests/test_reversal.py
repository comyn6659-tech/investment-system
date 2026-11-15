"""第十一批收尾 T2：账本改判为无法判断的追加、校验与历史问答。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from engine.policy.conclusion import append_evaluation
from engine.policy.conclusion_ledger import (
    ConclusionConflict,
    ConclusionLedger,
    ConclusionLedgerError,
    ConclusionRecord,
    latest_for_instrument,
)

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "data" / "ledger" / "equity-conclusions.jsonl"
POLICY = SimpleNamespace(policy_id="equity-policy-v1.1", policy_digest="pol-test")


def _formal(conclusion: str = "candidate_buy_point", *, date: str = "2026-11-12", snapshot: str = "snap-a"):
    return ConclusionRecord(
        instrument_id="EQUITY:601216.SH",
        code="601216",
        analysis_date=date,
        policy_version="equity-policy-v1.1",
        policy_digest="pol-test",
        snapshot_id=snapshot,
        price=4.58,
        price_date="2026-11-12",
        values={"pessimistic": 5.05, "base": 6.29, "optimistic": 7.68},
        grade="A",
        margin_of_safety=0.4,
        conclusion=conclusion,
        binding_condition="安全边际",
        recorded_at="2026-11-13T10:00:00+08:00",
    )


def _undetermined_record(*, reason_codes=None):
    return {
        "instrument_id": "EQUITY:601216.SH",
        "code": "601216",
        "status": "unsupported",
        "status_reason": "企业族 cyclical 首版未实现路线",
        "snapshot_id": "snap-b",
        "price": None,
        "price_as_of": None,
        "values": {},
        "grade": {"grade": "C"},
        "trial_conclusion": {"conclusion": "undetermined"},
        "route": {
            "family": "cyclical",
            "status": "family_not_implemented",
            "applicable_model_category": "周期位置判断 + 正常化现金流折现",
            "reasons": [{"observed": {"cyclical_list": {"group": "基础化工"}}}],
        },
        "reason_codes": list(reason_codes or []),
    }


def test_plain_undetermined_still_rejected(tmp_path):
    ledger = ConclusionLedger(tmp_path / "ledger.jsonl")
    with pytest.raises(ConclusionLedgerError):
        ledger.append([_formal("undetermined")])


def test_reversal_is_appended_once_and_targets_latest_formal(tmp_path):
    ledger = ConclusionLedger(tmp_path / "ledger.jsonl")
    ledger.append([_formal()])
    outcome = append_evaluation(
        ledger, _undetermined_record(), policy=POLICY, analysis_date="2026-11-13"
    )
    assert outcome["type"] == "reversal"
    assert outcome["status"] == "added"
    rows = ledger.load()
    assert len(rows) == 2
    old = rows[0]
    reversal = rows[1]
    assert reversal["record_type"] == "reversal"
    assert reversal["conclusion"] == "undetermined"
    assert reversal["reversal_of"] == old["record_id"]
    assert reversal["analysis_date"] == "2026-11-13"
    assert reversal["snapshot_id"] == "snap-b"
    assert reversal["policy_version"] == "equity-policy-v1.1"
    assert reversal["reason_codes"] == ["MODEL_FAMILY_NOT_IMPLEMENTED", "CYCLICAL_INDUSTRY_LIST_MATCH"]
    assert "明显周期" in reversal["reason"]
    assert ledger.check() == []

    # 最新已是改判 → 不重复写
    again = append_evaluation(
        ledger, _undetermined_record(), policy=POLICY, analysis_date="2026-11-13"
    )
    assert again["type"] == "none"
    assert len(ledger.load()) == 2


def test_no_prior_formal_means_no_write(tmp_path):
    ledger = ConclusionLedger(tmp_path / "ledger.jsonl")
    outcome = append_evaluation(
        ledger, _undetermined_record(), policy=POLICY, analysis_date="2026-11-13"
    )
    assert outcome["type"] == "none"
    assert ledger.load() == []


def test_reversal_requires_reason_and_target(tmp_path):
    ledger = ConclusionLedger(tmp_path / "ledger.jsonl")
    ledger.append([_formal()])
    bad = ConclusionRecord(
        instrument_id="EQUITY:601216.SH",
        code="601216",
        analysis_date="2026-11-13",
        policy_version="equity-policy-v1.1",
        policy_digest="pol-test",
        snapshot_id="snap-b",
        price=None,
        price_date="",
        values={},
        grade="",
        margin_of_safety=None,
        conclusion="undetermined",
        binding_condition="",
        record_type="reversal",
        reason_codes=(),
        reason="",
        reversal_of="",
    )
    with pytest.raises(ConclusionLedgerError):
        ledger.append([bad])


def test_reversal_of_missing_target_rejected(tmp_path):
    ledger = ConclusionLedger(tmp_path / "ledger.jsonl")
    bad = ConclusionRecord(
        instrument_id="EQUITY:601216.SH",
        code="601216",
        analysis_date="2026-11-13",
        policy_version="equity-policy-v1.1",
        policy_digest="pol-test",
        snapshot_id="snap-b",
        price=None,
        price_date="",
        values={},
        grade="",
        margin_of_safety=None,
        conclusion="undetermined",
        binding_condition="",
        record_type="reversal",
        reason_codes=("MODEL_FAMILY_NOT_IMPLEMENTED",),
        reason="测试原因",
        reversal_of="concl-not-exist",
    )
    with pytest.raises(ConclusionLedgerError):
        ledger.append([bad])


def test_real_ledger_has_two_reversals_for_junzheng_and_yangnong():
    ledger = ConclusionLedger(LEDGER)
    assert ledger.check() == []
    rows = ledger.load()
    for code, old_conclusion in (("601216", "candidate_buy_point"), ("600486", "avoid")):
        items = [row for row in rows if row["instrument_id"].startswith(f"EQUITY:{code}.")]
        assert len(items) == 2, code
        assert items[0]["conclusion"] == old_conclusion
        assert items[0].get("record_type", "formal") == "formal"
        reversal = items[1]
        assert reversal["record_type"] == "reversal"
        assert reversal["conclusion"] == "undetermined"
        assert reversal["reversal_of"] == items[0]["record_id"]
        assert reversal["analysis_date"] == "2026-11-13"
        assert reversal["policy_version"] == "equity-policy-v1.1"
        assert reversal["snapshot_id"]
        assert reversal["reason_codes"]
        assert "明显周期" in reversal["reason"]
    assert len(rows) >= 98


def test_history_question_shows_two_records_and_reason():
    from engine.ask.service import AskService

    service = AskService(
        allow_update=False, write_ledger=False, use_live_quotes=False,
        analysis_date=date(2026, 11, 14),
    )
    for question, code in (("君正集团以前的结论", "601216"), ("扬农化工以前的结论", "600486")):
        text = service.ask(question).markdown
        assert "账本记录共 2 条" in text
        assert "无法判断（改判）" in text
        assert "改判原因" in text
        assert "明显周期" in text
