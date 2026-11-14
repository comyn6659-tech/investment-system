"""指数候选 A 研究信号与只追加账本的验收测试（施工单 B，L6）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from engine.pipeline import Analyzer, AnalyzerConfig
from engine.policy.equity import EquityPolicy, load_policy
from engine.policy.valuation import leverage_guardrail, leverage_ratio
from engine.reporting.text_summary import render_text
from engine.series.models import SeriesStore
from engine.signals.candidate_a import build_candidate_a_signal, classify_signal
from engine.signals.ledger import (
    LedgerConflict,
    LedgerError,
    LedgerRecord,
    SignalLedger,
)

ROOT = Path(__file__).resolve().parent.parent
SERIES_DIR = ROOT / "data" / "series"
LEDGER_PATH = ROOT / "data" / "ledger" / "candidate-a-signals.jsonl"
BACKTEST_JSON = ROOT / "data" / "backtest" / "candidate-a.json"


@pytest.fixture(scope="module")
def store() -> SeriesStore:
    return SeriesStore(SERIES_DIR)


@pytest.fixture(scope="module")
def policy() -> EquityPolicy:
    return load_policy(ROOT / "data" / "policies" / "equity-policy-v1-draft.json")


def _record(**overrides) -> LedgerRecord:
    base = dict(
        instrument_id="INDEX:000300.SH",
        week="2026-W45",
        date="2026-11-06",
        pe=13.13,
        percentile=67.7,
        evaluable=True,
        signal="exit",
        series_ids={"close": "ser-close", "pe": "ser-pe", "total_return": None},
        engine_version="test",
        recorded_at="2026-11-12T00:00:00+08:00",
    )
    base.update(overrides)
    return LedgerRecord(**base)


# --- 信号 -------------------------------------------------------------------
def test_classify_signal_boundaries():
    assert classify_signal(20.0, True) == "entry"
    assert classify_signal(20.01, True) == "none"
    assert classify_signal(59.99, True) == "none"
    assert classify_signal(60.0, True) == "exit"
    assert classify_signal(10.0, False) == "not_evaluable"
    assert classify_signal(None, True) == "not_evaluable"


def test_candidate_a_signal_for_csi300(store):
    signal = build_candidate_a_signal(store, "INDEX:000300.SH", as_of="2026-11-10")
    assert signal["status"] == "complete"
    assert signal["approved"] is False
    assert signal["research_signal"] is True
    assert signal["is_trading_signal"] is False
    assert signal["model_status"] == "research"
    latest = signal["latest"]
    assert latest["date"] <= "2026-11-10"
    assert latest["week"] == "2026-W45"
    assert latest["percentile"] is not None
    assert latest["percentile_display"] == pytest.approx(round(latest["percentile"], 2))
    if latest["percentile"] >= 60:
        assert signal["signal"] == "exit"
        assert signal["exit_condition_met"] is True
        assert signal["entry_condition_met"] is False
    assert signal["rule"]["entry_percentile"] == 20.0
    assert signal["rule"]["exit_percentile"] == 60.0
    assert signal["rule"]["max_hold_months"] == 12


def test_signal_backtest_summary_matches_machine_readable_record(store):
    signal = build_candidate_a_signal(store, "INDEX:000300.SH", as_of="2026-11-10")
    summary = signal["backtest"]
    data = json.loads(BACKTEST_JSON.read_text(encoding="utf-8"))
    full = data["indices"]["000300"]["runs"]["total_return"]["lag1"]["10"]["full"]
    assert summary["trades"] == full["trades"]
    assert summary["cumulative_return"] == pytest.approx(full["cumulative_return"])
    assert summary["execution_lag"] == 1
    assert summary["cost_bp"] == 10.0
    assert summary["report_path"] == "docs/archive/phase3c-backtest.md"


def test_signal_uses_only_data_up_to_as_of(store):
    early = build_candidate_a_signal(store, "INDEX:000300.SH", as_of="2020-01-10")
    assert early["latest"]["date"] <= "2020-01-10"
    very_early = build_candidate_a_signal(store, "INDEX:000300.SH", as_of="2000-01-01")
    assert very_early["status"] == "insufficient_data"
    assert very_early["signal"] == "not_evaluable"


def test_missing_series_gives_insufficient_data(store):
    signal = build_candidate_a_signal(store, "INDEX:999999.SH", as_of="2026-11-10")
    assert signal["status"] == "insufficient_data"
    assert signal["signal"] == "not_evaluable"
    assert signal["latest"] is None
    assert signal["series_ids"]["close"] is None


# --- 账本 -------------------------------------------------------------------
def test_ledger_append_is_idempotent(tmp_path):
    ledger = SignalLedger(tmp_path / "ledger.jsonl")
    record = _record()
    first = ledger.append([record])
    assert first["added"] == [record.record_id]
    assert first["skipped"] == []
    before = (tmp_path / "ledger.jsonl").read_bytes()
    second = ledger.append([_record()])
    assert second["added"] == []
    assert second["skipped"] == [record.record_id]
    assert (tmp_path / "ledger.jsonl").read_bytes() == before


def test_ledger_refuses_to_rewrite_history(tmp_path):
    ledger = SignalLedger(tmp_path / "ledger.jsonl")
    ledger.append([_record()])
    with pytest.raises(LedgerConflict):
        ledger.append([_record(percentile=99.0)])
    # 旧记录保持原样
    records = ledger.load()
    assert records[0]["percentile"] == pytest.approx(67.7)


def test_ledger_detects_tampered_record(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = SignalLedger(path)
    ledger.append([_record()])
    lines = path.read_text(encoding="utf-8").splitlines()
    data = json.loads(lines[0])
    data["percentile"] = 1.0
    path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(LedgerError):
        ledger.load()
    assert ledger.check()


def test_ledger_check_detects_duplicate_week(tmp_path):
    ledger = SignalLedger(tmp_path / "ledger.jsonl")
    record = _record()
    ledger.append([record])
    with ledger.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
    problems = ledger.check()
    assert any("重复" in problem for problem in problems)


def test_repository_ledger_is_append_only_and_consistent():
    ledger = SignalLedger(LEDGER_PATH)
    assert ledger.check() == []
    records = ledger.load()
    assert len(records) >= 3
    instruments = {record["instrument_id"] for record in records}
    assert instruments == {"INDEX:000300.SH", "INDEX:000905.SH", "INDEX:000852.SH"}
    for record in records:
        assert record["record_id"].startswith("sig-")
        assert record["rule_version"] == "candidate-a/1"
        assert record["signal"] in ("entry", "exit", "none", "not_evaluable")


def test_ledger_last_date_tracks_latest_week(tmp_path):
    ledger = SignalLedger(tmp_path / "ledger.jsonl")
    assert ledger.last_date("INDEX:000300.SH") is None
    ledger.append([_record()])
    assert ledger.last_date("INDEX:000300.SH") == "2026-11-06"


# --- 命令 -------------------------------------------------------------------
def test_ledger_update_command_is_idempotent(tmp_path):
    """真实命令连续运行两次：第二次不得重复写入。"""
    ledger_path = tmp_path / "ledger.jsonl"
    command = [
        sys.executable,
        str(ROOT / "tools" / "update_signal_ledger.py"),
        "--ledger",
        str(ledger_path),
    ]
    first = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    first_payload = json.loads(first.stdout)
    assert first_payload["added"] == 3
    before = ledger_path.read_bytes()

    second = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert second.returncode == 0, second.stderr
    second_payload = json.loads(second.stdout)
    assert second_payload["added"] == 0
    assert ledger_path.read_bytes() == before
    assert SignalLedger(ledger_path).check() == []


# --- 指数报告 ---------------------------------------------------------------
def test_index_report_contains_research_signal():
    analyzer = Analyzer(AnalyzerConfig())
    result = analyzer.analyze("沪深300", now=None)
    payload = result.to_dict()["payload"]
    signal = payload["candidate_a_signal"]
    assert signal["approved"] is False
    assert signal["research_signal"] is True
    assert payload["strategy_state"]["status"] == "research_signal_only"
    assert payload["strategy_state"]["candidate_a_signal"]["signal"] == signal["signal"]
    assert result.decision == "undetermined"
    assert result.model_status == "research"
    assert "INDEX_SIGNAL_RESEARCH_ONLY" in result.reason_codes
    assert signal["ledger"]["status"] in ("ok", "empty")
    text = render_text(result.to_dict())
    assert "候选 A 研究信号（未批准）" in text
    assert "只追加账本" in text
    assert "不构成买卖建议" in text


def test_history_coverage_separates_snapshot_path_from_series_library():
    """M1：历史覆盖不得把「快照实时抓取」说成「没有长历史」。"""
    analyzer = Analyzer(AnalyzerConfig())
    for query in ("沪深300", "中证500", "中证1000"):
        result = analyzer.analyze(query)
        coverage = result.to_dict()["payload"]["history_coverage"]
        assert "snapshot_path" in coverage and "series_library" in coverage
        assert "快照" in coverage["scope_note"] and "序列库" in coverage["scope_note"]
        snapshot_path = coverage["snapshot_path"]
        # 快照路径的字段全部改名，避免与序列库事实混淆
        assert "supports_weekly_5y_history" not in snapshot_path
        assert "snapshot_fetch_supports_weekly_5y_history" in snapshot_path
        assert "total_return_series" not in snapshot_path
        assert "snapshot_fetch_total_return_series" in snapshot_path
        library = coverage["series_library"]
        assert library["status"] == "available"
        assert library["supports_weekly_5y_history"] is True
        assert set(library["series"]) == {
            "official_close", "official_pe_rolling", "total_return_close"
        }
        for block in library["series"].values():
            assert block["series_id"].startswith("ser-")
            assert block["observations"] > 1000
            assert block["start"] < block["end"]
        text = render_text(result.to_dict())
        assert "序列库（候选 A 研究信号与回测使用）" in text
        assert "支持 5 年周度历史：是" in text
        assert "快照实时抓取路径（不是候选 A 序列库）" in text


def test_signal_percentile_display_is_two_decimals(store):
    """M2：文本按未舍入值保留两位，JSON 显示值也是两位。"""
    signal = build_candidate_a_signal(store, "INDEX:000300.SH", as_of="2026-11-10")
    raw = signal["latest"]["percentile"]
    display = signal["latest"]["percentile_display"]
    assert display == pytest.approx(round(raw, 2))
    analyzer = Analyzer(AnalyzerConfig())
    result = analyzer.analyze("沪深300")
    text = render_text(result.to_dict())
    assert f"{raw:.2f}%" in text


def test_leverage_guardrail_boundary(policy):
    """M4：刚好 50% 不触发，略高于 50% 触发。"""
    assert leverage_ratio(50.0, 50.0) == pytest.approx(0.5)
    assert leverage_guardrail(policy, 0.5)["exceeded"] is False
    assert leverage_guardrail(policy, 0.5001)["exceeded"] is True
    assert leverage_guardrail(policy, 0.333)["exceeded"] is False
    assert leverage_guardrail(policy, 0.833)["note"].startswith("研究价值仅供研究参考")


def test_index_signal_cannot_become_price_judgement():
    analyzer = Analyzer(AnalyzerConfig())
    result = analyzer.analyze("沪深300")
    assert result.decision not in ("candidate", "wait", "avoid")
    assert result.valuation_state in ("unknown", "not_applicable")
    for item in result.model_results:
        assert item.get("is_trading_signal") is False
