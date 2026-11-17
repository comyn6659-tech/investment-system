"""batch7 hotfix H1–H4/H6：行情日期、过期提示、定时任务自检、卸载脚本、页面缓存。"""

from __future__ import annotations

import json
import plistlib
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import engine.web.app as web_app
from engine.ask.service import AskService
from engine.forward import launchd, quotes
from engine.web.app import WebApp

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Asia/Singapore")


# ---------------------------------------------------------------------------
# H1 行情日期：按行情时间戳逐行判断
# ---------------------------------------------------------------------------
def _tencent_line(
    timestamp: str,
    *,
    current: float = 100.0,
    previous: float = 99.0,
    code: str = "600519",
) -> str:
    fields = ["1", "测试公司", code, str(current), str(previous)] + ["0"] * 25
    fields.append(timestamp)
    fields += [""] * max(0, 50 - len(fields))
    prefix = "sh" if code.startswith("6") else "sz"
    return f'v_{prefix}{code}="{"~".join(fields)}"'


@pytest.mark.parametrize(
    "now,timestamp,expected_price,expected_date",
    [
        # 周二 00:12：行情日仍是周一，当前价就是周一收盘，不能取再前一天的昨收
        (datetime(2026, 11, 17, 0, 12, tzinfo=TZ), "20261116150000", 100.0, "2026-11-16"),
        # 周二 13:06：盘中，取昨收，价格日期为周一
        (datetime(2026, 11, 17, 13, 6, tzinfo=TZ), "20261117130000", 99.0, "2026-11-16"),
        # 周二 18:30：收盘后，取当日收盘
        (datetime(2026, 11, 17, 18, 30, tzinfo=TZ), "20261117150000", 100.0, "2026-11-17"),
        # 周六 10:00：行情日是周五，当前价即周五收盘
        (datetime(2026, 11, 21, 10, 0, tzinfo=TZ), "20261120150000", 100.0, "2026-11-20"),
        # 国庆后首个交易日开盘前：行情日是 9/30，当前价即 9/30 收盘
        (datetime(2026, 10, 8, 9, 0, tzinfo=TZ), "20260930150000", 100.0, "2026-09-30"),
    ],
)
def test_h1_quote_price_date_by_timestamp(now, timestamp, expected_price, expected_date):
    rows = quotes.parse_quotes(_tencent_line(timestamp), now=now)
    assert len(rows) == 1
    assert rows[0]["price"] == expected_price
    assert rows[0]["price_date"] == expected_date
    # 抓取日期与「应取到的最新收盘日」一致
    assert quotes.expected_price_date(now) == expected_date


def test_h1_expected_date_before_open_is_previous_trading_day():
    assert quotes.expected_price_date(datetime(2026, 11, 17, 0, 12, tzinfo=TZ)) == "2026-11-16"
    assert quotes.expected_price_date(datetime(2026, 11, 17, 13, 6, tzinfo=TZ)) == "2026-11-16"
    assert quotes.expected_price_date(datetime(2026, 11, 17, 18, 30, tzinfo=TZ)) == "2026-11-17"
    assert quotes.expected_price_date(datetime(2026, 11, 21, 10, 0, tzinfo=TZ)) == "2026-11-20"
    assert quotes.expected_price_date(datetime(2026, 10, 8, 9, 0, tzinfo=TZ)) == "2026-09-30"


# ---------------------------------------------------------------------------
# H2 过期提示：ask 第一行 + 页面顶部 + 数据状态
# ---------------------------------------------------------------------------
def _fake_quote_payload(price_date: str = "2026-11-16") -> dict:
    return {
        "price_date": price_date,
        "rows": [
            {
                "code": "600519",
                "name": "贵州茅台",
                "price": 1700.0,
                "price_date": price_date,
                "previous_close": 1690.0,
            }
        ],
    }


def test_h2_stale_price_is_first_line_of_ask(monkeypatch):
    monkeypatch.setenv("INVESTMENT_SYSTEM_TODAY", "2026-11-18")
    monkeypatch.setattr(quotes, "latest_snapshot", lambda: _fake_quote_payload())
    service = AskService(
        allow_update=False,
        write_ledger=False,
        analysis_date=date(2026, 11, 18),
        now=datetime(2026, 11, 18, 12, 0, tzinfo=TZ),
    )
    result = service.ask("贵州茅台怎么样")
    first = result.markdown.splitlines()[0]
    assert first == "价格停在 2026-11-16，已落后 1 个交易日，请运行 ./update 或检查定时任务。"
    assert result.markdown.splitlines()[1] == ""

    status = service.status_data()
    assert status["quote_date"] == "2026-11-16"
    assert status["stale"]["price_date"] == "2026-11-16"
    assert status["stale"]["trading_days"] == 1
    assert "价格停在 2026-11-16" in status["stale"]["message"]

    # 不带价格的回答不加过期提示
    changes = service.ask("最近有什么变动")
    assert not changes.markdown.startswith("价格停在")


