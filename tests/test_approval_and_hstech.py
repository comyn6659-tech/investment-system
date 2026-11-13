"""第四阶段第九批：三套标准批准落地 + 恒生科技指数可行性（Q1–Q6）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from engine.contracts.enums import ResolutionStatus
from engine.identity.catalog import IdentityCatalog
from engine.identity.resolver import resolve
from engine.pipeline import Analyzer, AnalyzerConfig
from engine.quality.policy import load_policy
from engine.series.models import (
    FREQ_DAILY,
    FREQ_DAILY_SHORT,
    METRIC_OFFICIAL_CLOSE,
    METRIC_OFFICIAL_PE_1,
    METRIC_TOTAL_RETURN_CLOSE,
    SeriesStore,
)
from engine.series.profile import build_index_profile
from engine.signals.candidate_a import build_candidate_a_signal
from engine.signals.ledger import SignalLedger

ROOT = Path(__file__).resolve().parent.parent
POLICIES = ROOT / "data" / "policies"
SERIES_DIR = ROOT / "data" / "series"
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
FEASIBILITY = ROOT / "data" / "coverage" / "hstech-feasibility.json"
HSTECH = "INDEX:HSTECH.HKEX"
CANDIDATE_LEDGER = ROOT / "data" / "ledger" / "candidate-a-signals.jsonl"
OFFICIAL_LEDGER = ROOT / "data" / "ledger" / "official-valuation-daily.jsonl"
EQUITY_LEDGER = ROOT / "data" / "ledger" / "equity-conclusions.jsonl"

APPROVED = [
    ("quality-pool-v1.2", "quality-pool-v1.2-draft"),
    ("income-pool-v1", "income-pool-v1-draft"),
    ("bank-pool-v1", "bank-pool-v1-draft"),
]


# --- Q1 批准文件 ------------------------------------------------------------
@pytest.mark.parametrize("approved_id,draft_id", APPROVED)
def test_approved_policy_matches_draft_and_has_approval_record(approved_id, draft_id):
    approved = load_policy(POLICIES / f"{approved_id}.json")
    draft = load_policy(POLICIES / f"{draft_id}.json")
    assert approved.status == "approved"
    assert approved.approved is True
    assert approved.raw["version_date"] == "2026-11-13"
    assert approved.raw["next_review_date"] == "2027-11-13"
    approval = approved.raw["approval"]
    for key in ("approved_by", "approved_at", "decision", "basis", "known_gaps", "remedy", "next_review_date"):
        assert approval[key]
    assert approval["next_review_date"] == "2027-11-13"
    assert approval["approved_at"] == "2026-11-13"
    assert approval["known_gaps"]
    assert approved.raw["parameters"] == draft.raw["parameters"]
    assert approved.raw["groups"] == draft.raw["groups"]
    assert approved.raw["sensitivity"] == draft.raw["sensitivity"]
    assert approved.raw["scope"] == draft.raw["scope"]


# --- Q2 三池以 approved 规则重跑 ---------------------------------------------
@pytest.mark.parametrize(
    "pool,approved_id,draft_report,count,hash_prefix",
    [
        ("quality-pool", "quality-pool-v1.2", "report-v1.2-draft.json", 138, "qph-"),
        ("income-pool", "income-pool-v1", "report-v1-draft.json", 142, "iph-"),
        ("bank-pool", "bank-pool-v1", "report-v1-draft.json", 11, "bph-"),
    ],
)
def test_approved_pool_run_matches_draft_list_and_appends(pool, approved_id, draft_report, count, hash_prefix):
    current = json.loads((ROOT / "data" / pool / "report.json").read_text(encoding="utf-8"))
    draft = json.loads((ROOT / "data" / pool / draft_report).read_text(encoding="utf-8"))
    assert current["meta"]["policy_id"] == approved_id
    assert current["meta"]["approved"] is True
    assert current["counts"]["in_pool"] == count
    assert [row["code"] for row in current["in_pool"]] == [row["code"] for row in draft["in_pool"]]
    assert current["counts"] == draft["counts"]

    ledger_name = {
        "quality-pool": "quality-pool.jsonl",
        "income-pool": "income-pool.jsonl",
        "bank-pool": "bank-pool.jsonl",
    }[pool]
    rows = [
        json.loads(line)
        for line in (ROOT / "data" / "ledger" / ledger_name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows[-1]["policy_id"] == approved_id
    assert rows[-1]["pool_hash"].startswith(hash_prefix)
    assert rows[-2]["pool_hash"] == rows[-1]["pool_hash"]
    assert rows[-1]["counts"]["in_pool"] == count


def test_annual_rerun_command_orders_build_then_run_for_three_pools():
    from tools.run_annual_pools import STEPS

    assert len(STEPS) == 6
    assert [label for label, _ in STEPS] == [
        "重建好公司池快照",
        "好公司池按已批准规则重跑并追加账本",
        "重建稳定收息池快照",
        "稳定收息池按已批准规则重跑并追加账本",
        "重建银行池快照",
        "银行池按已批准规则重跑并追加账本",
    ]


# --- Q3 银行估值不启用 -------------------------------------------------------
def test_bank_valuation_not_enabled():
    config = json.loads((ROOT / "data" / "policy-inputs" / "bank-check-v1-draft.json").read_text(encoding="utf-8"))
    assert config["approved"] is False
    assert "不启用" in config["decision3"]
    run = subprocess.run(
        [sys.executable, "-m", "engine.cli", "--json", "600036"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert run.returncode == 0
    payload = json.loads(run.stdout)
    assert payload["analysis_status"] == "insufficient_data"
    assert payload.get("valuation") is None
    rows = [
        json.loads(line)
        for line in EQUITY_LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) >= 7
    assert not any(str(row.get("code")) == "600036" for row in rows)


# --- Q4 HSTECH 可行性 --------------------------------------------------------
def test_hstech_feasibility_uses_official_data_only():
    data = json.loads(FEASIBILITY.read_text(encoding="utf-8"))
    assert data["decision"] == "short_history_only"
    assert data["official_history_available"] is True
    assert data["official_pe_history_available"] is False
    assert data["official_dividend_yield_history_available"] is False
    assert data["official_pb_history_available"] is False
    valuation = data["current_official_valuation"]
    assert valuation["date"] == "2026-10-30"
    assert valuation["pe_ratio"] == pytest.approx(25.43)
    assert valuation["dividend_yield_pct"] == pytest.approx(1.23)
    assert valuation["pb"] is None
    probes = {item["probe_id"]: item for item in data["probes"]}
    assert probes["h1_hsi_daily_close"]["official"] is True
    assert probes["h1_hsi_daily_close"]["adopted"] is True
    assert probes["h2_hsi_pe_history"]["status"] == "missing"
    assert probes["h2_hsi_pe_history"]["adopted"] is False
    assert probes["h2_hsi_dy_history"]["status"] == "missing"
    assert probes["h2_hsi_pb_history"]["status"] == "missing"
    assert probes["h2_hsi_current_factsheet"]["official"] is True
    for probe in data["probes"]:
        assert probe["official"] is True
        assert "hsi.com.hk" in probe["url"]
    assert data["third_party_not_adopted"]
    assert "5 年月度估值画像" in " ".join(data["not_implemented"])
    assert (ROOT / "docs" / "phase4i-hstech-feasibility.md").exists()


def test_hstech_series_and_snapshot_are_official_only():
    store = SeriesStore(SERIES_DIR)
    close = store.find(HSTECH, METRIC_OFFICIAL_CLOSE, frequency=FREQ_DAILY)
    tr = store.find(HSTECH, METRIC_TOTAL_RETURN_CLOSE, frequency=FREQ_DAILY)
    pe = store.find(HSTECH, METRIC_OFFICIAL_PE_1, frequency=FREQ_DAILY_SHORT)
    assert close and tr and pe
    assert close.source.source_id == "hsi.official_history"
    assert tr.source.source_id == "hsi.official_history"
    assert pe.source.source_id == "hsi.factsheet"
    assert close.start_date == "2021-11-11"
    assert close.end_date == "2026-11-12"
    assert len(close.observations) == 1230
    assert pe.observations == (("2026-10-30", pytest.approx(25.43)),)

    from engine.data.snapshot import SnapshotStore

    snapshot_store = SnapshotStore(SNAPSHOT_DIR)
    latest = snapshot_store.latest_for(HSTECH)
    assert latest
    snapshot = snapshot_store.load(latest)
    assert snapshot.instrument_id == HSTECH
    assert snapshot.as_of == "2026-11-12"
    assert {fact.source.source_id for fact in snapshot.facts} == {"hsi.official_history", "hsi.factsheet"}
    assert snapshot.coverage["market"] == "HK"
    assert snapshot.coverage["currency"] == "HKD"
    assert snapshot.coverage["valuation_history"]["available"] is False
    assert snapshot.coverage["supports_5y_window"] is False


# --- Q5 共用代码与名称识别 ---------------------------------------------------
def test_hstech_name_resolution_and_other_hang_seng_out_of_scope():
    catalog = IdentityCatalog.load(ROOT / "data" / "registry" / "instruments.json")
    for text in ("恒生科技", "恒生科技指数", "HSTECH"):
        resolution = resolve(text, catalog)
        assert resolution.status == ResolutionStatus.RESOLVED.value
        assert resolution.instrument is not None
        assert resolution.instrument.instrument_id == HSTECH
    assert resolve("恒生指数", catalog).status == ResolutionStatus.OUT_OF_SCOPE.value
    assert resolve("恒生国企", catalog).status == ResolutionStatus.OUT_OF_SCOPE.value
    assert resolve("恒生科技ETF", catalog).status == ResolutionStatus.OUT_OF_SCOPE.value


def test_hstech_uses_shared_profile_and_signal(monkeypatch):
    import engine.pipeline as pipeline

    calls = {}
    original_profile = pipeline.build_index_profile
    original_signal = pipeline.build_candidate_a_signal

    def profile_wrapper(*args, **kwargs):
        calls["profile"] = kwargs
        return original_profile(*args, **kwargs)

    def signal_wrapper(*args, **kwargs):
        calls["signal"] = (args, kwargs)
        return original_signal(*args, **kwargs)

    monkeypatch.setattr(pipeline, "build_index_profile", profile_wrapper)
    monkeypatch.setattr(pipeline, "build_candidate_a_signal", signal_wrapper)
    result = Analyzer(AnalyzerConfig()).analyze("恒生科技")
    assert result.instrument_id == HSTECH
    assert result.analysis_status == "insufficient_data"
    assert result.decision == "undetermined"
    assert calls["profile"]["instrument_id"] == HSTECH
    assert calls["profile"]["official_only"] is True
    assert calls["signal"][1]["as_of"] == "2026-11-12"
    profile = result.payload["valuation_profile"]
    assert profile["status"] == "insufficient_data"
    assert len(profile["official_short_history"]) == 1
    signal = result.payload["candidate_a_signal"]
    assert signal["status"] == "insufficient_data"
    assert signal["signal"] == "not_evaluable"
    assert signal["series_ids"]["close"]
    assert signal["series_ids"]["pe"] is None
    assert signal["series_ids"]["total_return"]


# --- Q6 既有结果不变 ---------------------------------------------------------
def test_existing_indices_and_ledgers_unchanged():
    store = SeriesStore(SERIES_DIR)
    for instrument_id in ("INDEX:000300.SH", "INDEX:000905.SH", "INDEX:000852.SH"):
        signal = build_candidate_a_signal(store, instrument_id, as_of="2026-11-11")
        assert signal["status"] == "complete"
        assert signal["signal"] == "exit"
    ledger = SignalLedger(CANDIDATE_LEDGER)
    assert ledger.check() == []
    assert len(ledger.load()) == 3
    assert {row["instrument_id"] for row in ledger.load()} == {
        "INDEX:000300.SH",
        "INDEX:000905.SH",
        "INDEX:000852.SH",
    }
    official = [
        json.loads(line)
        for line in OFFICIAL_LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(official) == 4
    assert not any(row.get("index_code") == "HSTECH" for row in official)
    equity = [
        json.loads(line)
        for line in EQUITY_LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(equity) >= 7
