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
from engine.quality.tables import read_table

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


# --- r3：X1–X7 -------------------------------------------------------------
def test_x1_pool_and_price_words_go_ranking(service):
    questions = (
        "收息池有哪些公司现在的价格很合适",
        "好公司池里哪些现在便宜",
        "好公司池哪些划算",
        "收息池里哪些低估",
        "好公司池里现在哪些值得买",
        "收息池哪些能买",
        "好公司池里价格合适的公司",
        "收息池离买点最近的公司",
        "银行池里哪些便宜",
    )
    for question in questions:
        result = service.ask(question)
        assert result.intent.name == "ranking", question
        if "银行池" in question:
            assert "银行池不排行" in result.markdown, question
            assert "不良率" in result.markdown, question
        else:
            assert "入池不等于买入" in result.markdown, question


def test_x2_pool_list_has_price_question_hint(service):
    quality = service.ask("好公司池有哪些").markdown
    income = service.ask("收息池有哪些公司").markdown
    assert "想看哪些现在离买点最近，可问：好公司池里谁离买点最近" in quality
    assert "想看哪些现在离买点最近，可问：稳定收息池里谁离买点最近" in income


def test_x3_x4_x5_bank_card_for_banks_outside_pool(service):
    for question, ownership in (("平安银行怎么样", "民企"), ("宁波银行怎么样", "地方国企")):
        result = service.ask(question)
        text = result.markdown
        assert "是否在银行池：否" in text, question
        for label in ("不良率", "拨备覆盖率", "核心一级", "所有制"):
            assert label in text, (question, label)
        assert ownership in text, (question, ownership)
        assert "银行专用估值方法还没启用（股东 2026-11-13 决定）" in text
        for internal in ("bank-check", "draft", "bank-pool", "equity-policy", "ROE"):
            assert internal not in text, (question, internal)


def test_x6_index_card_sources_uses_and_plain_signal(service):
    for question in ("沪深300 现在什么位置", "中证500 贵不贵", "中证1000 什么位置"):
        text = service.ask(question).markdown
        assert "月度参考" in text and "用于近 5 年分位" in text, question
        assert "官方周度" in text and "用于研究信号" in text, question
        assert "两个 PE 为什么不同" in text, question
        assert "比过去 5 年里" in text and ("都贵" in text or "都便宜" in text), question
        assert "按研究规则处于" in text, question
        assert "研究信号，规则未批准" in text, question
        for term in ("entry", "exit", "研究画像", "候选 A"):
            assert term not in text, (question, term)


def test_x6_hstech_card_plain(service):
    text = service.ask("恒生科技").markdown
    assert "官方月度" in text
    assert "只作月度快照" in text
    assert "无法给出近 5 年位置" in text
    for term in ("entry", "exit", "研究画像", "候选 A"):
        assert term not in text, term


def test_x7_index_position_bands():
    from engine.ask.index_bands import band_for

    expected = {
        0: "偏低",
        19.9: "偏低",
        20: "较低",
        39.9: "较低",
        40: "居中",
        59.9: "居中",
        60: "较高",
        79.9: "较高",
        80: "偏高",
        100: "偏高",
    }
    for percentile, label in expected.items():
        band = band_for(percentile)
        assert band is not None
        assert band["label"] == label, percentile
        assert "目前估值处于近 5 年" in band["summary"]


def test_x7_index_conclusion_has_position_summary(service):
    for question in ("沪深300 现在什么位置", "中证500 贵不贵", "中证1000 什么位置"):
        first = service.ask(question).markdown.splitlines()[0]
        assert first.startswith("**结论：无法判断** — 目前估值处于近 5 年")
        assert "位置。" in first


def test_x7_bands_config_is_versioned():
    import json

    path = ROOT / "data" / "policies" / "index-position-bands-v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "index-position-bands/1"
    assert len(payload["bands"]) == 5
    assert [band["label"] for band in payload["bands"]] == ["偏低", "较低", "居中", "较高", "偏高"]


def test_x3_all_in_scope_banks_have_indicator_and_ownership():
    service = AskService(allow_update=False, write_ledger=False)
    inputs = service._bank_inputs()
    checked = 0
    for code, row in inputs.items():
        if str(row.get("in_scope")) != "True":
            continue
        checked += 1
        indicator = service._bank_indicator(code)
        for key in ("npl_ratio", "provision_coverage", "core_tier1_ratio"):
            assert indicator.get(key) not in (None, ""), (code, key)
        assert service._bank_ownership(code), code
    assert checked == 36


# --- batch4l：银行估值 v2 试运行 + 历史位置 --------------------------------
def _bank_v2_fixture():
    from engine.bank.valuation_v2 import BankValuationInputs, load_policy, value_bank
    from engine.policy.equity import load_policy as load_equity_policy
    from engine.policy.valuation import load_macro_series
    from engine.quality.tables import read_table
    from pathlib import Path

    policy = load_policy()
    equity_policy = load_equity_policy("data/policies/equity-policy-v1.1.json")
    macro = load_macro_series(
        Path(equity_policy.node("discount_rate.risk_free")["series_file"])
    )
    rows = {
        row["code"]: row
        for row in read_table(Path("data/bank-pool/valuation-v2/inputs.csv.gz"))
    }
    pool_codes = {"600036", "601398"}
    inputs = {
        code: BankValuationInputs.from_row(rows[code], in_bank_pool=code in pool_codes)
        for code in ("600036", "601398")
    }
    return policy, equity_policy, macro, inputs, value_bank


