"""CLI 测试：真实子进程调用，验证退出码、JSON 契约与示例覆盖面。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from engine.contracts.validation import enforce

ROOT = Path(__file__).resolve().parent.parent


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "engine.cli", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "query,expected_status,expected_exit",
    [
        ("工商银行", "unsupported", 0),
        ("沪深300", "unsupported", 0),
        ("510300", "unsupported", 0),
        ("512880", "unsupported", 0),
        ("000001", "needs_clarification", 2),
        ("", "needs_clarification", 2),
    ],
)
def test_cli_json_contract(query, expected_status, expected_exit):
    proc = run_cli("--json", query)
    assert proc.returncode == expected_exit, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["analysis_status"] == expected_status
    assert payload["decision"] == "undetermined"
    enforce(payload)


def test_cli_text_output_has_summary_sections():
    proc = run_cli("沪深300")
    assert proc.returncode == 0
    for marker in ("【标的】", "【结论】", "【方法】", "【数据时点】"):
        assert marker in proc.stdout


def test_cli_rejects_missing_argument():
    proc = run_cli()
    assert proc.returncode == 2
    assert "缺少标的" in proc.stderr


def test_cli_snapshot_replay_is_identical():
    first = run_cli("--json", "中证500")
    assert first.returncode == 0
    snapshot_id = json.loads(first.stdout)["snapshot_id"]

    replay = run_cli("--json", "--snapshot", snapshot_id, "中证500")
    assert replay.returncode == 0
    a = json.loads(first.stdout)
    b = json.loads(replay.stdout)
    a.pop("generated_at")
    b.pop("generated_at")
    assert a == b


def test_cli_writes_out_file(tmp_path):
    out = tmp_path / "result.json"
    proc = run_cli("--json", "--out", str(out), "510300")
    assert proc.returncode == 0
    assert json.loads(out.read_text(encoding="utf-8"))["instrument_id"] == "ETF:510300.SH"


def test_cli_unknown_snapshot_is_engine_error():
    proc = run_cli("--json", "--snapshot", "snap-0000000000000000", "沪深300")
    assert proc.returncode == 3
    payload = json.loads(proc.stdout)
    assert payload["analysis_status"] == "engine_error"
    assert payload["decision"] == "undetermined"
