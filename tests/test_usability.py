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
    doc = ROOT / "docs" / "archive" / "phase4k-r2-claude-session.md"
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
            assert "银行池" in result.markdown, question
            assert ("候选买点" in result.markdown) or ("距门槛" in result.markdown), question
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
        assert "不在银行池" in text and "只给价值数字" in text
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
    pool_codes = {"600036", "601398", "601166"}
    inputs = {
        code: BankValuationInputs.from_row(rows[code], in_bank_pool=code in pool_codes)
        for code in ("600036", "601398", "601166")
    }
    return policy, equity_policy, macro, inputs, value_bank


def test_zr1_cmb_hand_calculation_with_decline():
    policy, equity_policy, macro, inputs, value_bank = _bank_v2_fixture()
    out = value_bank(inputs["600036"], policy, equity_policy, macro)
    # 最新 13.44%、近 3 年平均 14.7167%、近 5 年平均 15.634%：基准取最新
    assert abs(out["roe"]["base"] - 0.1344) < 1e-12
    # 2022 17.06% -> 2025 13.44%，3 年年均下滑 1.2067pp；外推 3 年 = 9.82%
    assert abs(out["decline_annual"] - (0.1706 - 0.1344) / 3) < 1e-12
    assert abs(out["decline_extrapolated"] - 0.0982) < 1e-12
    assert abs(out["roe"]["pessimistic"] - 0.0982) < 1e-12
    assert abs(out["roe"]["optimistic"] - (0.1449 + 0.1622 + 0.1344) / 3) < 1e-12
    k = out["risk_free"] + out["equity_risk_premium"] + 0.02
    bvps = 46.867733
    expected = {
        "pessimistic": bvps * (0.0982 - 0.015) / (k - 0.015),
        "base": bvps * (0.1344 - 0.02) / (k - 0.02),
        "optimistic": bvps * (((0.1449 + 0.1622 + 0.1344) / 3) - 0.025) / (k - 0.025),
    }
    for name, value in expected.items():
        assert abs(out["values"][name] - value) < 0.02, name
    assert out["conclusion"] == "candidate_buy_point"
    assert out["binding_condition"] == "pessimistic_value"


def test_zr1_industrial_bank_hand_calculation_with_decline():
    policy, equity_policy, macro, inputs, value_bank = _bank_v2_fixture()
    out = value_bank(inputs["601166"], policy, equity_policy, macro)
    assert abs(out["roe"]["base"] - 0.0915) < 1e-12
    assert abs(out["decline_annual"] - (0.1385 - 0.0915) / 3) < 1e-12
    assert abs(out["decline_extrapolated"] - 0.0445) < 1e-12
    assert abs(out["roe"]["pessimistic"] - 0.0445) < 1e-12
    assert abs(out["roe"]["optimistic"] - (0.1064 + 0.0989 + 0.0915) / 3) < 1e-12
    k = out["risk_free"] + out["equity_risk_premium"] + 0.02
    bvps = 40.20864
    expected = {
        "pessimistic": bvps * (0.0445 - 0.015) / (k - 0.015),
        "base": bvps * (0.0915 - 0.02) / (k - 0.02),
        "optimistic": bvps * (((0.1064 + 0.0989 + 0.0915) / 3) - 0.025) / (k - 0.025),
    }
    for name, value in expected.items():
        assert abs(out["values"][name] - value) < 0.02, name
    assert out["conclusion"] == "wait"


def test_zr2_implicit_return_formula_and_card_line():
    policy, equity_policy, macro, inputs, value_bank = _bank_v2_fixture()
    out = value_bank(inputs["600036"], policy, equity_policy, macro)
    expected = 0.02 + (0.1344 - 0.02) / (42.93 / 46.867733)
    assert abs(out["implicit_return"] - expected) < 1e-12
    assert abs(out["implicit_return_vs_k"] - (expected - out["cost_of_equity"])) < 1e-12
    text = AskService(allow_update=False, write_ledger=False).ask("招商银行怎么样").markdown
    assert "隐含长期回报率：约 14.5%" in text
    assert "描述性，不参与结论" in text


def test_zr3_report_has_comparison_and_3x3_matrix():
    payload = json.loads((ROOT / "data" / "bank-pool" / "valuation-v2.1.json").read_text(encoding="utf-8"))
    text = (ROOT / "docs" / "archive" / "phase4l-bank-valuation-v2.1.md").read_text(encoding="utf-8")
    assert payload["meta"]["policy"] == "bank-valuation-v2.1-draft"
    assert len(payload["banks"]) == 36
    assert len(payload["comparison_v2"]) == 36
    assert len(payload["comparison_v1"]) == 36
    assert len(payload["matrix"]) == 3
    assert all(len(row) == 3 for row in payload["matrix"])
    for row in payload["matrix"]:
        for cell in row:
            assert cell["candidate_count"] == len(cell["candidates"])
            assert cell["candidate_count"] >= 0
    for section in ("3×3", "v2 → v2.1", "隐含长期回报率", "逐家结果", "敏感性"):
        assert section in text, section


def test_zr3_policy_has_basis_for_every_parameter():
    policy = json.loads((ROOT / "data" / "policies" / "bank-valuation-v2.1-draft.json").read_text(encoding="utf-8"))
    for name, node in policy["parameters"].items():
        assert "basis" in node, name
    assert policy["parameters"]["roe_base"]["mode"] == "min_latest_3y_5y"
    assert policy["parameters"]["roe_pessimistic"]["mode"] == "min5y_deduction_or_decline_extrapolation"
    assert policy["parameters"]["roe_optimistic"]["mode"] == "3y_avg"
    assert policy["parameters"]["implicit_return"]["growth"] == 0.02
    assert policy["method"]["formula"]


