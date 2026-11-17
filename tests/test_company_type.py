"""第六阶段 G1：类型先行——卡片第一行类型、护城河提示、排行按类型分组、认不出名字的提示。

类型判定只用已批准的三池名单（data/*-pool/report.json），不新增规则、不改结论。
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from engine.ask.company_type import (
    MOAT_HINT,
    TYPE_BANK,
    TYPE_INCOME,
    TYPE_MEANINGS,
    TYPE_ORDER,
    TYPE_OUTSIDE,
    TYPE_QUALITY,
    TYPE_QUALITY_INCOME,
    classify,
    hint_line,
    ordered_types,
    type_line,
)
from engine.ask.service import AskService

ROOT = Path(__file__).resolve().parent.parent
REPORTS = {
    "quality": ROOT / "data" / "quality-pool" / "report.json",
    "income": ROOT / "data" / "income-pool" / "report.json",
    "bank": ROOT / "data" / "bank-pool" / "report.json",
}
GROUP_RE = re.compile(r"^【(?P<type>.+?)】（(?P<count>\d+) 家）$")
ITEM_RE = re.compile(r"^- (?P<code>\d{6}) .+：现价")
DISTANCE_RE = re.compile(r"(?:距买点线\s*)?([+-]\d+\.\d+)%")


@pytest.fixture(scope="module")
def service() -> AskService:
    return AskService(
        allow_update=False,
        write_ledger=False,
        use_live_quotes=False,
        analysis_date=date(2026, 11, 14),
    )


@pytest.fixture(scope="module")
def pool_sets() -> dict[str, set[str]]:
    sets: dict[str, set[str]] = {}
    for name, path in REPORTS.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        sets[name] = {str(row["code"]).zfill(6) for row in payload["in_pool"]}
    return sets


def test_type_meaning_table_matches_construction_order():
    assert TYPE_ORDER == (
        TYPE_QUALITY,
        TYPE_QUALITY_INCOME,
        TYPE_INCOME,
        TYPE_BANK,
        TYPE_OUTSIDE,
    )
    assert TYPE_MEANINGS[TYPE_QUALITY].startswith("过去十年持续高回报")
    assert "利润稳、分红稳" in TYPE_MEANINGS[TYPE_QUALITY_INCOME]
    assert TYPE_MEANINGS[TYPE_INCOME].startswith("利润稳、分红稳")
    assert TYPE_MEANINGS[TYPE_BANK] == "按银行专用标准"
    assert TYPE_MEANINGS[TYPE_OUTSIDE].startswith("不在任何池")
    assert MOAT_HINT == "系统不判断护城河是否仍然存在，请自行研究其生意"
    assert type_line(TYPE_BANK) == f"类型：{TYPE_BANK}｜按银行专用标准"
    assert hint_line() == f"提示：{MOAT_HINT}。"
    assert ordered_types([TYPE_INCOME, TYPE_BANK, TYPE_QUALITY]) == [
        TYPE_QUALITY,
        TYPE_INCOME,
        TYPE_BANK,
    ]


def test_five_types_classified_from_approved_pool_lists(pool_sets):
    quality, income, bank = pool_sets["quality"], pool_sets["income"], pool_sets["bank"]
    quality_only = sorted(quality - income - bank)
    income_only = sorted(income - quality - bank)
    both = sorted(quality & income - bank)
    assert quality_only and income_only and both, "三池名单应同时覆盖三种非银行类型"

    assert classify(quality_only[0], pool_sets) == TYPE_QUALITY
    assert classify(income_only[0], pool_sets) == TYPE_INCOME
    assert classify(both[0], pool_sets) == TYPE_QUALITY_INCOME
    assert classify("600036", pool_sets) == TYPE_BANK  # 招商银行
    # 平安银行是银行但不在银行池；比亚迪不在任何池 → 池外
    assert classify("000001", pool_sets) == TYPE_OUTSIDE
    assert classify("002594", pool_sets) == TYPE_OUTSIDE


@pytest.mark.parametrize(
    ("name", "expected_type"),
    [
        ("贵州茅台", TYPE_QUALITY_INCOME),
        ("招商银行", TYPE_BANK),
        ("平安银行", TYPE_OUTSIDE),
        ("比亚迪", TYPE_OUTSIDE),
    ],
)
def test_stock_and_bank_cards_start_with_type(service, name, expected_type):
    result = service.ask(f"{name}怎么样")
    assert result.intent.name in ("stock", "bank"), (name, result.intent.name)
    lines = result.markdown.splitlines()
    assert lines[0].startswith(f"类型：{expected_type}｜"), (name, lines[0])
    assert lines[1] == hint_line(), (name, lines[1])
    assert lines[2] == ""
    assert any(line.startswith("**结论：") for line in lines), name
    assert result.data["company_type"] == expected_type
    assert result.markdown.count(f"提示：{MOAT_HINT}") == 1


def test_income_only_stock_card_type_matches_membership(service, pool_sets):
    code = sorted(pool_sets["income"] - pool_sets["quality"] - pool_sets["bank"])[0]
    name = service.batch.name_for(code)
    text = service.ask(f"{name}怎么样").markdown
    assert text.splitlines()[0].startswith(f"类型：{TYPE_INCOME}｜")
    membership = service.ask(f"{name}在不在稳定收息池").markdown
    assert "在稳定收息池" in membership


def test_non_stock_cards_keep_conclusion_first(service):
    for question in (
        "沪深300 现在什么位置",
        "恒生科技",
        "510300 能买吗",
        "好公司池有哪些",
        "格力在不在好公司池",
        "数据是哪天的",
        "茅台以前的结论",
    ):
        text = service.ask(question).markdown
        assert text.splitlines()[0].startswith("**结论："), question
        assert "类型：" not in text, question
        assert f"提示：{MOAT_HINT}" not in text, question


def _ranking_blocks(
    markdown: str,
) -> list[list[tuple[str, list[tuple[str, float | None]]]]]:
    """把卡片拆成若干「区块」，每个区块内是按类型分组的连续段落。

    排行卡片有两个区块：候选买点、距买点线最近（问「现在有哪些候选买点」时
    还有银行部分）；区块之间组内顺序各自独立。
    """
    blocks: list[list[tuple[str, list[tuple[str, float | None]]]]] = []
    current_block: list[tuple[str, list[tuple[str, float | None]]]] | None = None
    current_group: list[tuple[str, float | None]] | None = None
    for line in markdown.splitlines():
        match = GROUP_RE.match(line)
        if match:
            if current_block is None:
                current_block = []
                blocks.append(current_block)
            current_group = []
            current_block.append((match.group("type"), current_group))
            continue
        item = ITEM_RE.match(line)
        if item and current_group is not None:
            distance = DISTANCE_RE.search(line)
            current_group.append(
                (item.group("code"), float(distance.group(1)) if distance else None)
            )
            continue
        if line.strip() and not line.startswith("-"):
            current_block = None
            current_group = None
    return [block for block in blocks if block]


@pytest.mark.parametrize(
    "question",
    [
        "现在有哪些候选买点",
        "好公司里谁离买点最近",
        "收息池里谁离买点最近",
        "银行池里谁离买点最近",
    ],
)
def test_ranking_is_grouped_by_type_and_sorted_inside_group(
    service, pool_sets, question
):
    text = service.ask(question).markdown
    blocks = _ranking_blocks(text)
    assert blocks, f"{question} 没有按类型分组"
    for block in blocks:
        order = [TYPE_ORDER.index(company_type) for company_type, _items in block]
        assert order == sorted(order), f"{question} 分组顺序与施工单不一致"
        for company_type, items in block:
            assert items, f"{question} 的 {company_type} 分组没有条目"
            for code, _distance in items:
                assert classify(code, pool_sets) == company_type, (
                    question,
                    code,
                    company_type,
                )
            distances = [distance for _code, distance in items if distance is not None]
            assert distances == sorted(distances), (
                f"{question} 的 {company_type} 组内没有按距买点线排序"
            )


def test_ranking_group_headers_cover_all_shown_rows(service):
    text = service.ask("现在有哪些候选买点").markdown
    item_codes = {
        match.group("code")
        for line in text.splitlines()
        if (match := ITEM_RE.match(line))
    }
    grouped_codes = {
        code
        for block in _ranking_blocks(text)
        for _company_type, items in block
        for code, _distance in items
    }
    assert item_codes == grouped_codes
    assert "【银行池】" in text


@pytest.mark.parametrize(
    "question",
    ["星河科技怎么样", "请问星河科技贵不贵", "查一下星河科技能买吗"],
)
def test_unknown_name_gets_new_hint(service, question):
    text = service.ask(question).markdown
    assert "没找到叫『星河科技』的上市公司，请用证券简称或 6 位代码" in text, question
    assert text.splitlines()[0].startswith("**结论：无法判断**")
    assert "支持的问法" in text
    assert "不构成投资建议" in text


def test_no_name_question_keeps_help_card(service):
    text = service.ask("帮我看看").markdown
    assert "没找到叫" not in text
    assert "没有识别出问题里的标的或问法" in text
    assert "支持的问法" in text


def test_default_cards_stay_within_twenty_five_lines(service, pool_sets):
    questions = json.loads(
        (ROOT / "data" / "ask" / "questions.json").read_text(encoding="utf-8")
    )["questions"]
    for item in questions:
        markdown = service.ask(item["question"]).markdown
        assert len(markdown.splitlines()) <= 25, item["id"]
        if item["expect"]["intent"] in ("stock", "bank"):
            assert markdown.splitlines()[0].startswith("类型："), item["id"]
