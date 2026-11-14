"""第四阶段第二批验收（P1–P7）：批准落地、模型状态、CLI 正式结论、新鲜度、账本。"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from engine.contracts import reason_codes as rc
from engine.data.facts import Fact
from engine.data.snapshot import Snapshot, SnapshotStore
from engine.models.registry import MODELS, MODELS_BY_ID, VALIDATED_MODEL_IDS
from engine.pipeline import Analyzer, AnalyzerConfig
from engine.policy.conclusion_ledger import (
    ConclusionConflict,
    ConclusionLedger,
    ConclusionLedgerError,
    ConclusionRecord,
)
from engine.policy.equity import load_policy
from engine.policy.freshness import check_freshness
from engine.version import POLICY_VERSION

ROOT = Path(__file__).resolve().parent.parent
APPROVED = ROOT / "data" / "policies" / "equity-policy-v1.1.json"
DRAFT = ROOT / "data" / "policies" / "equity-policy-v1.1-draft.json"
TRIAL = ROOT / "data" / "policy-trial" / "trial-report.json"
SNAPSHOTS = ROOT / "data" / "policy-trial" / "snapshots"
LEDGER = ROOT / "data" / "ledger" / "equity-conclusions.jsonl"

_MAP = {
    "candidate_buy_point": "candidate",
    "wait": "wait",
    "avoid": "avoid",
    "undetermined": "undetermined",
}


@pytest.fixture(scope="module")
def approved():
    return load_policy(APPROVED)


@pytest.fixture(scope="module")
def trial():
    return json.loads(TRIAL.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def policy_analyzer():
    return Analyzer(AnalyzerConfig(use_policy_path=True))


@pytest.fixture(scope="module")
def policy_results(policy_analyzer, trial):
    out = {}
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.fromisoformat("2026-11-12T12:00:00").replace(tzinfo=ZoneInfo("Asia/Singapore"))
    for record in trial["records"]:
        out[record["code"]] = policy_analyzer.analyze(record["code"], now=now)
    return out


# --- P1 ---------------------------------------------------------------------
def test_p1_approved_policy_matches_draft_parameters(approved):
    draft = load_policy(DRAFT)
    assert approved.status == "approved"
    assert approved.approved is True
    assert approved.policy_id == "equity-policy-v1.1"
    assert approved.data["parameters"] == draft.data["parameters"]
    approval = approved.data["approval"]
    assert approval["approved_by"] == "股东"
    assert approval["approved_at"] == "2026-11-12"
    assert approval["review_date"] > approval["approved_at"]
    assert approval["decision"] == "决策②"
    assert approval["basis"]
    # 草案仍保留且仍是未批准
    assert draft.status == "draft_unapproved"
    assert draft.approved is False


def test_p1_policy_version_uses_approved_identifier(approved):
    assert POLICY_VERSION == approved.policy_id == "equity-policy-v1.1"


# --- P2 ---------------------------------------------------------------------
def test_p2_only_policy_routes_are_validated():
    validated = {m.model_id for m in MODELS if m.status == "validated"}
    assert validated == set(VALIDATED_MODEL_IDS)
    assert validated == {
        "equity_dcf_fcff",
        "equity_dividend_discount",
        "bank_valuation_v2",
    }
    for model_id in (
        "bank_dividend_discount",
        "index_valuation_profile",
        "index_strategy_a_valuation_reversion",
        "etf_execution_check",
    ):
        assert MODELS_BY_ID[model_id].status != "validated"
    assert MODELS_BY_ID["bank_valuation_v2"].status == "validated"
    assert MODELS_BY_ID["bank_dividend_discount"].status == "research"


def test_p2_formal_result_requires_validated_policy_model(policy_results):
    result = policy_results["600519"]
    assert result.model_status == "validated"
    assert result.model_plan["model_status"] == "validated"
    assert result.model_results[0]["model_status"] == "validated"
    assert result.decision == "wait"


# --- P3 ---------------------------------------------------------------------
def test_p3_thirty_cli_conclusions_match_trial(policy_results, trial):
    mismatches = []
    for record in trial["records"]:
        result = policy_results[record["code"]]
        expected = _MAP[record["trial_conclusion"]["conclusion"]]
        if result.decision != expected:
            mismatches.append((record["code"], expected, result.decision))
    assert mismatches == []
    assert len(policy_results) == 30


def test_p3_cli_and_trial_share_evaluate_company(monkeypatch, policy_analyzer):
    from engine.policy import trial as trial_module

    calls = []
    original = trial_module.evaluate_company

    def spy(*args, **kwargs):
        calls.append(kwargs.get("code"))
        return original(*args, **kwargs)

    monkeypatch.setattr(trial_module, "evaluate_company", spy)
    policy_analyzer.analyze("600519")
    assert calls == ["600519"]


# --- P4 ---------------------------------------------------------------------
def test_p4_formal_output_contains_required_fields(policy_results):
    result = policy_results["600519"]
    assert result.decision == "wait"
    block = result.payload["policy_conclusion"]
    for key in (
        "policy",
        "grade",
        "values",
        "price",
        "candidate_entry_price",
        "distance_to_candidate_pct",
        "binding_condition",
        "conclusion",
        "snapshot_id",
        "disclaimer",
    ):
        assert key in block
    assert block["grade"]["grade"] in ("A", "B")
    assert block["grade"]["meaning"]
    assert set(block["values"]) >= {"pessimistic", "base", "optimistic", "usage"}
    assert block["policy"]["status"] == "approved"
    assert block["disclaimer"]


def test_p4_text_output_contains_all_fields(tmp_path, capsys):
    from engine.cli import main

    ledger = tmp_path / "ledger.jsonl"
    assert (
        main(
            [
                "600519",
                "--analysis-date",
                "2026-11-12",
                "--conclusion-ledger",
                str(ledger),
            ]
        )
        == 0
    )
    text = capsys.readouterr().out
    for keyword in (
        "等级：B",
        "A/B/C 衡量的是数据完整度与模型稳定性，不是公司质量",
        "三档价值",
        "起约束条件",
        "距候选门槛",
        "equity-policy-v1.1",
        "snap-",
        "不构成投资建议",
    ):
        assert keyword in text, keyword
    assert ledger.exists()


def test_p4_c_partial_unsupported_insufficient_are_undetermined(policy_results):
    assert policy_results["600887"].decision == "undetermined"
    assert "POLICY_GRADE_C" in policy_results["600887"].reason_codes
    assert policy_results["000858"].decision == "undetermined"
    assert "BASE_YEAR_ANOMALY" in policy_results["000858"].reason_codes
    assert policy_results["600031"].analysis_status == "unsupported"
    assert policy_results["600031"].decision == "undetermined"
    assert policy_results["600036"].analysis_status == "insufficient_data"
    assert policy_results["600036"].decision == "undetermined"


# --- P5 ---------------------------------------------------------------------
def _snapshot_copy(code: str = "600519") -> Snapshot:
    store = SnapshotStore(SNAPSHOTS)
    return Snapshot.from_dict(
        json.loads(
            (SNAPSHOTS / f"{store.latest_for(f'EQUITY:{code}.SH')}.json").read_text(
                encoding="utf-8"
            )
        )
    )


def _with_facts(snapshot: Snapshot, facts: list[Fact]) -> Snapshot:
    return Snapshot(
        instrument_id=snapshot.instrument_id,
        asset_class=snapshot.asset_class,
        as_of=snapshot.as_of,
        available_at=snapshot.available_at,
        fetched_at=snapshot.fetched_at,
        facts=facts,
        coverage=copy.deepcopy(snapshot.coverage),
        producers=list(snapshot.producers),
        notes=snapshot.notes,
    )


def test_p5_stale_price_hits(approved):
    snapshot = _snapshot_copy()
    facts = [
        replace(f, period="2026-11-04") if f.fact_id == "price.close" else f
        for f in snapshot.facts
    ]
    hits = check_freshness(
        approved, _with_facts(snapshot, facts), analysis_date=date(2026, 11, 12)
    )
    assert any(hit.code == rc.STALE_PRICE for hit in hits)


def test_p5_stale_financials_hits(approved):
    snapshot = _snapshot_copy()
    facts = [f for f in snapshot.facts if not (f.period or "").startswith("2025-")]
    hits = check_freshness(
        approved, _with_facts(snapshot, facts), analysis_date=date(2026, 11, 12)
    )
    assert any(hit.code == rc.STALE_FINANCIALS for hit in hits)


def test_p5_missing_policy_field_hits(approved):
    snapshot = _snapshot_copy()
    facts = [f for f in snapshot.facts if f.fact_id != "fin.2025-12-31.eps_basic"]
    hits = check_freshness(
        approved, _with_facts(snapshot, facts), analysis_date=date(2026, 11, 12)
    )
    assert any(hit.code == rc.MISSING_POLICY_FIELD for hit in hits)


def test_p5_fresh_snapshot_passes(approved):
    assert check_freshness(approved, _snapshot_copy(), analysis_date=date(2026, 11, 12)) == []


def test_p5_stale_price_forces_undetermined_end_to_end(tmp_path, approved):
    snapshot = _snapshot_copy()
    facts = [
        replace(f, period="2026-11-04") if f.fact_id == "price.close" else f
        for f in snapshot.facts
    ]
    store = SnapshotStore(tmp_path)
    store.save(_with_facts(snapshot, facts))
    store.rebuild_manifest()
    analyzer = Analyzer(
        AnalyzerConfig(use_policy_path=True, policy_snapshot_dir=tmp_path)
    )
    result = analyzer.analyze("600519")
    assert result.decision == "undetermined"
    assert "STALE_PRICE" in result.reason_codes
    assert result.analysis_status == "partial"
    assert result.payload["policy_conclusion"]["conclusion"] == "undetermined"


# --- P6 ---------------------------------------------------------------------
def _record(conclusion: str = "wait", *, price: float = 1402.3) -> ConclusionRecord:
    return ConclusionRecord(
        instrument_id="EQUITY:600519.SH",
        code="600519",
        analysis_date="2026-11-12",
        policy_version="equity-policy-v1.1",
        policy_digest="pol-test",
        snapshot_id="snap-test",
        price=price,
        price_date="2026-11-11",
        values={"pessimistic": 1038.2, "base": 1144.66, "optimistic": 1558.7},
        grade="B",
        margin_of_safety=0.4,
        conclusion=conclusion,
        binding_condition="安全边际",
        recorded_at="2026-11-12T12:00:00+08:00",
    )


def test_p6_ledger_append_skip_conflict_and_check(tmp_path):
    ledger = ConclusionLedger(tmp_path / "ledger.jsonl")
    first = ledger.append([_record()])
    assert len(first["added"]) == 1
    second = ledger.append([_record()])
    assert second["added"] == [] and len(second["skipped"]) == 1
    with pytest.raises(ConclusionConflict):
        ledger.append([_record(price=1.0)])
    assert ledger.check() == []


def test_p6_ledger_detects_tampering(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = ConclusionLedger(path)
    ledger.append([_record()])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["price"] = 1.0
    path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    problems = ledger.check()
    assert problems and "改写" in problems[0]


def test_p6_undetermined_cannot_be_written(tmp_path):
    ledger = ConclusionLedger(tmp_path / "ledger.jsonl")
    with pytest.raises(ConclusionLedgerError):
        ledger.append([_record("undetermined")])


def test_p6_first_write_has_seven_formal_conclusions(policy_results):
    formal = [
        code
        for code, result in policy_results.items()
        if result.decision in ("candidate", "wait", "avoid")
    ]
    assert len(formal) >= 7
    assert len(ConclusionLedger(LEDGER).load()) >= 7
    assert ConclusionLedger(LEDGER).check() == []


# --- P7 ---------------------------------------------------------------------
def test_p7_index_and_signal_ledger_unchanged():
    from engine.signals.ledger import SignalLedger

    signal = SignalLedger(ROOT / "data" / "ledger" / "candidate-a-signals.jsonl")
    assert len(signal.load()) == 3
    assert signal.check() == []
    analyzer = Analyzer(AnalyzerConfig())
    result = analyzer.analyze("沪深300")
    assert result.decision == "undetermined"
    assert result.model_status == "research"
    assert result.payload["candidate_a_signal"]["latest"]["percentile_display"] == 67.72


def test_p7_backtest_file_is_read_only():
    backtest = ROOT / "data" / "backtest" / "candidate-a.json"
    assert backtest.exists()
    data = json.loads(backtest.read_text(encoding="utf-8"))
    assert data["schema_version"] == "candidate-a-backtest/1"
    assert data["strategy"] == "candidate_a_weekly_valuation_percentile"
    assert len(data["indices"]) == 3


def test_p6_cli_check_detects_tampering(tmp_path):
    from engine.cli import main

    path = tmp_path / "ledger.jsonl"
    ConclusionLedger(path).append([_record()])
    assert main(["--check-conclusions", "--conclusion-ledger", str(path)]) == 0
    line = json.loads(path.read_text(encoding="utf-8"))
    line["price"] = 1.0
    path.write_text(json.dumps(line, ensure_ascii=False) + "\n", encoding="utf-8")
    assert main(["--check-conclusions", "--conclusion-ledger", str(path)]) == 1
