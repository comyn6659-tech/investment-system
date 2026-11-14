"""第十一批收尾 T1：运行时只读已提交的固定快照，不依赖 data/cache 或被忽略路径。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CACHE_MARKERS = (
    "data/cache",
    'data" / "cache',
    '"cache" /',
    "CACHE_DIR",
    "CACHE =",
)
RUNTIME_GLOBS = ("engine/**/*.py", "tools/run_*.py", "tools/update.py", "tests/*.py")
ALLOWED_PREFIXES = ("tools/build_", "tools/probe_", "tools/compress_")


def _runtime_files():
    for pattern in RUNTIME_GLOBS:
        for path in ROOT.glob(pattern):
            if path.name.startswith("__"):
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative.startswith(ALLOWED_PREFIXES) or path.name == "test_clean_inputs.py":
                continue
            yield path


def test_runtime_code_has_no_cache_directory_reference():
    problems = []
    for path in _runtime_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in CACHE_MARKERS:
            if marker in text:
                problems.append(f"{path.relative_to(ROOT)} 引用了 {marker!r}")
    assert not problems, "\n".join(problems)


def test_runtime_code_has_no_ignored_state_reference():
    problems = []
    for path in _runtime_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in ("update-state.json", ".last_series_attempt.json"):
            if marker in text:
                problems.append(f"{path.relative_to(ROOT)} 引用了 {marker!r}")
    assert not problems, "\n".join(problems)


def test_ownership_comes_from_committed_snapshot():
    from engine.ask.service import OWNERSHIP as ASK_OWNERSHIP
    from tools.run_pool_valuation import OWNERSHIP as POOL_OWNERSHIP

    for path in (ASK_OWNERSHIP, POOL_OWNERSHIP):
        assert path.exists(), path
        assert "data/income-pool/snapshot-" in path.as_posix()
        assert "data/cache" not in path.as_posix()
        if (ROOT / ".git").exists():
            ignored = subprocess.run(
                ["git", "check-ignore", "-q", str(path)], cwd=ROOT
            )
            assert ignored.returncode == 1, f"{path} 被 git 忽略"


def test_required_runtime_data_files_are_committed():
    required = [
        ROOT / "data" / "policies" / "equity-policy-v1.1.json",
        ROOT / "data" / "policies" / "cyclical-industries-v1.json",
        ROOT / "data" / "income-pool" / "snapshot-qps-d8fb7e3718675b93" / "ownership.csv.gz",
        ROOT / "data" / "ledger" / "equity-conclusions.jsonl",
        ROOT / "data" / "ledger" / "candidate-a-signals.jsonl",
        ROOT / "data" / "ledger" / "official-valuation-daily.jsonl",
        ROOT / "data" / "ledger" / "hstech-monthly-valuation.jsonl",
        ROOT / "data" / "ledger" / "update-log.jsonl",
        ROOT / "data" / "pool-valuation" / "live" / "manifest.json",
        ROOT / "data" / "ask" / "questions.json",
        ROOT / "data" / "ask" / "acceptance.json",
    ]
    for path in required:
        assert path.exists(), path
        if (ROOT / ".git").exists():
            ignored = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=ROOT)
            assert ignored.returncode == 1, f"{path} 被 git 忽略"

    # C1：账本引用的持久化快照必须存在且内容哈希通过（与 scripts/verify-ledger-snapshots.py 同一实现）。
    import importlib.util

    script = ROOT / "scripts" / "verify-ledger-snapshots.py"
    spec = importlib.util.spec_from_file_location("verify_ledger_snapshots_script", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.verify()
    assert result["problems"] == [], "\n".join(result["problems"])
    assert result["qps"] >= 5, result
    assert result["snap"] >= 7, result


def test_clean_archive_script_is_documented():
    script = ROOT / "scripts" / "verify-clean-archive.sh"
    assert script.exists()
    assert script.stat().st_mode & 0o111
    text = script.read_text(encoding="utf-8")
    assert "git archive" in text
    assert "pytest" in text
    assert "run_ask_acceptance" in text
    assert "git archive" in (ROOT / "DELIVERY.md").read_text(encoding="utf-8")