def test_h2_page_top_has_stale_banner():
    html = (ROOT / "engine" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "engine" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="alert-bar"' in html
    assert "stale.message" in script
    assert "数据已更新" in script


# ---------------------------------------------------------------------------
# H3 定时任务自检
# ---------------------------------------------------------------------------
def _write_plist(path: Path, project: Path, *, working: str | None = None) -> None:
    payload = {
        "Label": launchd.LABEL,
        "ProgramArguments": [
            str(project / ".venv" / "bin" / "python"),
            str(project / "tools" / "update.py"),
        ],
        "WorkingDirectory": working if working is not None else str(project),
        "StartCalendarInterval": {"Hour": 18, "Minute": 30},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(payload))


def test_h3_launchd_check_ok_and_missing(tmp_path):
    project = tmp_path / "project"
    (project / "tools").mkdir(parents=True)
    plist = tmp_path / "LaunchAgents" / f"{launchd.LABEL}.plist"
    _write_plist(plist, project)

    ok = launchd.check_launchd(project, plist=plist)
    assert ok["status"] == "ok"
    assert ok["problems"] == []

    missing = launchd.check_launchd(project, plist=tmp_path / "missing.plist")
    assert missing["status"] == "missing"
    assert any("不存在" in item for item in missing["problems"])
    assert any("install-daily-update.sh" in item for item in missing["problems"])


def test_h3_launchd_check_reports_old_path_with_fix(tmp_path):
    project = tmp_path / "new-project"
    (project / "tools").mkdir(parents=True)
    old = tmp_path / "old-project"
    plist = tmp_path / "LaunchAgents" / f"{launchd.LABEL}.plist"
    _write_plist(plist, project, working=str(old))

    result = launchd.check_launchd(project, plist=plist)
    assert result["status"] == "mismatch"
    assert any("WorkingDirectory" in item and str(old) in item for item in result["problems"])
    assert any("install-daily-update.sh" in item for item in result["problems"])
    assert str(project) in result["fix"]


# ---------------------------------------------------------------------------
# H4 卸载脚本：变量后紧跟中文不再被并入变量名
# ---------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "darwin", reason="launchd 只支持 macOS")
def test_h4_uninstall_reports_label_without_unbound_variable(tmp_path):
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "uninstall-daily-update.sh")],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "已卸载定时任务" in result.stdout
    assert "unbound variable" not in result.stderr


def test_h4_no_shell_variable_glued_to_chinese():
    paths = list((ROOT / "scripts").glob("*.sh")) + [ROOT / "ask", ROOT / "update", ROOT / "web"]
    problems = []
    for path in paths:
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in re.finditer(r"\$[A-Za-z_][A-Za-z0-9_]*", line):
                following = line[match.end() : match.end() + 1]
                if following and ord(following) > 127:
                    problems.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# H6 页面缓存：行情快照或结论账本变化时自动失效
# ---------------------------------------------------------------------------
class _FakeService:
    def __init__(self, ledger: Path):
        self.ledger_path = ledger
        self.calls = 0
        self.resets = 0

    def _ranking(self, intent, *, detail=False):
        self.calls += 1
        return {
            "markdown": "",
            "data": {
                "ranking": [],
                "bank_ranking": [],
                "data_date": "2026-11-17",
                "count": 0,
            },
        }

    def reset_market_caches(self):
        self.resets += 1


def test_h6_web_cache_invalidates_on_quote_and_ledger_change(tmp_path, monkeypatch):
    ledger = tmp_path / "equity-conclusions.jsonl"
    ledger.write_text("", encoding="utf-8")
    pool = tmp_path / "pool-valuation"
    pool.mkdir()
    (pool / "manifest.json").write_text('{"current": "snapshot-test"}\n', encoding="utf-8")
    state = {"snapshots": [{"price_date": "2026-11-16", "content_hash": "a"}]}
    monkeypatch.setattr(web_app.quotes, "load_manifest", lambda: state)
    monkeypatch.setattr(web_app, "POOL_VALUATION_DIR", pool)

    service = _FakeService(ledger)
    app = WebApp(service=service)

    app.overview()
    app.overview()
    assert service.calls == 1

    # 新行情快照 → 缓存失效，重新评估
    state["snapshots"].append({"price_date": "2026-11-17", "content_hash": "b"})
    app.overview()
    assert service.calls == 2
    assert service.resets == 1

    # 结论账本变化 → 缓存失效
    ledger.write_text('{"record_id": "concl-x"}\n', encoding="utf-8")
    app.overview()
    assert service.calls == 3
    assert service.resets == 2


def test_h3_install_script_prints_actual_plist_paths():
    script = (ROOT / "scripts" / "install-daily-update.sh").read_text(encoding="utf-8")
    for marker in ('echo "plist：$PLIST"', 'echo "程序：$PYTHON"', 'echo "工作目录：$PROJECT"'):
        assert marker in script
