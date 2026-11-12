"""银行估值预演 v2（按个股政策 v1.1 重跑）的公式、检查、结论与报告测试。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from engine.policy.equity import load_policy
from engine.policy.valuation import load_macro_series, normalized_risk_free
from engine.quality.tables import read_table
from tools.run_bank_valuation_rehearsal import (
    BANK_CHECK,
    INPUTS,
    OUTPUT_JSON,
    OUTPUT_MD,
    _conclusion,
    bank_check,
    run_rehearsal,
)

ROOT = Path(__file__).resolve().parent.parent
EQUITY_POLICY = ROOT / "data" / "policies" / "equity-policy-v1.1.json"
TOOL = ROOT / "tools" / "run_bank_valuation_rehearsal.py"


def _num(value):
    return None if value in (None, "") else float(value)


def _growth_paths(row: dict, policy) -> tuple[list[float], float | None]:
    cap = policy.value("forecast.growth_cap")
    base_revenue = _num(row["base_revenue"])
    revenue_2020 = _num(row["revenue_2020"])
    revenue_2022 = _num(row["revenue_2022"])
    cagr5 = (base_revenue / revenue_2020) ** (1 / 5) - 1 if base_revenue and revenue_2020 else None
    cagr3 = (base_revenue / revenue_2022) ** (1 / 3) - 1 if base_revenue and revenue_2022 else None
    latest_yoy = _num(row.get("latest_revenue_yoy"))
    year1 = min(latest_yoy, cap) if latest_yoy is not None else min(cagr5, cap)
    steady = min(cap, cagr5, cagr3) if cagr3 is not None else min(cap, cagr5)
    return [year1 + (steady - year1) * index / 4 for index in range(5)], cagr3


def _hand_value(row: dict, policy, risk_free: float, scenario: str) -> float:
    coe = risk_free + policy.value("discount_rate.equity_risk_premium")
    payout = _num(row["payout_3y"])
    roe_latest = _num(row["roe_latest"])
    roe_5y = _num(row["roe_5y"])
    base_revenue = _num(row["base_revenue"])
    base_profit = _num(row["base_net_profit"])
    shares = _num(row["shares"])
    base_margin = _num(row["net_margin_base"])
    min_margin = _num(row["net_margin_min_5y"])
    path, _cagr3 = _growth_paths(row, policy)
    adjustment = float(policy.node(f"scenarios.{scenario}")["growth_adjustment"])
    growth = [value + adjustment for value in path]
    requested = float(policy.node(f"scenarios.{scenario}")["terminal_growth"])
    terminal_g = min(
        requested,
        policy.value("terminal.growth_cap"),
        risk_free,
        roe_latest * (1.0 - payout),
    )
    roi = coe if scenario in ("pessimistic", "base") else (roe_5y if roe_5y is not None else coe)
    if roi <= terminal_g:
        roi = coe
    reinvestment = terminal_g / roi
    eps = base_profit / shares
    if scenario == "pessimistic":
        eps *= min(1.0, min_margin / base_margin)
    present_value = 0.0
    for index, growth_rate in enumerate(growth, start=1):
        eps = eps * (1.0 + growth_rate)
        dps = eps * payout
        present_value += dps / (1.0 + coe) ** index
    terminal_dps = eps * (1.0 + terminal_g) * (1.0 - reinvestment)
    present_value += terminal_dps / (coe - terminal_g) / (1.0 + coe) ** 5
    return present_value


@pytest.fixture(scope="module")
def policy():
    return load_policy(EQUITY_POLICY)


@pytest.fixture(scope="module")
def rehearsal():
    if not OUTPUT_JSON.exists():
        run_rehearsal()
    return json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))


def test_cmb_and_icbc_hand_calculation(policy, rehearsal):
    macro = load_macro_series(policy.node("discount_rate.risk_free")["series_file"])
    risk_free = float(normalized_risk_free(policy, macro)["normalized"])
    rows = {row["code"]: row for row in read_table(INPUTS)}
    by_code = {row["code"]: row for row in rehearsal["banks"]}
    for code in ("600036", "601398"):
        expected = {
            scenario: _hand_value(rows[code], policy, risk_free, scenario)
            for scenario in ("pessimistic", "base", "optimistic")
        }
        actual = by_code[code]["values"]
        for scenario in expected:
            assert expected[scenario] == pytest.approx(actual[scenario], rel=1e-9, abs=1e-9), (
                code,
                scenario,
            )
        # 终端价值按政策 R1 的新投资回报率计算：基准/悲观 = 股权成本，乐观 = 近 5 年 ROE。
        assert by_code[code]["discount"]["cost_of_equity"] == pytest.approx(risk_free + policy.value("discount_rate.equity_risk_premium"))


def test_only_policy_and_bank_check_are_read(policy, rehearsal):
    source = TOOL.read_text(encoding="utf-8")
    assert "bank-valuation-rehearsal-v1.json" not in source
    assert "equity-policy-v1.1.json" in source
    assert "bank-check-v1-draft.json" in source
    assert rehearsal["meta"]["policy"] == "equity-policy-v1.1"
    assert rehearsal["meta"]["bank_check"] == "bank-check-v1-draft"
    assert rehearsal["meta"]["cost_of_equity"] == pytest.approx(0.07902935833333333)
    assert "required_cet1_ratio" not in json.dumps(rehearsal["meta"], ensure_ascii=False)
    assert "rwa_density" not in json.dumps(rehearsal["meta"], ensure_ascii=False)


def test_old_rehearsal_params_are_not_read(monkeypatch):
    import builtins

    real_open = builtins.open

    def guarded(file, *args, **kwargs):
        if str(file).endswith("bank-valuation-rehearsal-v1.json"):
            raise AssertionError("预演不得读取旧参数文件")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    payload = run_rehearsal()
    assert len(payload["banks"]) == 36


def test_bank_check_boundaries():
    config = json.loads(BANK_CHECK.read_text(encoding="utf-8"))
    base = {"cet1_ratio": 8.5, "npl_ratio": 1.0, "provision_coverage": 150.0}
    passed, failures = bank_check(base, config)
    assert passed is True and failures == []
    low_cet1 = dict(base, cet1_ratio=8.49)
    assert bank_check(low_cet1, config)[0] is False
    low_provision = dict(base, provision_coverage=149.99)
    assert bank_check(low_provision, config)[0] is False
    missing = dict(base)
    missing.pop("npl_ratio")
    assert bank_check(missing, config)[0] is False


def test_conclusion_mapping():
    values = {"pessimistic": 8.0, "base": 10.0, "optimistic": 14.0}
    # 门槛 = 10 * 0.6 = 6；现价 5 低于门槛且低于悲观 → 候选买点
    assert _conclusion(grade="B", complete=True, price=5.0, values=values, margin=0.4)[0] == "candidate_buy_point"
    # 现价 7：高于安全边际门槛，但低于悲观 → 等待，起约束条件为安全边际
    conclusion, binding, _ = _conclusion(
        grade="B", complete=True, price=7.0, values=values, margin=0.4
    )
    assert conclusion == "wait" and binding == "安全边际"
    # 现价 15 高于乐观 → 回避
    assert _conclusion(grade="B", complete=True, price=15.0, values=values, margin=0.4)[0] == "avoid"
    # C 级 → 无法判断
    assert _conclusion(grade="C", complete=True, price=5.0, values=values, margin=0.4)[0] == "undetermined"


def test_report_columns_and_sensitivity(rehearsal):
    assert OUTPUT_MD.exists()
    text = OUTPUT_MD.read_text(encoding="utf-8")
    for heading in ("现价", "价格日期", "安全边际门槛价", "预演结论", "起约束条件", "距门槛", "终值占比", "股权成本 ±0.5pp"):
        assert heading in text
    assert len(rehearsal["banks"]) == 36
    for row in rehearsal["banks"]:
        assert row["grade"] in ("B", "C")
        assert row["conclusion"] in ("candidate_buy_point", "wait", "avoid", "undetermined")
        assert len(row["sensitivity"]) == 3
        if row["status"] == "complete":
            assert set(row["values"]) == {"pessimistic", "base", "optimistic"}
            assert row["threshold_price"] is not None
            assert row["distance"] is not None
    assert rehearsal["summary"]["grade_b"] + rehearsal["summary"]["grade_c"] + rehearsal["summary"]["insufficient"] == 36


def test_bank_pool_and_other_ledgers_unchanged(rehearsal):
    manifest = json.loads((ROOT / "data" / "bank-pool" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["snapshot_id"] == "qps-dc9c12160476db8b"
    report = json.loads((ROOT / "data" / "bank-pool" / "report.json").read_text(encoding="utf-8"))
    assert report["counts"]["in_pool"] == 11
    assert report["meta"]["snapshot_id"] == manifest["snapshot_id"]
    conclusions = [
        json.loads(line)
        for line in (ROOT / "data" / "ledger" / "equity-conclusions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(conclusions) == 7
    assert not any(row.get("code") == "600036" for row in conclusions)
    quality = json.loads((ROOT / "data" / "quality-pool" / "report.json").read_text(encoding="utf-8"))
    assert quality["counts"]["in_pool"] == 138
    income = json.loads((ROOT / "data" / "income-pool" / "report.json").read_text(encoding="utf-8"))
    assert income["counts"]["in_pool"] == 142


def test_cli_bank_conclusion_unchanged():
    result = subprocess.run(
        [sys.executable, "-m", "engine.cli", "--json", "600036"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-500:]
    payload = json.loads(result.stdout)
    assert payload["analysis_status"] == "insufficient_data"
    assert payload.get("valuation") is None