def test_z1_cmb_hand_calculation():
    policy, equity_policy, macro, inputs, value_bank = _bank_v2_fixture()
    out = value_bank(inputs["600036"], policy, equity_policy, macro)
    bvps = 46.867733
    roe_base = min(0.14716666666666667, 0.15633999999999998)
    roe_pess = 0.1344 - 0.01
    roe_opt = 0.15633999999999998
    k = out["risk_free"] + out["equity_risk_premium"] + 0.02
    expected = {
        "pessimistic": bvps * (roe_pess - 0.015) / (k - 0.015),
        "base": bvps * (roe_base - 0.02) / (k - 0.02),
        "optimistic": bvps * (roe_opt - 0.025) / (k - 0.025),
    }
    for name, value in expected.items():
        assert abs(out["values"][name] - value) < 0.02, name
    assert abs(out["cost_of_equity"] - k) < 1e-12
    assert out["asset_quality_deduction"] is False
    assert out["conclusion"] == "candidate_buy_point"


def test_z1_icbc_hand_calculation():
    policy, equity_policy, macro, inputs, value_bank = _bank_v2_fixture()
    out = value_bank(inputs["601398"], policy, equity_policy, macro)
    bvps = 11.239444
    roe_base = min(0.09996666666666666, 0.10718000000000001)
    roe_pess = 0.09449999999999999 - 0.01
    roe_opt = 0.10718000000000001
    k = out["risk_free"] + out["equity_risk_premium"] + 0.02
    expected = {
        "pessimistic": bvps * (roe_pess - 0.015) / (k - 0.015),
        "base": bvps * (roe_base - 0.02) / (k - 0.02),
        "optimistic": bvps * (roe_opt - 0.025) / (k - 0.025),
    }
    for name, value in expected.items():
        assert abs(out["values"][name] - value) < 0.02, name
    assert out["conclusion"] == "wait"


def test_z2_report_has_required_sections_and_sensitivity():
    payload = json.loads((ROOT / "data" / "bank-pool" / "valuation-v2.json").read_text(encoding="utf-8"))
    text = (ROOT / "docs" / "phase4l-bank-valuation-v2.md").read_text(encoding="utf-8")
    assert payload["meta"]["policy"] == "bank-valuation-v2-draft"
    assert payload["meta"]["policy_approved"] is False
    assert len(payload["banks"]) == 36
    assert len(payload["comparison_v1"]) == 36
    assert {item["bank_premium"] for item in payload["sensitivity_k"]} == {0.01, 0.02, 0.03}
    assert {item["margin_of_safety"] for item in payload["sensitivity_margin"]} == {0.20, 0.30, 0.40}
    for section in ("三档 ROE", "敏感性", "第八批", "历史估值位置", "每股净资产", "门槛价"):
        assert section in text, section
    for item in payload["banks"]:
        assert item["values"]["pessimistic"] is not None
        assert item["multiples"]["base"] is not None
        assert item["threshold_price"] is not None


def test_z2_policy_has_basis_for_every_parameter():
    policy = json.loads((ROOT / "data" / "policies" / "bank-valuation-v2-draft.json").read_text(encoding="utf-8"))
    for name, node in policy["parameters"].items():
        assert "basis" in node, name
    assert policy["method"]["formula"]
    assert policy["eligibility"]["bank_pool_required"] is True


def test_z3_non_bank_pool_banks_only_values_and_undetermined():
    payload = json.loads((ROOT / "data" / "bank-pool" / "valuation-v2.json").read_text(encoding="utf-8"))
    pool_codes = {
        "002839", "002966", "600036", "601009", "601166",
        "601288", "601328", "601398", "601658", "601939", "601998",
    }
    non_pool = [item for item in payload["banks"] if item["code"] not in pool_codes]
    assert len(non_pool) == 25
    for item in non_pool:
        assert item["conclusion"] == "undetermined"
        for name in ("pessimistic", "base", "optimistic"):
            assert item["values"][name] is not None
        assert any("不在已批准银行池" in reason for reason in item["reasons"])


def test_z4_history_position_same_source_and_card_line(service):
    manifest = json.loads(
        (ROOT / "data" / "bank-pool" / "valuation-v2" / "manifest.json").read_text(encoding="utf-8")
    )
    assert any("value_analysis" in source for source in manifest["sources"])
    assert any("dividend" in source for source in manifest["sources"])
    assert "同源" in manifest["tables"][1]["definition_note"]
    inputs = read_table(ROOT / "data" / "bank-pool" / "valuation-v2" / "inputs.csv.gz")
    assert len(inputs) == 42
    ok = [row for row in inputs if row["history_status"] == "ok"]
    assert len(ok) == 41
    for row in ok:
        assert int(float(row["history_months"])) >= 60
        assert row["history_start"] and row["history_end"]
    text = service.ask("招商银行怎么样").markdown
    assert "市净率处于近 10 年" in text
    assert "股息率处于" in text
    assert "同源同口径" in text
    insufficient = service.ask("兰州银行怎么样").markdown
    assert "不足 5 年" in insufficient
