#!/usr/bin/env python
"""银行估值预演：用银行数据补齐资本/不良/拨备证据，按个股政策 v1.1 银行条款跑一次。

只写预演报告（JSON + Markdown），不写正式结论账本，不改变 CLI 结论。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.bank.evaluator import evaluate_company  # noqa: E402
from engine.quality.policy import load_policy  # noqa: E402
from engine.quality.snapshot import load_snapshot_dir  # noqa: E402
from engine.quality.tables import read_table  # noqa: E402
from engine.valuation.bank import (  # noqa: E402
    BankDividendDrivers,
    BankRiskEvidence,
    value_bank_dividend,
)
from engine.valuation.errors import ValuationError  # noqa: E402

BANK_DIR = ROOT / "data" / "bank-pool"
BANK_POLICY = ROOT / "data" / "policies" / "bank-pool-v1-draft.json"
EQUITY_POLICY = ROOT / "data" / "policies" / "equity-policy-v1.1.json"
REHEARSAL_INPUT = ROOT / "data" / "policy-inputs" / "bank-valuation-rehearsal-v1.json"
OUTPUT_JSON = BANK_DIR / "valuation-rehearsal.json"
OUTPUT_MD = ROOT / "docs" / "phase4h-bank-valuation-rehearsal.md"


def _num(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _annual(company) -> dict[int, dict]:
    output = {}
    for row in company.bank_reports:
        if str(row.get("report_type") or "") != "年报":
            continue
        day = str(row.get("report_date") or "")[:10]
        if day[4:10] == "-12-31":
            output[int(day[:4])] = dict(row)
    return output


def _latest_with(company, key: str) -> dict | None:
    rows = [dict(item) for item in company.bank_reports if _num(item.get(key)) is not None]
    rows.sort(key=lambda row: str(row.get("report_date") or ""))
    return rows[-1] if rows else None


def _payout(company, annual: dict[int, dict]) -> float | None:
    years = list(company.window_years)[-5:]
    dividend = sum(float(company.dividends.get(year) or 0.0) for year in years)
    profit = 0.0
    for year in years:
        row = annual.get(year)
        value = _num(row.get("parent_netprofit")) if row else None
        if value is None:
            return None
        profit += value
    if profit <= 0:
        return None
    return max(0.05, min(0.9, dividend / profit))


def _scenario_values(company, profile, annual, settings) -> tuple[list[dict], str | None]:
    latest = _latest_with(company, "core_tier1_ratio")
    npl = _latest_with(company, "npl_ratio")
    provision = _latest_with(company, "provision_coverage")
    if latest is None or npl is None or provision is None:
        return [], "缺核心一级/不良率/拨备覆盖率"
    shares = _num(profile.get("total_shares"))
    assets = _num(profile.get("total_assets_2025"))
    if not shares or not assets:
        return [], "缺总股本或总资产"
    base_row = annual.get(2025)
    if base_row is None or _num(base_row.get("parent_netprofit")) is None:
        return [], "缺 2025 年报归母净利润"
    base_profit = _num(base_row.get("parent_netprofit"))
    payout = _payout(company, annual)
    if payout is None:
        return [], "缺最近 5 年分红或净利润"
    prior_cet1 = _num((annual.get(2024) or {}).get("core_tier1_ratio"))
    prior_npl = _num((annual.get(2024) or {}).get("npl_ratio"))
    risk = BankRiskEvidence(
        cet1_ratio=_num(latest.get("core_tier1_ratio")) / 100.0,
        required_cet1_ratio=float(settings["required_cet1_ratio"]),
        npl_ratio=_num(npl.get("npl_ratio")) / 100.0,
        provision_coverage=_num(provision.get("provision_coverage")) / 100.0,
        required_provision_coverage=float(settings["required_provision_coverage"]),
        risk_weighted_assets=assets * float(settings["rwa_density"]),
        rwa_growth=float(settings["rwa_growth"]),
        cet1_ratio_prior=prior_cet1 / 100.0 if prior_cet1 is not None else None,
        npl_ratio_prior=prior_npl / 100.0 if prior_npl is not None else None,
    )
    roe = _num(base_row.get("roe_weighted"))
    values = []
    for name, scenario in settings["scenarios"].items():
        drivers = BankDividendDrivers(
            base_year=2025,
            base_net_profit_parent=base_profit,
            net_profit_growth=tuple(float(item) for item in scenario["net_profit_growth"]),
            payout=tuple(
                max(0.05, min(0.9, payout + float(scenario.get("payout_adjust") or 0.0)))
                for _ in scenario["net_profit_growth"]
            ),
            shares=shares,
            cost_of_equity=float(scenario["cost_of_equity"]),
            terminal_growth=float(scenario["terminal_growth"]),
            roe=roe / 100.0 if roe is not None else None,
        )
        try:
            valuation = value_bank_dividend(drivers, risk)
        except ValuationError as exc:
            return [], f"{name} 情景失败：{exc.message}"
        values.append(
            {
                "scenario": name,
                "note": scenario.get("note"),
                "value_per_share": valuation.value_per_share,
                "payout": drivers.payout[0],
                "issues": [issue.to_dict() for issue in valuation.issues],
            }
        )
    return values, None


def _unmet_conditions(result) -> list[str]:
    items = []
    for condition in result.conditions:
        if condition.status in ("fail", "pending") and condition.condition_id != "untested":
            items.append(f"{condition.label}：{condition.reason or condition.status}")
    return items


def run_rehearsal() -> dict:
    settings = json.loads(REHEARSAL_INPUT.read_text(encoding="utf-8"))
    equity = json.loads(EQUITY_POLICY.read_text(encoding="utf-8"))
    bank_clause = (equity.get("grades") or {}).get("banks") or {}
    max_grade = str(bank_clause.get("max_grade") or "B")
    bank_policy = load_policy(BANK_POLICY)
    manifest = json.loads((BANK_DIR / "manifest.json").read_text(encoding="utf-8"))
    snapshot, companies, _stats = load_snapshot_dir(BANK_DIR / manifest["current"])
    profiles = {
        str(row["code"]).zfill(6): row
        for row in read_table(BANK_DIR / manifest["current"] / "bank-profile.csv.gz")
    }
    rows = []
    for company in companies:
        result = evaluate_company(company, bank_policy)
        if result.status == "out_of_scope":
            continue
        annual = _annual(company)
        unmet = _unmet_conditions(result)
        latest_cet1 = _latest_with(company, "core_tier1_ratio")
        latest_provision = _latest_with(company, "provision_coverage")
        risk_ok = (
            latest_cet1 is not None
            and latest_provision is not None
            and _num(latest_cet1.get("core_tier1_ratio")) / 100.0
            >= float(settings["required_cet1_ratio"])
            and _num(latest_provision.get("provision_coverage")) / 100.0
            >= float(settings["required_provision_coverage"])
        )
        if latest_cet1 is None or latest_provision is None:
            values, error = [], "缺核心一级资本充足率或拨备覆盖率"
            status = "insufficient_data"
            grade = "C"
        elif not risk_ok:
            values = []
            status = "risk_check_failed"
            grade = "C"
            error = (
                "核心一级资本充足率或拨备覆盖率低于预演适用下限"
                f"（{float(settings['required_cet1_ratio']):.1%} / {float(settings['required_provision_coverage']):.0%}）"
            )
        else:
            values, error = _scenario_values(company, profiles.get(company.code, {}), annual, settings)
            status = "ok" if values else "insufficient_data"
            grade = max_grade if values else "C"
        conclusion = (
            f"{grade} 级预演；悲观/基准/乐观每股价值 "
            + " / ".join(f"{item['value_per_share']:.2f}" for item in values)
            + " 元；只作预演，未写入正式结论账本。"
            if values
            else f"{grade} 级预演（{error}）；不给出估值数字。"
        )
        rows.append(
            {
                "code": company.code,
                "name": company.name,
                "route": "bank_dividend_discount",
                "status": status,
                "grade": grade,
                "grade_cap": max_grade,
                "values": values,
                "unmet_conditions": unmet,
                "risk_checks_passed": risk_ok,
                "rehearsal_conclusion": conclusion,
                "error": error,
            }
        )
    summary = {"total": len(rows), "grade_b": 0, "grade_c": 0, "insufficient": 0}
    for row in rows:
        if row["status"] == "ok":
            if row["grade"] == max_grade:
                summary["grade_b"] += 1
            else:
                summary["grade_c"] += 1
        elif row["status"] == "risk_check_failed":
            summary["grade_c"] += 1
        else:
            summary["insufficient"] += 1
    payload = {
        "meta": {
            "as_of": snapshot["as_of"],
            "snapshot_id": snapshot["snapshot_id"],
            "policy": "equity-policy-v1.1 grades.banks",
            "max_grade": max_grade,
            "bank_pool_policy": bank_policy.policy_id,
            "rehearsal_input": settings["version"],
            "generated_at": snapshot["as_of"],
            "warning": "预演，不写入正式结论账本；银行估值模型仍为 research，是否启用由股东在决策③决定。",
        },
        "settings": settings,
        "summary": summary,
        "banks": rows,
    }
    BANK_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(f"预演 {len(rows)} 家：B {summary['grade_b']}、C {summary['grade_c']}、资料不足 {summary['insufficient']}")
    print(f"报告：{OUTPUT_JSON}；{OUTPUT_MD}")
    return payload


def render_markdown(payload: dict) -> str:
    meta = payload["meta"]
    summary = payload["summary"]
    lines = [
        "# 银行估值预演（个股政策 v1.1 银行条款）",
        "",
        "> **预演，不写入正式结论账本，不改变 CLI 结论。** 银行估值模型仍为 research，"
        "是否启用由股东在决策③一并决定。",
        "",
        f"- as_of：{meta['as_of']}",
        f"- 快照：`{meta['snapshot_id']}`",
        f"- 政策：{meta['policy']}（银行最高等级 {meta['max_grade']}）",
        f"- 预演假设：`{meta['rehearsal_input']}`（未批准）",
        "",
        "## 1. 汇总",
        "",
        "| 项 | 数量 |",
        "|---|---:|",
        f"| 范围内银行 | {summary['total']} |",
        f"| B 级预演 | {summary['grade_b']} |",
        f"| C 级预演 | {summary['grade_c']} |",
        f"| 资料不足 | {summary['insufficient']} |",
        "",
        "## 2. 逐家预演",
        "",
        "| 代码 | 名称 | 路线 | 状态 | 等级 | 悲观 | 基准 | 乐观 | 未满足条件 / 说明 |",
        "|---|---|---|---|---|---:|---:|---:|---|",
    ]
    for row in payload["banks"]:
        values = {item["scenario"]: item["value_per_share"] for item in row["values"]}
        note = row.get("error") or "；".join(row["unmet_conditions"]) or "—"
        note = note.replace("|", "／")
        if len(note) > 180:
            note = note[:178] + "…"
        def fmt(name):
            return f"{values[name]:.2f}" if name in values else "—"

        lines.append(
            f"| {row['code']} | {row['name']} | {row['route']} | {row['status']} | {row['grade']} | "
            f"{fmt('bear')} | {fmt('base')} | {fmt('bull')} | {note} |"
        )
    lines += [
        "",
        "## 3. 口径与限制",
        "",
        "- 路线：`bank_dividend_discount`；复用银行股息折现数学与资本一致性检查。",
        "- 证据：核心一级资本充足率、不良率、拨备覆盖率来自东方财富 F10 银行主要指标；"
        "取最新可用报告期。",
        "- 预演假设（未批准）：核心一级下限 8.5%、拨备下限 150%、风险加权资产 ≈ 总资产 × 65%、"
        "风险加权资产增速 3%；三情景的净利润增速、股权成本与终值增长见 "
        "`data/policy-inputs/bank-valuation-rehearsal-v1.json`。",
        "- 等级上限 B：政策原文「银行取得资本充足率、不良率、拨备覆盖率并通过第二阶段银行检查时，"
        "最高 B 级；否则 C」。预演把三项证据齐备且资本/拨备不低于适用下限视为通过银行检查。",
        "- 「未满足条件」列示银行池规则（12 条）中未通过的条目，与估值等级不是同一套门槛；"
        "等级只按政策银行条款的三项证据与资本/拨备检查判定。",
        "- 本报告不等于目标价，不构成买入建议；正式结论账本与 CLI 均未改变。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    run_rehearsal()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
