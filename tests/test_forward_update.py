"""前向记录 update（batch4k 第四节）测试：只追加、重复跳过、冲突不覆盖、定时脚本。"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from engine.forward import hstech, official, quotes, update

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Asia/Singapore")


def _quote_rows(price: float = 10.0) -> list[dict]:
    return [
        {
            "code": "600519",
            "name": "贵州茅台",
            "price": price,
            "price_date": "2026-11-13",
            "previous_close": 9.5,
        },
        {
            "code": "000001",
            "name": "平安银行",
            "price": 11.0,
            "price_date": "2026-11-13",
            "previous_close": 10.9,
        },
    ]


# --- 行情快照 ---------------------------------------------------------------
def test_quote_snapshot_is_append_only_and_deduped(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "LIVE_DIR", tmp_path)
    monkeypatch.setattr(quotes, "fetch_quotes", lambda codes, now=None: _quote_rows())
    monkeypatch.setattr(quotes, "_codes", lambda: ["600519", "000001"])
    now = datetime(2026, 11, 13, 16, 30, tzinfo=TZ)

    first = quotes.refresh(now=now)
    assert first["status"] == "appended"
    files = sorted(tmp_path.glob("quotes-*.json"))
    assert [path.name for path in files] == ["quotes-2026-11-13.json"]

    second = quotes.refresh(now=now)
    assert second["status"] == "skipped"
    assert len(sorted(tmp_path.glob("quotes-*.json"))) == 1

    # 次日抓到的仍是 11-13 的不同内容 → 冲突，不覆盖
    monkeypatch.setattr(quotes, "fetch_quotes", lambda codes, now=None: _quote_rows(price=11.11))
    third = quotes.refresh(now=datetime(2026, 11, 16, 16, 30, tzinfo=TZ))
    assert third["status"] == "conflict"
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["rows"][0]["price"] == 10.0


def test_latest_quotes_reads_hash_checked_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "LIVE_DIR", tmp_path)
    monkeypatch.setattr(quotes, "fetch_quotes", lambda codes, now=None: _quote_rows())
    monkeypatch.setattr(quotes, "_codes", lambda: ["600519", "000001"])
    quotes.refresh(now=datetime(2026, 11, 13, 16, 30, tzinfo=TZ))
    rows = quotes.latest_quotes()
    assert rows["600519"]["price"] == 10.0
    assert quotes.is_fresh(now=datetime(2026, 11, 13, 17, 0, tzinfo=TZ))


# --- 官方估值 ---------------------------------------------------------------
def _official_record(date: str = "2026-11-13", pe: float = 13.0) -> dict:
    record = {
        "date": date,
        "pe": pe,
        "pe_field": "peg",
        "pe_definition": "test",
        "pb": None,
        "pb_note": "test",
        "source_id": "csindex.indexCsiDsPe",
        "source_url": "https://www.csindex.com.cn/",
        "index_code": "000300",
        "index_name": "沪深300",
        "recorded_at": "2026-11-13T18:00:00+08:00",
    }
    record["record_id"] = official.record_id(record)
    return record


def test_official_append_skips_duplicates_and_conflicts(tmp_path):
    path = tmp_path / "official.jsonl"
    first = official.append_records([_official_record()], path=path)
    assert len(first["added"]) == 1
    assert first["records_after"] == 1

    second = official.append_records([_official_record()], path=path)
    assert second["added"] == []
    assert second["skipped"] == ["2026-11-13/000300"]

    changed = _official_record(pe=99.0)
    third = official.append_records([changed], path=path)
    assert third["conflicts"]
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["pe"] == 13.0


def test_official_ledger_has_three_indices_and_no_new_chinext():
    count, problems = official.check()
    assert count >= 3
    assert problems == []
    rows = official.read_ledger()
    assert {row["index_code"] for row in rows if row["date"] >= "2026-11-12"} <= {
        "000300",
        "000905",
        "000852",
    }
    assert all(row["index_code"] != "399006" for row in rows if row["date"] >= "2026-11-13")


# --- 恒生科技月度估值 -------------------------------------------------------
def test_hstech_parse_and_append(tmp_path):
    text = "恒生科技指数 1.23 25.43\nAll data as at 30 Oct 2026\n"
    parsed = hstech.parse_factsheet_text(text)
    assert parsed["as_of"] == "2026-10-30"
    assert parsed["pe_ratio"] == 25.43
    assert parsed["dividend_yield_pct"] == 1.23

    record = {
        "schema_version": "hstech-monthly-valuation/1",
        "date": "2026-10-30",
        "frequency": "monthly",
        "index_code": "HSTECH",
        "index_name": "恒生科技指数",
        "pe": 25.43,
        "dividend_yield_pct": 1.23,
        "pb": None,
        "recorded_at": "2026-11-13T18:00:00+08:00",
    }
    record["record_id"] = hstech.record_id(record)
    path = tmp_path / "hstech.jsonl"
    assert hstech.append_record(record, path=path)["status"] == "appended"
    assert hstech.append_record(record, path=path)["status"] == "skipped"

    changed = dict(record)
    changed["pe"] = 99.0
    changed["record_id"] = hstech.record_id(changed)
    assert hstech.append_record(changed, path=path)["status"] == "conflict"


def test_hstech_ledger_has_monthly_record():
    count, problems = hstech.check()
    assert count >= 1
    assert problems == []
    rows = hstech.read_ledger()
    assert all(row["frequency"] == "monthly" for row in rows)
    assert all(row["pb"] is None for row in rows)


# --- 更新编排 ---------------------------------------------------------------
def test_update_light_skips_heavy_series_and_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(update, "LOG", tmp_path / "update-log.jsonl")
    monkeypatch.setattr(
        update.series, "refresh_official_series", lambda **kwargs: {"status": "ok", "indices": []}
    )
    monkeypatch.setattr(
        update.series, "refresh_signal_ledger", lambda **kwargs: {"status": "ok", "added": 0}
    )
    monkeypatch.setattr(
        update.quotes,
        "refresh",
        lambda **kwargs: {"status": "appended", "price_date": "2026-11-13", "rows": 10},
    )
    monkeypatch.setattr(
        update.official, "run", lambda **kwargs: {"status": "ok", "added": [], "skipped": []}
    )
    monkeypatch.setattr(update.hstech, "run", lambda **kwargs: {"status": "skipped"})

    payload = update.run(now=datetime(2026, 11, 13, 18, 30, tzinfo=TZ), light=True)
    steps = [item["step"] for item in payload["steps"]]
    assert "official_series" not in steps
    assert payload["status"] == "ok"
    assert (tmp_path / "update-log.jsonl").exists()
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["last_status"] == "ok"
    assert state["last_ok_at"].startswith("2026-11-13")


def test_update_failure_does_not_block_other_steps(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(update, "LOG", tmp_path / "update-log.jsonl")
    monkeypatch.setattr(
        update.series,
        "refresh_official_series",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("network down")),
    )
    monkeypatch.setattr(
        update.series, "refresh_signal_ledger", lambda **kwargs: {"status": "ok", "added": 0}
    )
    monkeypatch.setattr(
        update.quotes,
        "refresh",
        lambda **kwargs: {"status": "skipped", "reason": "today"},
    )
    monkeypatch.setattr(
        update.official, "run", lambda **kwargs: {"status": "ok", "added": [], "skipped": []}
    )
    monkeypatch.setattr(update.hstech, "run", lambda **kwargs: {"status": "skipped"})
    payload = update.run(now=datetime(2026, 11, 13, 18, 30, tzinfo=TZ))
    assert payload["status"] == "failed"
    assert any(item["status"] == "failed" for item in payload["steps"])
    assert any(item["step"] == "quotes" for item in payload["steps"])


def test_should_attempt_once_per_day(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "STATE", tmp_path / "state.json")
    update.write_state(
        {
            "last_status": "ok",
            "last_ok_at": "2026-11-13T18:30:00+08:00",
        }
    )
    assert update.should_attempt(datetime(2026, 11, 13, 19, 0, tzinfo=TZ)) is False
    assert update.should_attempt(datetime(2026, 11, 14, 9, 0, tzinfo=TZ)) is True


# --- 定时脚本 ---------------------------------------------------------------
def test_launchd_scripts_are_manual_only(tmp_path):
    install = ROOT / "scripts" / "install-daily-update.sh"
    uninstall = ROOT / "scripts" / "uninstall-daily-update.sh"
    assert install.exists() and uninstall.exists()
    assert install.stat().st_mode & 0o111
    assert uninstall.stat().st_mode & 0o111
    for script in (install, uninstall):
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    dry = subprocess.run(
        ["bash", str(install), "--dry-run"], capture_output=True, text=True, env=env
    )
    assert dry.returncode == 0
    assert "launchctl load -w" in dry.stdout
    assert "<key>StartCalendarInterval</key>" in dry.stdout
    assert not (tmp_path / "Library" / "LaunchAgents").exists()
    plist_text = dry.stdout.split("----- plist 内容 -----", 1)[1].split("----------------------", 1)[0].strip()
    import plistlib

    parsed = plistlib.loads(plist_text.encode("utf-8"))
    assert parsed["Label"] == "com.investment-system.forward-update"
    assert parsed["RunAtLoad"] is False
    assert parsed["StartCalendarInterval"] == {"Hour": 18, "Minute": 30}
    assert parsed["ProgramArguments"][-1].endswith("tools/update.py")

    dry_uninstall = subprocess.run(
        ["bash", str(uninstall), "--dry-run"], capture_output=True, text=True, env=env
    )
    assert dry_uninstall.returncode == 0
    assert "launchctl unload" in dry_uninstall.stdout
    assert "将删除" in dry_uninstall.stdout


def test_no_automatic_install_in_runtime():
    install_label = "install-daily-update.sh"
    for path in (ROOT / "engine").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if install_label in text:
            assert "subprocess" not in text and "os.system" not in text, path
    assert install_label in (ROOT / "SKILL.md").read_text(encoding="utf-8")
