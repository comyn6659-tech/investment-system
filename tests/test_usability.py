"""第第十一批第二次收尾（可用性）测试：V1–V5 / W2–W4。"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from engine.ask.plain import BANNED_TERMS, REASON_PLAIN, reason_plain
from engine.ask.service import AskService
from engine.forward import update
from engine.forward.calendar import is_trading_day

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "data" / "ask" / "questions.json"
TZ = ZoneInfo("Asia/Singapore")


@pytest.fixture(scope="module")
def service() -> AskService:
    return AskService(allow_update=False, write_ledger=False, use_live_quotes=False)


@pytest.fixture(scope="module")
def question_set() -> list[dict]:
    return json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]


def test_v1_claude_md_requires_ask_and_verbatim():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "./ask" in text
    assert "原样" in text
    assert "SKILL.md" in text
    assert "不得用自身知识" in text or "不得用你自己的知识" in text
    for word in ("好公司池", "收息池", "银行池", "指数", "ETF"):
        assert word in text


def test_v2_recommendation_questions_are_ranking(service):
    for question in ("A股有哪些值得投资的公司", "有什么好股票", "推荐几只股票"):
        result = service.ask(question)
        assert result.intent.name == "ranking", question
        assert "入池不等于买入" in result.markdown, question
        assert "候选买点" in result.markdown, question


def test_v2_byd_membership_questions_are_scorecards(service):
    for question in ("为什么比亚迪不是好公司", "比亚迪算不算好公司", "比亚迪是好公司吗"):
        result = service.ask(question)
        assert result.intent.name == "membership", question
        assert result.target.code == "002594"
        assert "不在好公司池" in result.markdown, question
        assert "成绩单" in result.markdown, question
        assert "未通过" in result.markdown, question
        assert "三档价值" not in result.markdown, question


def test_v3_default_update_budget_is_ten_seconds():
    assert AskService(allow_update=True, write_ledger=False).update_timeout == 10.0


def test_v3_calendar_weekend_and_holiday():
    assert is_trading_day(datetime(2026, 11, 14).date()) is False
    assert is_trading_day(datetime(2026, 10, 1).date()) is False
    assert is_trading_day(datetime(2026, 11, 13).date()) is True


def test_v3_non_trading_day_skips_update_and_is_fast(monkeypatch):
    def _unexpected(**kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError("非交易日不应调用 update")

    monkeypatch.setattr(update, "run", _unexpected)
    service = AskService(
        allow_update=True,
        write_ledger=False,
        use_live_quotes=False,
        now=datetime(2026, 11, 14, 10, 0, tzinfo=TZ),
    )
    started = time.monotonic()
    result = service.ask("茅台怎么样")
    elapsed = time.monotonic() - started
    assert elapsed < 15
    assert "非交易日" in result.markdown
    assert "数据截至" in result.markdown


def test_v3_already_updated_skips_network(tmp_path, monkeypatch):
    log = tmp_path / "update-log.jsonl"
    log.write_text(
        json.dumps(
            {"run_at": "2026-11-13T18:30:00+08:00", "status": "ok", "steps": []},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(update, "LOG", log)

    def _unexpected(**kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError("当天已成功更新，不应再次联网")

    monkeypatch.setattr(update, "run", _unexpected)
    service = AskService(
        allow_update=True,
        write_ledger=False,
        use_live_quotes=False,
        now=datetime(2026, 11, 13, 16, 0, tzinfo=TZ),
    )
    started = time.monotonic()
    result = service.ask("茅台怎么样")
    assert time.monotonic() - started < 15
    assert "自动更新失败" not in result.markdown
    assert re.search(r"20\d\d-\d\d-\d\d", result.markdown)


def test_v3_update_timeout_returns_existing_data(monkeypatch):
    monkeypatch.setattr(update, "should_attempt", lambda now=None: True)

    def _slow(**kwargs):
        time.sleep(1.0)
        return {"status": "ok", "run_at": "2026-11-13T16:00:00+08:00", "steps": []}

    monkeypatch.setattr(update, "run", _slow)
    service = AskService(
        allow_update=True,
        write_ledger=False,
        use_live_quotes=False,
        now=datetime(2026, 11, 13, 16, 0, tzinfo=TZ),
        update_timeout=0.2,
    )
    started = time.monotonic()
    result = service.ask("茅台怎么样")
    elapsed = time.monotonic() - started
    assert elapsed < 15
    assert "本次未能更新" in result.markdown
    assert "数据截至" in result.markdown


def test_v4_default_cards_have_no_banned_terms(service, question_set):
    problems = []
    for item in question_set:
        text = service.ask(item["question"]).markdown
        for term in BANNED_TERMS:
            if term in text:
                problems.append(f"{item['id']} 出现 {term!r}")
    assert not problems, "\n".join(problems)


def test_v4_reason_codes_all_have_plain_explanations(service, question_set):
    codes: set[str] = set()
    for item in question_set:
        result = service.ask(item["question"])
        codes.update(result.data.get("reason_codes") or [])
    # 覆盖端到端 30 只测试股
    report = json.loads((ROOT / "data" / "quality-pool" / "report.json").read_text(encoding="utf-8"))
    for row in report["test_set"]:
        result = service.ask(f"{row['name']}怎么样")
        codes.update(result.data.get("reason_codes") or [])
    missing = sorted(code for code in codes if code not in REASON_PLAIN)
    assert not missing, missing
    for code in codes:
        text = reason_plain(code)
        assert text and text != code


def test_v4_byd_card_does_not_repeat_reason(service):
    text = service.ask("比亚迪怎么样").markdown
    assert text.count("未来五年预计自由现金流为负") == 1
    assert text.count("按最悲观的经营假设算出的每股价值为负") == 1
    assert text.count("三档价值：无") == 1
    assert "FCFF" not in text
    assert "主模型" not in text
    assert "机制默认" not in text
    assert "终值连续性护栏" not in text


def test_v5_cycle_label(service):
    assert "周期属性 非周期" in service.ask("茅台怎么样").markdown
    assert "非金融企业" not in service.ask("茅台怎么样").markdown
    assert "周期属性 明显周期" in service.ask("中国神华怎么样").markdown
    assert "周期属性 非周期（保险）" in service.ask("中国平安").markdown


def test_w1_claude_code_session_evidence_present():
    doc = ROOT / "docs" / "phase4k-r2-claude-session.md"
    assert doc.exists()
    text = doc.read_text(encoding="utf-8")
    assert "./ask" in text
    assert "A股有哪些值得投资的公司" in text
    assert "茅台怎么样" in text
    for name in ("claude-code-q1.png", "claude-code-q2.png"):
        path = ROOT / "docs" / "evidence" / name
        assert path.exists() and path.stat().st_size > 1000


def test_claude_md_present_and_short():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 40
    assert "投资分析系统" in text
    assert "不得补充" in text or "不补充" in text
