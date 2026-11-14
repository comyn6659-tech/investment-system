#!/usr/bin/env python
"""银行专用估值 v2 试运行：只写报告，不写正式账本、不改变 CLI 银行结论。

输出：
- `data/bank-pool/valuation-v2.json`
- `docs/phase4l-bank-valuation-v2.md`
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.bank.valuation_v2 import (  # noqa: E402
    BankValuationInputs,
    load_policy,
    value_bank,
)
from engine.policy.equity import load_policy as load_equity_policy  # noqa: E402
from engine.policy.valuation import load_macro_series  # noqa: E402
from engine.quality.tables import read_table  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
BANK_DIR = ROOT / "data" / "bank-pool"
INPUTS = BANK_DIR / "valuation-v2" / "inputs.csv.gz"
HISTORY = BANK_DIR / "valuation-v2" / "history.csv.gz"
V1 = BANK_DIR / "valuation-rehearsal.json"
BANK_REPORT = BANK_DIR / "report.json"
EQUITY_POLICY = ROOT / "data" / "policies" / "equity-policy-v1.1.json"
OUT_JSON = BANK_DIR / "valuation-v2.json"
OUT_MD = ROOT / "docs" / "phase4l-bank-valuation-v2.md"

CONCLUSION = {
    "candidate_buy_point": "候选买点",
    "wait": "等待",
    "avoid": "回避",
    "undetermined": "无法判断",
}
BINDING = {"margin_of_safety": "安全边际", "pessimistic_value": "悲观价值"}


def _num(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct(value, digits=2):
    return "—" if value is None else f"{value * 100:.{digits}f}%"


def _money(value, digits=2):
    return "—" if value is None else f"{value:.{digits}f}"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    policy = load_policy(ROOT / "data" / "policies" / "bank-valuation-v2-draft.json")
    equity_policy = load_equity_policy(EQUITY_POLICY)
    series = Path(equity_policy.node("discount_rate.risk_free")["series_file"])
    if not series.is_absolute():
        series = ROOT / series
    macro = load_macro_series(series)
    pool_codes = {str(row["code"]).zfill(6) for row in json.loads(BANK_REPORT.read_text(encoding="utf-8"))["in_pool"]}
    raw_rows = read_table(INPUTS)
    rows = [row for row in raw_rows if str(row.get("in_scope")) == "True"]
    inputs = [BankValuationInputs.from_row(row, in_bank_pool=str(row["code"]).zfill(6) in pool_codes) for row in rows]
    base = [value_bank(item, policy, equity_policy, macro) for item in inputs]
    by_code = {item["code"]: item for item in base}
    base_premium = float(policy["parameters"]["cost_of_equity"]["bank_premium"])
    base_margin = float(policy["parameters"]["margin_of_safety"]["value"])

    def variant(premium: float | None, margin: float | None) -> list[dict]:
        return [
            value_bank(item, policy, equity_policy, macro, premium=premium, margin=margin)
            for item in inputs
        ]

    sens_k = []
    for premium in policy["parameters"]["cost_of_equity"]["premium_sensitivity"]:
        results = variant(float(premium), base_margin)
        candidates = [item for item in results if item["conclusion"] == "candidate_buy_point"]
        sens_k.append(
            {
                "bank_premium": float(premium),
                "cost_of_equity": results[0]["cost_of_equity"] if results else None,
                "candidate_count": len(candidates),
                "candidates": [f"{item['code']} {item['name']}" for item in candidates],
            }
        )
    sens_margin = []
    for margin in policy["parameters"]["margin_of_safety"]["sensitivity"]:
        results = variant(base_premium, float(margin))
        candidates = [item for item in results if item["conclusion"] == "candidate_buy_point"]
        sens_margin.append(
            {
                "margin_of_safety": float(margin),
                "candidate_count": len(candidates),
                "candidates": [f"{item['code']} {item['name']}" for item in candidates],
            }
        )

    counts = {key: sum(1 for item in base if item["conclusion"] == key) for key in CONCLUSION}
    values_distribution = {}
    for scenario in ("pessimistic", "base", "optimistic"):
        values = [item["values"][scenario] for item in base if item["values"].get(scenario) is not None]
        values_distribution[scenario] = {
            "min": min(values) if values else None,
            "median": statistics.median(values) if values else None,
            "max": max(values) if values else None,
        }

    v1 = json.loads(V1.read_text(encoding="utf-8"))
    v1_by_code = {str(item["code"]).zfill(6): item for item in v1.get("banks", [])}
    comparison = []
    for item in base:
        previous = v1_by_code.get(item["code"])
        if previous is None:
            continue
        old_value = (previous.get("values") or {}).get("base")
        diff = (
            (item["values"]["base"] - old_value) / old_value
            if item["values"].get("base") is not None and old_value not in (None, 0)
            else None
        )
        comparison.append(
            {
                "code": item["code"],
                "name": item["name"],
                "v1_base": old_value,
                "v2_base": item["values"].get("base"),
                "diff_pct": diff,
                "v1_conclusion": previous.get("conclusion"),
                "v2_conclusion": item["conclusion"],
                "v1_terminal_share": previous.get("terminal_share"),
                "reason": (
                    "v1 为通用股息折现"
                    + (
                        f"（终值占比 {previous['terminal_share']:.1%}）"
                        if previous.get("terminal_share") is not None
                        else ""
                    )
                    + "；v2 用银行稳态合理市净率，直接以 ROE、g、k 定价，不再依赖高终值占比。"
                ),
            }
        )

    history = read_table(HISTORY)
    history_by_code = {}
    for code in {str(row["code"]).zfill(6) for row in raw_rows}:
        items = [row for row in history if str(row["code"]).zfill(6) == code]
        input_row = next((row for row in raw_rows if str(row["code"]).zfill(6) == code), {})
        history_by_code[code] = {
            "code": code,
            "name": input_row.get("name"),
            "status": input_row.get("history_status"),
            "months": int(float(input_row["history_months"])) if input_row.get("history_months") not in (None, "") else 0,
            "start": input_row.get("history_start"),
            "end": input_row.get("history_end"),
            "pb_current": _num(input_row.get("pb_current")),
            "pb_percentile": _num(input_row.get("pb_percentile")),
            "dy_current": _num(input_row.get("dy_current")),
            "dy_percentile": _num(input_row.get("dy_percentile")),
            "monthly_rows": len(items),
        }

    meta = {
        "as_of": datetime.now(TZ).date().isoformat(),
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "policy": policy["policy_id"],
        "policy_status": policy["status"],
        "policy_approved": False,
        "policy_sha256": _digest(Path("data/policies/bank-valuation-v2-draft.json").resolve()),
        "equity_policy": equity_policy.policy_id,
        "risk_free": base[0]["risk_free"] if base else None,
        "equity_risk_premium": base[0]["equity_risk_premium"] if base else None,
        "cost_of_equity": base[0]["cost_of_equity"] if base else None,
        "inputs": str(INPUTS.relative_to(ROOT)),
        "history": str(HISTORY.relative_to(ROOT)),
        "warning": "银行估值 v2 为未批准草案，只做试运行；不写正式账本，不改变 CLI 银行结论。",
    }
    payload = {
        "meta": meta,
        "summary": {
            "total": len(base),
            **counts,
            "values_distribution": values_distribution,
        },
        "banks": base,
        "sensitivity_k": sens_k,
        "sensitivity_margin": sens_margin,
        "comparison_v1": comparison,
        "history": list(history_by_code.values()),
        "history_summary": {
            "total": len(history_by_code),
            "ok": sum(1 for item in history_by_code.values() if item["status"] == "ok"),
            "insufficient": sum(1 for item in history_by_code.values() if item["status"] != "ok"),
        },
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# 银行专用估值 v2 试运行（未批准草案）",
        "",
        f"生成：{meta['generated_at']}｜政策：`{meta['policy']}`（{meta['policy_status']}）｜政策哈希：`{meta['policy_sha256'][:16]}`",
        "",
        "> 只做试运行，不写正式结论账本、不改变 CLI 银行结论。所有参数与依据见 "
        "`data/policies/bank-valuation-v2-draft.json`。",
        "",
        "## 1. 方法与参数",
        "",
        "- 公式：每股价值 = 最新每股净资产 × (ROE − g) ÷ (k − g)。",
        f"- 规范化无风险利率：{_pct(meta['risk_free'], 4)}；股权风险溢价：{_pct(meta['equity_risk_premium'], 4)}；"
        f"银行溢价：{_pct(base_premium, 1)}；k = {_pct(meta['cost_of_equity'], 4)}。",
        "- ROE 基准 = min(近 3 年平均, 近 5 年平均)；悲观 = 近 5 年最低 − 1 个百分点；乐观 = 近 5 年平均；"
        "拨备 < 200% 或不良率 5 年上升 > 0.3 个百分点时三档再减 1 个百分点。",
        "- g：基准 2.0%、悲观 1.5%、乐观 2.5%，且不超过规范化无风险利率；安全边际 30%。",
        "",
        "## 2. 结果分布",
        "",
        f"- 36 家范围内银行：候选买点 {counts['candidate_buy_point']}、等待 {counts['wait']}、"
        f"回避 {counts['avoid']}、无法判断 {counts['undetermined']}。",
        "- 三档价值分布（元/股）：" + "；".join(
            f"{label} {_money(values_distribution[key]['min'])} / {_money(values_distribution[key]['median'])} / "
            f"{_money(values_distribution[key]['max'])}（最小/中位/最大）"
            for key, label in (("pessimistic", "悲观"), ("base", "基准"), ("optimistic", "乐观"))
        ) + "。",
        "",
        "## 3. 逐家结果",
        "",
        "| 代码 | 名称 | 三档 ROE（悲观/基准/乐观） | 扣减 | k | g（悲观/基准/乐观） | 每股净资产 | 三档价值（悲观/基准/乐观） | 现价 | 合理 PB（基准） | 门槛价 | 预演结论 | 起约束条件 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in base:
        lines.append(
            "| {code} | {name} | {pess}/{base}/{opt} | {deduct} | {k} | {gp}/{gb}/{go} | {bvps} | "
            "{vp}/{vb}/{vo} | {price} | {pb} | {threshold} | {conclusion} | {binding} |".format(
                code=item["code"],
                name=item["name"],
                pess=_pct(item["roe"]["pessimistic"]),
                base=_pct(item["roe"]["base"]),
                opt=_pct(item["roe"]["optimistic"]),
                deduct="是" if item["asset_quality_deduction"] else "否",
                k=_pct(item["cost_of_equity"], 2),
                gp=_pct(item["growth"]["pessimistic"], 2),
                gb=_pct(item["growth"]["base"], 2),
                go=_pct(item["growth"]["optimistic"], 2),
                bvps=_money(item["bvps"]),
                vp=_money(item["values"]["pessimistic"]),
                vb=_money(item["values"]["base"]),
                vo=_money(item["values"]["optimistic"]),
                price=_money(item["price"]),
                pb=_money(item["multiples"]["base"], 3),
                threshold=_money(item["threshold_price"]),
                conclusion=CONCLUSION.get(item["conclusion"], item["conclusion"]),
                binding=BINDING.get(item["binding_condition"], "—"),
            )
        )
        if item["reasons"]:
            lines.append("")
            lines.append(f"> {item['code']} 无法判断原因：" + "；".join(item["reasons"]))
            lines.append("")
    lines += [
        "## 4. 敏感性",
        "",
        "### 4.1 银行股权成本溢价（k 溢价）",
        "",
        "| k 溢价 | k | 候选买点数量 | 名单 |",
        "|---|---|---|---|",
    ]
    for item in sens_k:
        lines.append(
            f"| {_pct(item['bank_premium'], 1)} | {_pct(item['cost_of_equity'], 2)} | "
            f"{item['candidate_count']} | {'、'.join(item['candidates']) or '无'} |"
        )
    lines += [
        "",
        "### 4.2 安全边际",
        "",
        "| 安全边际 | 候选买点数量 | 名单 |",
        "|---|---|---|",
    ]
    for item in sens_margin:
        lines.append(
            f"| {_pct(item['margin_of_safety'], 0)} | {item['candidate_count']} | "
            f"{'、'.join(item['candidates']) or '无'} |"
        )
    lines += [
        "",
        "## 5. 与第八批通用股息折现对照",
        "",
        "| 代码 | 名称 | v1 基准价值 | v2 基准价值 | 差异 | v1 结论 | v2 结论 | v1 终值占比 | 差异原因 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for item in comparison:
        diff_text = "—" if item["diff_pct"] is None else f"{item['diff_pct']:.1%}"
        terminal_text = (
            "—" if item["v1_terminal_share"] is None else f"{item['v1_terminal_share']:.1%}"
        )
        lines.append(
            f"| {item['code']} | {item['name']} | {_money(item['v1_base'])} | {_money(item['v2_base'])} | "
            f"{diff_text} | {CONCLUSION.get(item['v1_conclusion'], item['v1_conclusion'])} | "
            f"{CONCLUSION.get(item['v2_conclusion'], item['v2_conclusion'])} | {terminal_text} | "
            f"{item['reason']} |"
        )
    lines += [
        "",
        "## 6. 银行历史估值位置（描述性）",
        "",
        "42 家银行按东方财富同源收盘价/市净率与分红（除权除息日）计算：现价市净率、股息率在近 10 年"
        "（不足 10 年用全部可得，至少 60 个月）月末序列中的分位。只描述相对自身历史贵不贵，不给买点。",
        "",
        f"- 可用 41 家；不足 5 年 {payload['history_summary']['insufficient']} 家（{', '.join(item['code'] + ' ' + str(item['name']) for item in payload['history'] if item['status'] != 'ok')}）。",
        "",
        "| 代码 | 名称 | 可用月数 | PB 分位 | 股息率分位 | 状态 |",
        "|---|---|---|---|---|---|",
    ]
    for item in sorted(payload["history"], key=lambda row: row["code"]):
        pb_text = "—" if item["pb_percentile"] is None else f"{item['pb_percentile']:.1f}%"
        dy_text = "—" if item["dy_percentile"] is None else f"{item['dy_percentile']:.1f}%"
        lines.append(
            f"| {item['code']} | {item['name']} | {item['months']} | {pb_text} | {dy_text} | "
            f"{'可用' if item['status'] == 'ok' else '不足 5 年'} |"
        )
    lines += [
        "",
        "## 7. 限制与边界",
        "",
        "- 历史估值位置只描述相对自身历史的位置，不是买卖判断，也不参与本估值与结论。",
        "- v2 是未批准草案；正式 CLI 银行结论仍为「无法判断：银行估值未启用」。",
        "- 不写正式账本，不改银行池、个股政策、三池与账本旧记录。",
        "- 逐家差异见本报告第 5 节；完整结构化数据见 `data/bank-pool/valuation-v2.json`。",
        "",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"summary": payload["summary"], "history": payload["history_summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
