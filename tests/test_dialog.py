"""对话入口（batch4k）验收测试：意图、卡片结构、标签、账本与问题集。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from engine.ask.intent import SUPPORTED_QUESTIONS
from engine.ask.service import AskService

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "data" / "ask" / "questions.json"
LEDGER = ROOT / "data" / "ledger" / "equity-conclusions.jsonl"
SKILL = ROOT / "SKILL.md"
FORMAL_LABEL = "正式结论（政策 equity-policy-v1.1，已批准）"
CONCLUSIONS = ("候选买点", "等待", "回避", "无法判断")
FORBIDDEN = ("建议买入", "建议卖出", "推荐买入", "推荐卖出", "强烈推荐", "建议加仓", "建议减仓")


@pytest.fixture(scope="module")
def service() -> AskService:
    # 固定用批量快照价格，保证问题集可重复；不写正式账本
    return AskService(allow_update=False, write_ledger=False, use_live_quotes=False)


@pytest.fixture(scope="module")
def question_set() -> list[dict]:
    return json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]


def test_question_set_has_at_least_40_and_covers_every_intent(question_set):
    assert len(question_set) >= 40
    categories = {item["category"] for item in question_set}
    assert len(categories) >= 10
    counts = {}
    for item in question_set:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    assert counts["单只个股"] >= 3
    assert counts["银行"] >= 3
    assert counts["指数"] >= 3
    assert counts["ETF"] >= 3
    assert counts["池名单"] >= 3
    assert counts["是否在池"] >= 3
    assert counts["排行"] >= 3
    assert counts["历史"] >= 3
    assert counts["状态"] >= 3
    assert counts["范围外/歧义"] >= 3


def test_every_question_matches_expected(service, question_set):
    problems = []
    for item in question_set:
        result = service.ask(item["question"])
        expect = item["expect"]
        markdown = result.markdown
        if result.intent.name != expect["intent"]:
            problems.append(f"{item['id']} intent={result.intent.name} expected={expect['intent']}")
        code = result.target.code if result.target else None
        if expect.get("code") and code != expect["code"]:
            problems.append(f"{item['id']} code={code} expected={expect['code']}")
        if expect.get("conclusion") and result.data.get("conclusion") != expect["conclusion"]:
            problems.append(f"{item['id']} conclusion={result.data.get('conclusion')}")
        for needle in expect.get("contains") or []:
            if needle not in markdown:
                problems.append(f"{item['id']} missing {needle!r}")
        if expect.get("label") and expect["label"] not in markdown:
            problems.append(f"{item['id']} missing label {expect['label']!r}")
        assert len(markdown.splitlines()) <= 25, f"{item['id']} 超过 25 行"
        assert markdown.splitlines()[0].startswith("**结论："), f"{item['id']} 第一行不是结论"
        assert "不构成投资建议" in markdown, f"{item['id']} 缺免责声明"
        assert re.search(r"20\d\d-\d\d-\d\d", markdown), f"{item['id']} 缺数据日期"
        assert any(term in markdown for term in ("来源", "快照", "账本", "规则")), f"{item['id']} 缺来源"
    assert not problems, "\n".join(problems)


def test_card_structure(service):
    result = service.ask("茅台怎么样")
    lines = result.markdown.splitlines()
    assert lines[0].startswith("**结论：等待**")
    assert any(line.startswith("现价 ") for line in lines)
    assert any("三档价值（元/股）" in line for line in lines)
    assert any(line.startswith("为什么：") for line in lines)
    assert any(line.startswith("它是什么：") for line in lines)
    assert any(line.startswith("局限：") for line in lines)
    assert any(line.startswith("来源与日期：") for line in lines)
    assert lines[-1] == "不构成投资建议。"


def test_formal_and_research_labels(service):
    formal = service.ask("茅台怎么样").markdown
    assert FORMAL_LABEL in formal
    for question in ("沪深300 现在什么位置", "510300 能买吗"):
        text = service.ask(question).markdown
        assert "正式结论" not in text, question
        assert ("研究" in text) or ("未启用" in text), question


def test_research_never_has_formal_conclusion(service):
    for question in ("沪深300 现在什么位置", "恒生科技", "510300 能买吗"):
        text = service.ask(question).markdown
        assert "正式结论" not in text
        assert "**结论：无法判断**" in text


def test_forbidden_words_absent(service, question_set):
    for item in question_set:
        text = service.ask(item["question"]).markdown
        for word in FORBIDDEN:
            assert word not in text, f"{item['id']} 出现禁用词 {word}"


def test_detail_expands_and_keeps_disclaimer(service):
    default = service.ask("茅台怎么样")
    detail = service.ask("茅台怎么样", detail=True)
    assert len(default.markdown.splitlines()) <= 25
    assert len(detail.markdown.splitlines()) > len(default.markdown.splitlines())
    assert "【详情】" in detail.markdown
    assert detail.markdown.rstrip().endswith("不构成投资建议。")


def test_bank_card_has_all_required_metrics(service):
    text = service.ask("招商银行怎么样").markdown
    for label in ("市净率", "股息率", "净资产收益率", "不良率", "拨备覆盖率", "核心一级"):
        assert label in text


def test_index_card_has_position_and_research_signal(service):
    text = service.ask("沪深300 现在什么位置").markdown
    assert "近 5 年分位" in text
    assert "研究信号" in text
    assert "官方周度" in text
    assert "未批准" in text


def test_etf_card_has_product_facts_and_index(service):
    text = service.ask("510300 能买吗").markdown
    assert "产品资料" in text
    assert "跟踪指数位置" in text
    assert "管理费" in text


def test_insufficient_or_unsupported_explains_in_chinese(service):
    insurance = service.ask("中国平安").markdown
    assert "保险公司需要专门的估值方法" in insurance
    assert "保险" in insurance
    assert "cyclical" not in insurance
    bridge = service.ask("格力电器现在能买吗").markdown
    assert "经营现金流" in bridge
    assert "partial" not in bridge


def test_ledger_write_is_append_only_and_deduped(tmp_path):
    ledger = tmp_path / "equity.jsonl"
    service = AskService(
        allow_update=False,
        write_ledger=True,
        ledger_path=ledger,
        use_live_quotes=False,
    )
    first = service.ask("茅台怎么样")
    assert first.data["ledger"]["status"] == "added"
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1
    second = service.ask("茅台怎么样")
    assert second.data["ledger"]["status"] == "skipped"
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1
    # 无法判断不写入
    service.ask("京东方A怎么样")
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_help_lists_supported_questions(service):
    text = service.ask("今天天气怎么样").markdown
    assert "支持的问法" in text
    for item in SUPPORTED_QUESTIONS[:3]:
        assert item.split("：")[0] in text


def test_skill_file_tells_claude_to_present_verbatim():
    text = SKILL.read_text(encoding="utf-8")
    assert "./ask" in text
    assert "原样" in text
    assert "不得改写" in text or "不改写" in text
    assert "--detail" in text
    assert "不构成投资建议" in text


def test_cli_module_and_wrapper(tmp_path):
    import subprocess
    import sys

    wrapper = ROOT / "ask"
    assert wrapper.exists() and wrapper.stat().st_mode & 0o111
    result = subprocess.run(
        [sys.executable, "-m", "engine.ask", "--no-update", "--json", "数据是哪天的"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["intent"]["intent"] == "status"
    assert "行情" in payload["markdown"]


def test_acceptance_report_present_and_all_passed():
    data = json.loads((ROOT / "data" / "ask" / "acceptance.json").read_text(encoding="utf-8"))
    assert data["all_passed"] is True
    assert data["question_count"] >= 40
    assert data["question_passed"] == data["question_count"]
    assert len(data["sections"]["stocks"]) == 30
    assert len(data["sections"]["indices"]) == 4
    assert len(data["sections"]["banks"]) == 2
    assert len(data["sections"]["etfs"]) == 3
    assert (ROOT / "docs" / "phase4k-acceptance.md").exists()


def test_samples_document_has_five_real_cards():
    text = (ROOT / "docs" / "phase4k-samples.md").read_text(encoding="utf-8")
    assert text.count("## 样例") == 5
    assert "**结论：" in text
    assert text.count("不构成投资建议") >= 5