def test_zr3_non_bank_pool_banks_only_values_and_undetermined():
    payload = json.loads((ROOT / "data" / "bank-pool" / "valuation-v2.1.json").read_text(encoding="utf-8"))
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
    assert "东方财富市场市净率" in text
    assert "未做换算" in text
    insufficient = service.ask("兰州银行怎么样").markdown
    assert "不足 5 年" in insufficient


# --- batch4m：银行买点批准落地 ---------------------------------------------
def test_b1_approved_policy_matches_draft_and_has_approval_record():
    approved = json.loads((ROOT / "data" / "policies" / "bank-valuation-v2.1.json").read_text(encoding="utf-8"))
    draft = json.loads((ROOT / "data" / "policies" / "bank-valuation-v2.1-draft.json").read_text(encoding="utf-8"))
    assert approved["status"] == "approved" and approved["approved"] is True
    assert approved["parameters"] == draft["parameters"]
    assert approved["method"] == draft["method"]
    approval = approved["approval"]
    assert approval["approved_at"] == "2026-11-14"
    assert approval["next_review"] == "2027-05-31"
    assert "决策④" in approval["decision"]
    assert approved["known_gaps"] and approved["remediation"]
    old = json.loads((ROOT / "data" / "policy-inputs" / "bank-check-v1-draft.json").read_text(encoding="utf-8"))
    assert old["approved"] is False
    assert "bank-valuation-v2.1" in old["superseded_by"]


def test_b2_only_bank_v21_model_is_newly_validated():
    from engine.models.registry import MODELS_BY_ID, VALIDATED_MODEL_IDS

    assert MODELS_BY_ID["bank_valuation_v2"].status == "validated"
    assert MODELS_BY_ID["bank_valuation_v2"].version == "2.1.0"
    assert MODELS_BY_ID["bank_dividend_discount"].status == "research"
    assert MODELS_BY_ID["bank_dividend_discount"].version.endswith("-research")
    assert MODELS_BY_ID["index_valuation_profile"].status == "research"
    assert MODELS_BY_ID["index_strategy_a_valuation_reversion"].status == "research"
    assert MODELS_BY_ID["etf_execution_check"].status == "planned"
    assert VALIDATED_MODEL_IDS == {
        "equity_dcf_fcff",
        "equity_dividend_discount",
        "bank_valuation_v2",
    }


def test_b3_cli_bank_conclusions_match_v21_and_ledger_has_11(service):
    ledger = [
        json.loads(line)
        for line in (ROOT / "data" / "ledger" / "equity-conclusions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(ledger) == 110
    assert len(ledger[:99]) == 99
    assert all(str(row["code"]).zfill(6) not in {
        "002839", "002966", "600036", "601009", "601166", "601288",
        "601328", "601398", "601658", "601939", "601998",
    } for row in ledger[:99])
    bank_rows = ledger[99:]
    assert len(bank_rows) == 11
    assert all(row.get("record_type", "formal") == "formal" for row in bank_rows)
    payload = json.loads((ROOT / "data" / "bank-pool" / "valuation-v2.1.json").read_text(encoding="utf-8"))
    expected = {
        item["code"]: item["conclusion"]
        for item in payload["banks"]
        if item["code"] in {
            "002839", "002966", "600036", "601009", "601166", "601288",
            "601328", "601398", "601658", "601939", "601998",
        }
    }
    assert len(expected) == 11
    labels = {"candidate_buy_point": "候选买点", "wait": "等待", "avoid": "回避"}
    for code, conclusion in expected.items():
        text = service.ask(f"{next(item['name'] for item in payload['banks'] if item['code'] == code)}怎么样").markdown
        assert f"**结论：{labels[conclusion]}**" in text
        assert f"**结论：{labels[conclusion]}**" in text
    assert sum(1 for value in expected.values() if value == "candidate_buy_point") == 3


def test_b4_non_bank_pool_has_no_formal_conclusion_and_rank_includes_bank(service):
    for question in ("平安银行怎么样", "宁波银行怎么样", "上海银行怎么样"):
        text = service.ask(question).markdown
        assert "**结论：无法判断**" in text
        for label in ("候选买点", "等待", "回避"):
            assert label not in text, (question, label)
        assert "三档价值" in text and "隐含长期回报率" in text
    ranked = service.ask("银行池里谁离买点最近").markdown
    assert "银行池" in ranked and "候选买点" in ranked and "距门槛" in ranked
    all_rank = service.ask("现在有哪些候选买点").markdown
    assert "银行部分" in all_rank and "银行候选买点" in all_rank
    bank_rank = service.ask("银行里有哪些买点").markdown
    assert "银行池" in bank_rank and "候选买点" in bank_rank


def test_b5_bank_question_set_terms_and_limits(service):
    questions = json.loads((ROOT / "data" / "ask" / "questions.json").read_text(encoding="utf-8"))["questions"]
    bank_questions = [item for item in questions if item["category"] == "银行"]
    assert len(bank_questions) >= 5
    banned = set(BANNED_TERMS) | {
        "bank-check", "draft", "bank-pool", "equity-policy", "ROE", "研究画像", "entry", "exit"
    }
    for question in ("招商银行怎么样", "工商银行怎么样", "平安银行怎么样", "张家港行怎么样"):
        text = service.ask(question).markdown
        for term in banned:
            assert term not in text, (question, term)
        assert "局限：净资产收益率仍在下滑；报表坏账可能低估；只能前向验证。" in text
