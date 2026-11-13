"""周期行业清单补全、重跑与标签修正（batch4k 第一节/第二节）测试。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from engine.contracts import reason_codes as rc
from engine.evidence.applicability import classify_family
from engine.evidence.cyclical import is_cyclical_industry, list_summary, match_industry
from engine.evidence.models import AnnualPoint, BusinessEvidence
from engine.models.registry import MODELS_BY_ID
from engine.policy.equity import load_policy
from engine.policy.trial import evaluate_company
from engine.policy.valuation import load_macro_series
from engine.pool_valuation.adapter import BatchStore, build_snapshot, load_evidence

ROOT = Path(__file__).resolve().parent.parent
LIST = ROOT / "data" / "policies" / "cyclical-industries-v1.json"
RERUN = ROOT / "data" / "pool-valuation" / "cyclical-rerun.json"
REPORT = ROOT / "docs" / "phase4k-cyclical-rerun.md"
POLICY = ROOT / "data" / "policies" / "equity-policy-v1.1.json"
MACRO = ROOT / "data" / "policy-trial" / "macro" / "cn-10y-yield.json"


def _evidence(industry: str, annual) -> BusinessEvidence:
    return BusinessEvidence(
        instrument_id="EQUITY:000000.SH",
        display_name="测试标的",
        as_of="2026-11-12",
        version="test",
        prepared_at="2026-11-12T00:00:00+08:00",
        industry_clue=industry,
        segments=(),
        regional_segments=(),
        annual=tuple(
            AnnualPoint(fiscal_year=year, revenue=revenue, net_profit_parent=profit)
            for year, revenue, profit in annual
        ),
        sources=(),
    )


def test_list_is_versioned_with_shenwan_basis():
    payload = json.loads(LIST.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "cyclical-industry-list/1"
    assert payload["version"]
    assert "申万" in payload["basis"]["classification"]
    assert payload["basis"]["source_url"].startswith("http")
    summary = list_summary(LIST)
    names = {item["name"] for item in summary["exact_names"]}
    for required in (
        "煤炭开采",
        "油气开采Ⅱ",
        "普钢",
        "特钢Ⅱ",
        "工业金属",
        "小金属",
        "能源金属",
        "贵金属",
        "化学原料",
        "化学纤维",
        "农化制品",
        "塑料",
        "橡胶",
        "水泥",
        "玻璃玻纤",
        "航运港口",
        "养殖业",
        "生猪养殖",
        "肉鸡养殖",
    ):
        assert required in names, required


def test_matching_is_exact_for_chemical_subindustries():
    assert is_cyclical_industry("化学原料")
    assert is_cyclical_industry("基础化工 / 化学原料")
    item = match_industry("化学原料")
    assert item and item["group"] == "基础化工" and item["matched_by"] == "exact_name"
    # 「化学制药」不能因为包含「化学」被误判为周期
    assert not is_cyclical_industry("化学制药")
    assert not is_cyclical_industry("白酒Ⅱ")
    assert is_cyclical_industry("装修建材")  # 旧线索保留，避免反向改变已披露结论
    assert is_cyclical_industry("煤炭 / 动力煤 / 煤炭开采")


def test_classification_still_needs_volatility_confirmation():
    volatile = _evidence(
        "化学原料",
        [(2020, 100.0, 10.0), (2021, 140.0, 14.0), (2022, 110.0, 11.0), (2025, 150.0, 15.0)],
    )
    family, reasons = classify_family(volatile)
    assert family == "cyclical"
    assert reasons[0]["observed"]["cyclical_list"]["list_id"] == "cyclical-industries-v1"

    stable = _evidence(
        "化学原料",
        [(2020, 100.0, 10.0), (2021, 101.0, 10.0), (2022, 102.0, 10.0)],
    )
    assert classify_family(stable)[0] == "non_financial"


def test_rerun_changes_are_recorded():
    payload = json.loads(RERUN.read_text(encoding="utf-8"))
    assert payload["meta"]["cyclical_list"]["list_id"] == "cyclical-industries-v1"
    pool = payload["pool"]
    assert pool["total"] == 221
    changed = {row["code"]: row for row in pool["changes"]}
    assert set(changed) == {"601216", "600486"}
    junzheng = changed["601216"]
    assert junzheng["before"]["conclusion"] == "candidate_buy_point"
    assert junzheng["after"]["conclusion"] == "undetermined"
    assert junzheng["after"]["family"] == "cyclical"
    assert junzheng["after"]["cyclical_list"]["matched_name"] == "化学原料"
    assert junzheng["prior_values"]["base"] > 0
    assert junzheng["industry"] == "化学原料"
    affected = {row["code"] for row in pool["affected_not_changed"]}
    assert {"000920", "002749"}.issubset(affected)
    assert pool["ledger"]["added"] == []
    assert pool["ledger"]["conflicts"] == []
    assert payload["test_set"]["total"] == 30
    assert payload["test_set"]["changed_count"] == 0


def test_report_explains_junzheng_and_thresholds():
    text = REPORT.read_text(encoding="utf-8")
    assert "君正集团判定说明" in text
    assert "化学原料" in text
    assert "18.3%" in text
    assert "阈值" in text
    assert "不改" in text or "未改" in text
    assert "无法判断" in text


def test_policy_thresholds_unchanged():
    policy = json.loads(POLICY.read_text(encoding="utf-8"))["parameters"]
    assert policy["grade"]["unsupported_families"]["basis"].find("强周期") >= 0
    # 与第十批相同的政策摘要
    assert load_policy(POLICY).policy_digest == "pol-6d1a5387780fe6a2"


def test_validated_model_versions_have_no_research_suffix():
    for model_id in ("equity_dcf_fcff", "equity_dividend_discount"):
        model = MODELS_BY_ID[model_id]
        assert model.status == "validated"
        assert not model.version.endswith("-research")
    # research 模型仍保留后缀，标签边界清楚
    assert MODELS_BY_ID["index_valuation_profile"].version.endswith("-research")


def test_reason_code_description_is_current():
    text = rc.describe(rc.INDEX_NOT_IN_FIRST_BATCH)
    assert "首批" in text
    assert "恒生科技" in text
    assert "已接入指数" in text


def test_live_policy_conclusion_label_is_formal():
    policy = load_policy(POLICY)
    macro = load_macro_series(MACRO)
    batch = BatchStore()
    snapshot = build_snapshot(batch, "600519")
    evidence = load_evidence(
        "600519",
        name=batch.name_for("600519"),
        industry=batch.industry_for("600519"),
        snapshot=snapshot,
    )
    record = evaluate_company(policy, snapshot=snapshot, evidence=evidence, macro=macro, code="600519")
    conclusion = record["trial_conclusion"]
    assert conclusion["is_trial_only"] is False
    assert conclusion["label"] == "正式结论（政策 equity-policy-v1.1，已批准）"
    assert "预演" not in conclusion["label"]


def test_ask_card_uses_chinese_condition_names():
    from engine.ask.service import AskService

    service = AskService(allow_update=False, write_ledger=False, use_live_quotes=False)
    text = service.ask("茅台怎么样").markdown
    assert "近似项影响" in text
    assert "approximation_impact" not in text
