"""第四阶段第十批：池成员批量估值（R1–R7）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from engine.pool_valuation.adapter import BatchStore, build_snapshot, load_evidence
from engine.policy.equity import load_policy
from engine.policy.trial import evaluate_company
from engine.policy.valuation import load_macro_series

ROOT = Path(__file__).resolve().parent.parent
POOL_DIR = ROOT / "data" / "pool-valuation"
MANIFEST = POOL_DIR / "manifest.json"
REGRESSION = POOL_DIR / "regression-30.json"
REPORT_JSON = POOL_DIR / "pool-member-valuation.json"
REPORT_MD = ROOT / "docs" / "phase4j-pool-valuation-report.md"
MAPPING = ROOT / "docs" / "phase4j-field-mapping.md"
POLICY = ROOT / "data" / "policies" / "equity-policy-v1.1.json"
MACRO = ROOT / "data" / "policy-trial" / "macro" / "cn-10y-yield.json"


@pytest.fixture(scope="module")
def batch():
    if not MANIFEST.exists():
        pytest.skip("池成员批量快照尚未构建")
    return BatchStore()


def test_batch_snapshot_size_and_replay(batch):
    manifest = batch.manifest
    assert manifest["schema_version"].startswith("quality-pool-snapshot/")
    assert manifest["total_bytes"] <= 200 * 1024 * 1024
    assert len(manifest["tables"]) >= 40
    assert any(entry["kind"] == "price" for entry in manifest["tables"])
    assert any(entry["kind"] == "shares" for entry in manifest["tables"])
    again = BatchStore()
    assert again.manifest["snapshot_id"] == manifest["snapshot_id"]
    snapshot = build_snapshot(batch, "600519")
    rebuilt = build_snapshot(again, "600519")
    assert snapshot.snapshot_id == rebuilt.snapshot_id
    assert snapshot.fact("fin.2025-12-31.revenue") is not None
    assert snapshot.fact("fin.2025-12-31.cash_equivalents_end") is not None
    assert snapshot.fact("profile.total_shares") is not None
    assert snapshot.fact("price.close").is_usable_number


def test_field_mapping_document_covers_policy_fields():
    text = MAPPING.read_text(encoding="utf-8")
    for column in (
        "TOTAL_OPERATE_INCOME",
        "END_CCE",
        "MINORITY_EQUITY",
        "INCOME_TAX",
        "FE_INTEREST_EXPENSE",
        "INVENTORY",
        "SHARE_CAPITAL",
        "TOTAL_SHARES",
    ):
        assert column in text
    assert "R3" in text


def test_regression_30_matches_original_paths():
    if not REGRESSION.exists():
        pytest.skip("30 只回归尚未运行")
    data = json.loads(REGRESSION.read_text(encoding="utf-8"))
    assert data["summary"]["total"] == 30
    assert data["summary"]["passed"] == 30, [
        row for row in data["rows"] if row["code"] not in {r["code"] for r in data["rows"] if r["same_status"] and r["same_grade"] and r["same_conclusion"] and (r["base_rel_diff"] or 0) <= 0.01}
    ]
    assert data["summary"]["base_diff_max"] <= 0.01
    for row in data["rows"]:
        assert row["same_status"] and row["same_grade"] and row["same_conclusion"]
        assert (
            row["base_old"] is None
            and row["base_new"] is None
        ) or row["base_rel_diff"] <= 0.01


def test_batch_snapshot_600519_recomputes_policy_inputs(batch):
    policy = load_policy(POLICY)
    macro = load_macro_series(MACRO)
    snapshot = build_snapshot(batch, "600519")
    evidence = load_evidence("600519", name="贵州茅台", industry="白酒", snapshot=snapshot)
    record = evaluate_company(policy, snapshot=snapshot, evidence=evidence, macro=macro, code="600519")
    assert record["status"] in ("complete", "partial", "insufficient_data", "unsupported")
    if record["status"] in ("complete", "partial"):
        assert record["values"]["base"] > 0
        assert record["inputs"]["cash_equivalents_end"] is not None


def test_pool_report_contains_all_members_and_sections():
    if not REPORT_JSON.exists():
        pytest.skip("池成员估值报告尚未生成")
    data = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    assert data["counts"]["pool_members"] == 221
    assert len(data["results"]) == 221
    banks = {
        str(row["code"]).zfill(6)
        for row in json.loads((ROOT / "data" / "bank-pool" / "report.json").read_text(encoding="utf-8"))["in_pool"]
    }
    assert not banks & {row["code"] for row in data["results"]}
    assert data["counts"]["conclusions"]["candidate_buy_point"] >= 0
    assert sum(data["counts"]["conclusions"].values()) == 221
    assert data["ranking"]
    for row in data["ranking"]:
        assert row["distance"] is not None
        assert row["grade"] in ("A", "B")
        assert row["pools"]
    assert data["undetermined"]
    assert data["ledger"]["eligible"] == 88
    assert data["ledger"]["baseline_before"] == 7
    assert data["ledger"]["existing_before"] >= 95
    assert data["ledger"]["conflicts"] == 0
    text = REPORT_MD.read_text(encoding="utf-8")
    for heading in ("结论分布", "距候选门槛", "无法判断的原因分组", "结论账本", "口径", "抽查复算"):
        assert heading in text


def test_show_recomputes_from_snapshot():
    result = subprocess.run(
        [sys.executable, "tools/run_pool_valuation.py", "--show", "600519"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-300:]
    payload = json.loads(result.stdout)
    assert payload["code"] == "600519"
    assert payload["status"] == "complete"
    assert payload["values"]["base"] > 0
    assert payload["inputs"]["cash_equivalents_end"] is not None


def test_existing_conclusion_records_unchanged():
    ledger = ROOT / "data" / "ledger" / "equity-conclusions.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    expected = [
        "concl-88c210a0d4d016ad",
        "concl-402f8110bf6c09c4",
        "concl-9957b79d595a05ee",
        "concl-ee16b991cc71c1d4",
        "concl-15b01838a5625ad4",
        "concl-338475441bcaa58b",
        "concl-74cec2debd853d5d",
    ]
    assert [row["record_id"] for row in rows[:7]] == expected
    assert len(rows) >= 7


def test_policy_and_valuation_code_unchanged():
    """第十批的 R2 证明；第十一批只允许改结论标签，不改政策规则与估值。"""
    valuation = subprocess.run(
        ["git", "diff", "--quiet", "e0da6d2", "--", "engine/valuation"], cwd=ROOT
    )
    assert valuation.returncode == 0, "估值代码被修改"
    policy = subprocess.run(
        ["git", "diff", "e0da6d2", "--", "engine/policy"], cwd=ROOT, capture_output=True, text=True
    )
    changed = [
        line
        for line in policy.stdout.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        and "is_trial_only" not in line
        and "正式结论" not in line
        and "预演结论" not in line
        and "政策 v1.1 已于" not in line
        and "不再标" not in line
        and "只有指数候选 A" not in line
    ]
    assert not changed, "政策代码除结论标签外被修改：\n" + "\n".join(changed[:20])
