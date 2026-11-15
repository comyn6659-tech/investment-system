"""第五阶段收尾 D1/D2：测试账本隔离与日期注入的显式守护测试。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from engine import clock, ledger_paths
from engine.ask.service import AskService

ROOT = Path(__file__).resolve().parent.parent
EQUITY_LEDGER = ROOT / "data" / "ledger" / "equity-conclusions.jsonl"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ledger_write_dir_comes_from_test_environment():
    directory = Path(os.environ[ledger_paths.LEDGER_DIR_ENV])
    assert directory.is_dir()
    assert not str(directory.resolve()).startswith(str(ROOT.resolve()))
    assert ledger_paths.ledger_dir() == directory
    assert ledger_paths.write_path("equity-conclusions.jsonl") == directory / "equity-conclusions.jsonl"
    # 读取仍指向仓库内已提交账本，历史查询行为不变
    assert ledger_paths.read_path("equity-conclusions.jsonl") == EQUITY_LEDGER
    service = AskService(allow_update=False, write_ledger=False)
    assert service.ledger_path == EQUITY_LEDGER
    assert service.write_ledger_path == directory / "equity-conclusions.jsonl"


def test_ledger_guard_helper_detects_change(tmp_path):
    target = tmp_path / "ledger"
    target.mkdir()
    (target / "a.jsonl").write_text("{}\n", encoding="utf-8")
    before = ledger_paths.ledger_hashes(target)
    after_same = ledger_paths.ledger_hashes(target)
    assert before == after_same
    (target / "a.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    after_changed = ledger_paths.ledger_hashes(target)
    assert after_changed != before


def test_clock_env_injection_changes_default_analysis_date(monkeypatch):
    monkeypatch.setenv(clock.TODAY_ENV, "2027-06-01")
    assert clock.today() == date(2027, 6, 1)
    assert clock.now().hour == 12
    service = AskService(allow_update=False, write_ledger=False)
    assert service.analysis_date == date(2027, 6, 1)


def test_cli_writes_to_temp_ledger_not_repo(monkeypatch):
    before = _sha(EQUITY_LEDGER)
    directory = Path(os.environ[ledger_paths.LEDGER_DIR_ENV])
    ledger = directory / "equity-conclusions.jsonl"
    original = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
    result = subprocess.run(
        [sys.executable, "-m", "engine.cli", "--json", "600036"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        env=dict(os.environ),
    )
    assert result.returncode == 0, result.stderr[-800:]
    assert _sha(EQUITY_LEDGER) == before, "CLI 不得写真实账本"
    text = ledger.read_text(encoding="utf-8")
    assert len(text.splitlines()) >= len(original.splitlines())
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    assert any(row.get("code") == "600036" for row in rows)
    assert all(row.get("analysis_date") == os.environ[clock.TODAY_ENV] for row in rows if row.get("code") == "600036")


def test_ask_service_default_write_goes_to_temp_ledger():
    before = _sha(EQUITY_LEDGER)
    directory = Path(os.environ[ledger_paths.LEDGER_DIR_ENV])
    ledger = directory / "equity-conclusions.jsonl"
    service = AskService(
        allow_update=False,
        write_ledger=True,
        use_live_quotes=False,
        analysis_date=date(2026, 11, 14),
    )
    result = service.ask("600519 怎么样")
    assert _sha(EQUITY_LEDGER) == before
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(row.get("code") == "600519" for row in rows)
