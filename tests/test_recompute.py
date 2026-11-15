"""第五阶段收尾 D3：正式结论追溯字段与 `--recompute` 复算。"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from engine.ask.service import AskService
from engine.policy.conclusion_ledger import ConclusionLedger
from engine.policy.recompute import recompute

ROOT = Path(__file__).resolve().parent.parent
REAL_LEDGER = ROOT / "data" / "ledger" / "equity-conclusions.jsonl"
RECORD_96 = "concl-469933db0d7e70bd"


def _write_new_records(tmp_path: Path) -> list[dict]:
    ledger = tmp_path / "equity-conclusions.jsonl"
    service = AskService(
        allow_update=False,
        write_ledger=True,
        ledger_path=ledger,
        use_live_quotes=True,
        analysis_date=date(2026, 11, 15),
    )
    service.ask("招商银行怎么样")
    service.ask("600519 怎么样")
    return ConclusionLedger(ledger).load()


def test_new_records_have_trace_and_recompute_consistent(tmp_path):
    rows = _write_new_records(tmp_path)
    assert {row["code"] for row in rows} >= {"600036", "600519"}
    for row in rows:
        trace = row.get("trace") or {}
        assert trace.get("schema_version") == "conclusion-trace/1"
        assert trace.get("policy", {}).get("sha256")
        financial = trace.get("financial") or {}
        assert financial.get("kind") in ("batch_snapshot", "bank_valuation_inputs")
        assert financial.get("path")
        quote = trace.get("quote") or {}
        assert quote.get("path") and quote.get("sha256")
        assert row.get("price_date")
        result = recompute(row)
        assert result["status"] == "consistent", result
        assert result["mismatched_fields"] == []
        for field in result["fields"]:
            assert field["ok"]
            if field["relative_difference"] is not None:
                assert field["relative_difference"] <= 1e-9


def test_trace_files_exist_and_are_not_git_ignored(tmp_path):
    rows = _write_new_records(tmp_path)
    paths = set()
    for row in rows:
        trace = row.get("trace") or {}
        paths.add(trace["policy"]["path"])
        paths.add(trace["financial"]["path"])
        paths.add(trace["quote"]["path"])
        for item in trace["financial"].get("files") or []:
            paths.add(item["path"])
    for relative in sorted(paths):
        path = ROOT / relative
        assert path.exists(), relative
        if (ROOT / ".git").exists() and path.is_file():
            ignored = subprocess.run(
                ["git", "check-ignore", "-q", str(relative)], cwd=ROOT
            )
            assert ignored.returncode == 1, f"{relative} 被 git 忽略"


def test_legacy_policy_snapshot_record_recomputes_with_note():
    rows = ConclusionLedger(REAL_LEDGER).load()
    row = rows[0]
    result = recompute(row)
    assert result["status"] == "consistent", result
    assert result["trace_present"] is False
    assert "单标的快照" in (result.get("note") or "")


def test_record_96_is_not_recomputable_with_reason():
    rows = ConclusionLedger(REAL_LEDGER).load()
    assert len(rows) == 110
    row = next(item for item in rows if item["record_id"] == RECORD_96)
    assert row["code"] == "600519" and row["analysis_date"] == "2026-11-13"
    result = recompute(row)
    assert result["status"] == "not_recomputable"
    assert "未记录批量财务快照 ID" in result["reason"]
    assert "无法证明" in result["reason"]


def test_legacy_batch_derived_record_recomputes():
    rows = ConclusionLedger(REAL_LEDGER).load()
    from engine.policy import trace as policy_trace

    row = next(
        item
        for item in rows
        if str(item.get("snapshot_id", "")).startswith("snap-")
        and item["record_id"] != RECORD_96
        and policy_trace.snapshot_file_for(str(item["snapshot_id"])) is None
    )
    result = recompute(row)
    assert result["status"] == "consistent", result
    assert "批量快照" in (result.get("note") or "")


def test_tampered_value_is_detected(tmp_path):
    row = _write_new_records(tmp_path)[0]
    tampered = json.loads(json.dumps(row))
    tampered["values"]["base"] = float(tampered["values"]["base"]) * 1.01
    result = recompute(tampered)
    assert result["status"] == "inconsistent"
    assert "values.base" in result["mismatched_fields"]


def test_ask_recompute_command_exit_codes(tmp_path):
    rows = _write_new_records(tmp_path)
    record_id = rows[0]["record_id"]
    ledger = tmp_path / "equity-conclusions.jsonl"
    ok = subprocess.run(
        [sys.executable, "-m", "engine.ask", "--recompute", record_id, "--ledger", str(ledger)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert ok.returncode == 0, ok.stderr[-500:]
    assert json.loads(ok.stdout)["status"] == "consistent"
    old = subprocess.run(
        [sys.executable, "-m", "engine.ask", "--recompute", RECORD_96],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert old.returncode == 2, old.stderr[-500:]
    payload = json.loads(old.stdout)
    assert payload["status"] == "not_recomputable"
    assert "无法证明" in payload["reason"]


def test_recompute_inconsistent_exit_code(tmp_path):
    row = _write_new_records(tmp_path)[0]
    from engine.policy.conclusion_ledger import ConclusionRecord

    tampered = json.loads(json.dumps(row))
    tampered["values"]["base"] = 1.0
    tampered["record_id"] = ConclusionRecord.from_dict(tampered).record_id
    ledger = tmp_path / "tampered.jsonl"
    ledger.write_text(json.dumps(tampered, ensure_ascii=False) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "engine.ask", "--recompute", tampered["record_id"], "--ledger", str(ledger)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 1, result.stderr[-500:]
    assert json.loads(result.stdout)["status"] == "inconsistent"
