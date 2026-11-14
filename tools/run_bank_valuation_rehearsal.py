#!/usr/bin/env python
"""银行估值预演：完全按个股政策 v1.1 股息折现路线与 bank-check-v1-draft 重跑。

只写预演报告（JSON + Markdown），不写正式结论账本，不改变 CLI、银行池、其他池与账本。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.policy.equity import load_policy as load_equity_policy  # noqa: E402
from engine.policy.valuation import (  # noqa: E402
    CompanyInputs,
    build_discount,
    load_macro_series,
    normalized_risk_free,
    value_all_scenarios,
)
from engine.quality.tables import read_table  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
BANK_DIR = ROOT / "data" / "bank-pool"
EQUITY_POLICY = ROOT / "data" / "policies" / "equity-policy-v1.1.json"
BANK_CHECK = ROOT / "data" / "policy-inputs" / "bank-check-v1-draft.json"
INPUTS = BANK_DIR / "valuation-inputs.csv.gz"
OUTPUT_JSON = BANK_DIR / "valuation-rehearsal.json"
OUTPUT_MD = ROOT / "docs" / "archive" / "phase4h-bank-valuation-rehearsal.md"

SCENARIOS = ("pessimistic", "base", "optimistic")
SCENARIO_LABELS = {"pessimistic": "悲观", "base": "基准", "optimistic": "乐观"}
CONCLUSION_LABELS = {
    "candidate_buy_point": "候选买点",
    "avoid": "回避",
    "wait": "等待",
    "undetermined": "无法判断",
}


def _num(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes", "y")


def _cagr(current: float | None, earlier: float | None, years: int) -> float | None:
    if current is None or earlier is None or current <= 0 or earlier <= 0:
        return None
    return (current / earlier) ** (1.0 / years) - 1.0


def load_inputs() -> list[dict]:
    return read_table(INPUTS)


def build_company_inputs(row: dict, policy, risk_free: float) -> tuple[CompanyInputs, dict]:
    shares = _num(row.get("shares"))
    price = _num(row.get("price"))
    base_profit = _num(row.get("base_net_profit"))
    base_revenue = _num(row.get("base_revenue"))
    payout = _num(row.get("payout_3y"))
    roe_latest = _num(row.get("roe_latest"))
    roe_5y = _num(row.get("roe_5y"))
    net_margin_base = _num(row.get("net_margin_base"))
    net_margin_min_5y = _num(row.get("net_margin_min_5y"))
    cagr5 = _cagr(base_revenue, _num(row.get("revenue_2020")), 5)
    cagr3 = _cagr(base_revenue, _num(row.get("revenue_2022")), 3)
    cap = policy.value("forecast.growth_cap")
    latest_yoy = _num(row.get("latest_revenue_yoy"))
    if latest_yoy is None:
        year1 = min(cagr5, cap) if cagr5 is not None else 0.0
        year1_basis = "未取得最近一期累计营收同比：退回受上限约束的 5 年复合增速"
    else:
        year1 = min(latest_yoy, cap)
        year1_basis = "最近一期累计营收同比，受 10% 上限约束"
    required = {
        "shares": shares,
        "price": price,
        "base_profit": base_profit,
        "base_revenue": base_revenue,
        "payout": payout,
        "roe_latest": roe_latest,
        "net_margin_base": net_margin_base,
        "net_margin_min_5y": net_margin_min_5y,
        "cagr5": cagr5,
    }
    missing = [name for name, value in required.items() if value is None]
    company = CompanyInputs(
        instrument_id=f"EQUITY:{row['code']}.{'SH' if str(row['code']).startswith('6') else 'SZ'}",
        base_year=int(row["base_year"]),
        shares=shares or 0.0,
        price=price or 0.0,
        revenue=base_revenue or 0.0,
        ebit_margin=0.0,
        ebit_margin_years=(),
        tax_rate=0.0,
        tax_rate_years=(),
        da_pct=0.0,
        capex_pct=0.0,
        nwc_intensity=0.0,
        payout=payout or 0.0,
        payout_years=(2023, 2024, 2025),
        roe_latest=roe_latest,
        roe_5y=roe_5y,
        revenue_cagr5=cagr5,
        year1_growth=year1,
        year1_basis=year1_basis,
        debt=0.0,
        debt_components={},
        missing_debt_components=(),
        average_debt=0.0,
        interest_expense=None,
        monetary_funds=None,
        cash_equivalents_end=None,
        cash_equivalents_basis=None,
        non_cash_equivalents=None,
        operating_cash_need=0.0,
        available_cash=0.0,
        minority_equity=0.0,
        net_profit=None,
        net_profit_parent=base_profit,
        minority_profit=None,
        loan_advance=None,
        accept_deposit_interbank=None,
        total_assets=None,
        ocf_5y=0.0,
        profit_5y=0.0,
        loss_years=int(_num(row.get("loss_years_7")) or 0),
        available_years=10,
        ebitda=None,
        loss_years_7=int(_num(row.get("loss_years_7")) or 0),
        revenue_cagr3=cagr3,
        net_margin_base=net_margin_base,
        net_margin_min_5y=net_margin_min_5y,
        notes=tuple(missing),
    )
    return company, {"missing": missing, "growth": {"year1": year1, "cagr5": cagr5, "cagr3": cagr3}}


def bank_check(row: dict, config: dict) -> tuple[bool, list[str]]:
    missing = []
    cet1 = _num(row.get("cet1_ratio"))
    npl = _num(row.get("npl_ratio"))
    provision = _num(row.get("provision_coverage"))
    for name, value in (("核心一级资本充足率", cet1), ("不良贷款率", npl), ("拨备覆盖率", provision)):
        if value is None:
            missing.append(f"缺{name}")
    if missing:
        return False, missing
    failures = []
    if cet1 / 100.0 < float(config["cet1_min"]):
        failures.append(
            f"核心一级资本充足率 {cet1:.2f}% < {float(config['cet1_min']):.1%}"
        )
    if float(provision) / 100.0 < float(config["provision_min"]):
        failures.append(
            f"拨备覆盖率 {provision:.2f}% < {float(config['provision_min']):.0%}"
        )
    return not failures, failures


def _conclusion(
    *, grade: str, complete: bool, price: float | None, values: dict[str, float], margin: float
) -> tuple[str, str | None, float | None]:
    if not values or price is None:
        return "undetermined", None, None
    base = values["base"]
    pessimistic = values["pessimistic"]
    optimistic = values["optimistic"]
    threshold = base * (1.0 - margin)
    distance = price / threshold - 1.0 if threshold else None
    if grade != "B" or not complete:
        return "undetermined", None, distance
    if price <= threshold and price <= pessimistic:
        binding = "安全边际" if threshold <= pessimistic else "悲观价值"
        return "candidate_buy_point", binding, distance
    if price > optimistic:
        return "avoid", "现价高于乐观价值", distance
    if price > threshold:
        return "wait", "安全边际", distance
    if price > pessimistic:
        return "wait", "悲观价值", distance
    return "wait", None, distance


def _sensitivity_conclusion(
    *,
    company: CompanyInputs,
    policy,
    risk_free: float,
    discount,
    grade: str,
    complete: bool,
    price: float | None,
    margin: float,
    delta: float,
) -> dict:
    if not complete:
        return {"delta": delta, "conclusion": "undetermined", "values": None}
    adjusted = replace(
        discount,
        cost_of_equity=discount.cost_of_equity + delta,
    )
    scenarios = value_all_scenarios(
        policy,
        model_id="bank_dividend_discount",
        inputs=company,
        discount=adjusted,
        risk_free=risk_free,
    )
    values = {
        name: scenarios[name].value_per_share
        for name in SCENARIOS
    }
    conclusion, binding, distance = _conclusion(
        grade=grade,
        complete=complete,
        price=price,
        values=values,
        margin=margin,
    )
    return {
        "delta": delta,
        "conclusion": conclusion,
        "binding": binding,
        "distance": distance,
        "values": values,
    }


def rehearse(row: dict, policy, risk_free: float, config: dict) -> dict:
    company, meta = build_company_inputs(row, policy, risk_free)
    price = _num(row.get("price"))
    complete = _truthy(row.get("data_complete")) and not meta["missing"]
    missing = list(meta["missing"])
    if not complete:
        return {
            "code": row["code"],
            "name": row["name"],
            "route": "bank_dividend_discount",
            "status": "insufficient_data",
            "grade": "C",
            "price": price,
            "price_date": row.get("price_date"),
            "values": None,
            "terminal_share": None,
            "threshold_price": None,
            "conclusion": "undetermined",
            "binding": None,
            "distance": None,
            "bank_check_passed": False,
            "loss_years_7": _num(row.get("loss_years_7")),
            "data_complete": False,
            "unmet_conditions": [f"缺数据：{'、'.join(missing)}"] if missing else ["数据不完整"],
            "rehearsal_conclusion": "C 级预演（资料不足）；不给出估值数字。",
            "sensitivity": [],
            "growth": meta["growth"],
        }
    discount = build_discount(policy, company, risk_free=risk_free)
    scenarios = value_all_scenarios(
        policy,
        model_id="bank_dividend_discount",
        inputs=company,
        discount=discount,
        risk_free=risk_free,
    )
    values = {name: scenarios[name].value_per_share for name in SCENARIOS}
    check_passed, check_failures = bank_check(row, config)
    loss_years = int(_num(row.get("loss_years_7")) or 0)
    grade = "B" if check_passed and loss_years <= int(config["max_loss_years_7"]) else "C"
    unmet = list(check_failures)
    if loss_years > int(config["max_loss_years_7"]):
        unmet.append(f"最近 7 个财年亏损 {loss_years} 年 > {config['max_loss_years_7']}")
    margin = float(config["margin_of_safety"])
    conclusion, binding, distance = _conclusion(
        grade=grade,
        complete=True,
        price=price,
        values=values,
        margin=margin,
    )
    threshold = values["base"] * (1.0 - margin)
    terminal_share = scenarios["base"].terminal_share
    sensitivity = []
    for delta in (-0.005, 0.0, 0.005):
        sensitivity.append(
            _sensitivity_conclusion(
                company=company,
                policy=policy,
                risk_free=risk_free,
                discount=discount,
                grade=grade,
                complete=True,
                price=price,
                margin=margin,
                delta=delta,
            )
        )
    label = CONCLUSION_LABELS[conclusion]
    if conclusion == "candidate_buy_point":
        text = f"{label}（B 级，现价 ≤ 基准 × {1-margin:.0%} 且 ≤ 悲观）"
    elif conclusion == "avoid":
        text = f"{label}（现价高于乐观价值）"
    elif conclusion == "wait":
        text = f"{label}（未触发候选买点，也未高于乐观价值）"
    else:
        text = "无法判断（C 级或银行检查未通过）"
    return {
        "code": row["code"],
        "name": row["name"],
        "route": "bank_dividend_discount",
        "status": "complete",
        "grade": grade,
        "price": price,
        "price_date": row.get("price_date"),
        "values": values,
        "terminal_share": terminal_share,
        "threshold_price": threshold,
        "conclusion": conclusion,
        "binding": binding,
        "distance": distance,
        "bank_check_passed": check_passed,
        "loss_years_7": loss_years,
        "data_complete": True,
        "unmet_conditions": unmet,
        "rehearsal_conclusion": text,
        "sensitivity": sensitivity,
        "growth": meta["growth"],
        "discount": {
            "risk_free": discount.risk_free,
            "equity_risk_premium": discount.equity_risk_premium,
            "cost_of_equity": discount.cost_of_equity,
        },
    }


def run_rehearsal() -> dict:
    policy = load_equity_policy(EQUITY_POLICY)
    series = Path(policy.node("discount_rate.risk_free")["series_file"])
    if not series.is_absolute():
        series = ROOT / series
    macro = load_macro_series(series)
    risk_free_info = normalized_risk_free(policy, macro)
    risk_free = float(risk_free_info["normalized"])
    config = json.loads(BANK_CHECK.read_text(encoding="utf-8"))
    rows = [row for row in load_inputs() if _truthy(row.get("in_scope"))]
    banks = [rehearse(row, policy, risk_free, config) for row in rows]
    summary = {
        "total": len(banks),
        "grade_b": sum(1 for row in banks if row["grade"] == "B"),
        "grade_c": sum(1 for row in banks if row["grade"] == "C"),
        "insufficient": sum(1 for row in banks if row["status"] != "complete"),
        "candidate_buy_point": sum(1 for row in banks if row["conclusion"] == "candidate_buy_point"),
        "wait": sum(1 for row in banks if row["conclusion"] == "wait"),
        "avoid": sum(1 for row in banks if row["conclusion"] == "avoid"),
        "undetermined": sum(1 for row in banks if row["conclusion"] == "undetermined"),
    }
    payload = {
        "meta": {
            "as_of": datetime.now(TZ).date().isoformat(),
            "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
            "policy": policy.policy_id,
            "policy_digest": policy.policy_digest,
            "bank_check": config["version"],
            "bank_check_approved": config.get("approved", False),
            "risk_free": risk_free,
            "equity_risk_premium": policy.value("discount_rate.equity_risk_premium"),
            "cost_of_equity": risk_free + policy.value("discount_rate.equity_risk_premium"),
            "risk_free_detail": risk_free_info,
            "inputs": str(INPUTS.relative_to(ROOT)),
            "warning": "预演，不写入正式结论账本；银行估值模型仍为 research，是否启用由股东在决策③决定。",
        },
        "summary": summary,
        "banks": banks,
    }
    BANK_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUTPUT_MD.write_text(render_markdown(payload), encoding="utf-8")
    print(
        f"预演 {len(banks)} 家：B {summary['grade_b']}、C {summary['grade_c']}、"
        f"资料不足 {summary['insufficient']}；候选买点 {summary['candidate_buy_point']}、"
        f"等待 {summary['wait']}、回避 {summary['avoid']}、无法判断 {summary['undetermined']}"
    )
    print(f"报告：{OUTPUT_JSON}；{OUTPUT_MD}")
    return payload


def _fmt(value, *, pct: bool = False, digits: int = 2) -> str:
    if value is None:
        return "—"
    if pct:
        return f"{float(value):.{digits}%}"
    return f"{float(value):.{digits}f}"


def render_markdown(payload: dict) -> str:
    meta = payload["meta"]
    summary = payload["summary"]
    lines = [
        "# 银行估值预演（按个股政策 v1.1 股息折现路线重跑）",
        "",
        "> **预演，不写入正式结论账本，不改变 CLI、银行池、其他池与账本。** "
        "银行估值模型仍为 research，是否启用由股东在决策③一并决定。",
        "",
        f"- 生成时间：{meta['generated_at']}",
        f"- 政策：`{meta['policy']}`（摘要 `{meta['policy_digest']}`）",
        f"- 银行检查：`{meta['bank_check']}`（approved={str(meta['bank_check_approved']).lower()}）",
        f"- 规范化无风险利率：{meta['risk_free']:.4%}；股权风险溢价：{meta['equity_risk_premium']:.4%}；"
        f"股权成本：{meta['cost_of_equity']:.4%}",
        f"- 预演输入：`{meta['inputs']}`（现价与日期来自东方财富行情）",
        "",
        "## 1. 汇总",
        "",
        "| 项 | 数量 |",
        "|---|---:|",
        f"| 范围内银行 | {summary['total']} |",
        f"| B 级预演 | {summary['grade_b']} |",
        f"| C 级预演 | {summary['grade_c']} |",
        f"| 资料不足 | {summary['insufficient']} |",
        f"| 候选买点 | {summary['candidate_buy_point']} |",
        f"| 等待 | {summary['wait']} |",
        f"| 回避 | {summary['avoid']} |",
        f"| 无法判断 | {summary['undetermined']} |",
        "",
        "## 2. 逐家预演",
        "",
        "| 代码 | 名称 | 路线 | 状态 | 等级 | 现价 | 价格日期 | 悲观 | 基准 | 乐观 | 安全边际门槛价 | 预演结论 | 起约束条件 | 距门槛 | 终值占比 | 未满足条件 |",
        "|---|---|---|---|---|---:|---|---:|---:|---:|---:|---|---|---:|---:|---|",
    ]
    for row in payload["banks"]:
        values = row.get("values") or {}
        note = "；".join(row.get("unmet_conditions") or []) or "—"
        note = note.replace("|", "／")
        if len(note) > 180:
            note = note[:178] + "…"
        lines.append(
            f"| {row['code']} | {row['name']} | {row.get('route') or '—'} | {row['status']} | {row['grade']} | "
            f"{_fmt(row.get('price'))} | {row.get('price_date') or '—'} | "
            f"{_fmt(values.get('pessimistic'))} | {_fmt(values.get('base'))} | "
            f"{_fmt(values.get('optimistic'))} | {_fmt(row.get('threshold_price'))} | "
            f"{CONCLUSION_LABELS.get(row.get('conclusion'), row.get('conclusion') or '—')} | "
            f"{row.get('binding') or '—'} | {_fmt(row.get('distance'), pct=True)} | "
            f"{_fmt(row.get('terminal_share'), pct=True)} | {note} |"
        )
    lines += [
        "",
        "## 3. 股权成本 ±0.5pp 的预演结论变化",
        "",
        "| 代码 | 名称 | COE−0.5pp | 基准 | COE+0.5pp |",
        "|---|---|---|---|---|",
    ]
    for row in payload["banks"]:
        by_delta = {item["delta"]: item["conclusion"] for item in row.get("sensitivity") or []}
        lines.append(
            f"| {row['code']} | {row['name']} | "
            f"{CONCLUSION_LABELS.get(by_delta.get(-0.005), '—')} | "
            f"{CONCLUSION_LABELS.get(by_delta.get(0.0), '—')} | "
            f"{CONCLUSION_LABELS.get(by_delta.get(0.005), '—')} |"
        )
    lines += [
        "",
        "## 4. 口径与限制",
        "",
        "- 数学完全复用个股政策 v1.1 股息折现路线：股权成本 = 规范化无风险利率 + ERP，三情景同一折现率；",
        "  增长按 R7（第 1 年封顶 10%、稳态 = min(10%, 5 年与 3 年复合增速)、第 2–5 年线性过渡）；",
        "  派息率按 R10 总额口径的近 3 年平均；终值按 R1（基准与悲观新投资回报率 = 股权成本，乐观 = 近 5 年平均 ROE）；",
        "  悲观按 R8（基期 EPS × min(1, 近 5 年最低归母净利率 ÷ 基期归母净利率)）。",
        "- 银行检查按 `bank-check-v1-draft`：三项证据齐全，且核心一级 ≥ 8.5%、拨备覆盖率 ≥ 150%；",
        "  通过且最近 7 个财年至多 1 年亏损、数据完整 → B（安全边际 40%），否则 C；银行不评 A。",
        "- 结论映射同个股政策：B 且 complete，现价 ≤ 基准 × 0.6 且 ≤ 悲观 → 候选买点；现价 > 乐观 → 回避；其余等待。",
        "- 旧版预演参数文件不再被读取；本预演未另设股权成本、风险加权资产密度或增速参数。",
        "- 按政策 R1，乐观情景的新投资回报率取近 5 年平均 ROE；若该值低于股权成本，乐观终值可能低于基准，"
        "预演仍按政策原样计算并在 C 级（无法判断）中保留研究数字。",
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
