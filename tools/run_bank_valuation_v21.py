#!/usr/bin/env python
"""银行专用估值 v2.1 试运行：只写 v2.1 报告，不写正式账本、不改变 CLI 银行结论。

输出：
- `data/bank-pool/valuation-v2.1.json`
- `docs/phase4l-bank-valuation-v2.1.md`
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
V2 = BANK_DIR / "valuation-v2.json"
V1 = BANK_DIR / "valuation-rehearsal.json"
BANK_REPORT = BANK_DIR / "report.json"
EQUITY_POLICY = ROOT / "data" / "policies" / "equity-policy-v1.1.json"
POLICY_FILE = ROOT / "data" / "policies" / "bank-valuation-v2.1-draft.json"
OUT_JSON = BANK_DIR / "valuation-v2.1.json"
OUT_MD = ROOT / "docs" / "phase4l-bank-valuation-v2.1.md"

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
    policy = load_policy(POLICY_FILE)
    equity_policy = load_equity_policy(EQUITY_POLICY)
    series = Path(equity_policy.node("discount_rate.risk_free")["series_file"])
    if not series.is_absolute():
        series = ROOT / series
    macro = load_macro_series(series)
    pool_codes = {
        str(row["code"]).zfill(6)
        for row in json.loads(BANK_REPORT.read_text(encoding="utf-8"))["in_pool"]
    }
    raw_rows = read_table(INPUTS)
    rows = [row for row in raw_rows if str(row.get("in_scope")) == "True"]
    inputs = [
        BankValuationInputs.from_row(row, in_bank_pool=str(row["code"]).zfill(6) in pool_codes)
        for row in rows
    ]
    base = [value_bank(item, policy, equity_policy, macro) for item in inputs]
    base_premium = float(policy["parameters"]["cost_of_equity"]["bank_premium"])
    base_margin = float(policy["parameters"]["margin_of_safety"]["value"])

    def variant(premium: float | None, margin: float | None) -> list[dict]:
        return [
            value_bank(item, policy, equity_policy, macro, premium=premium, margin=margin)
            for item in inputs
        ]

    premiums = policy["parameters"]["cost_of_equity"]["premium_sensitivity"]
    margins = policy["parameters"]["margin_of_safety"]["sensitivity"]
    matrix = []
    for premium in premiums:
        row_cells = []
        for margin in margins:
            results = variant(float(premium), float(margin))
            candidates = [item for item in results if item["conclusion"] == "candidate_buy_point"]
            row_cells.append(
                {
                    "bank_premium": float(premium),
                    "margin_of_safety": float(margin),
                    "candidate_count": len(candidates),
                    "candidates": [f"{item['code']} {item['name']}" for item in candidates],
                }
            )
        matrix.append(row_cells)

    counts = {key: sum(1 for item in base if item["conclusion"] == key) for key in CONCLUSION}
    values_distribution = {}
    for scenario in ("pessimistic", "base", "optimistic"):
        values = [item["values"][scenario] for item in base if item["values"].get(scenario) is not None]
        values_distribution[scenario] = {
            "min": min(values) if values else None,
            "median": statistics.median(values) if values else None,
            "max": max(values) if values else None,
        }

    v2 = json.loads(V2.read_text(encoding="utf-8"))
    v2_by_code = {str(item["code"]).zfill(6): item for item in v2.get("banks", [])}
    comparison_v2 = []
    for item in base:
        old = v2_by_code.get(item["code"])
        if old is None:
            continue
        comparison_v2.append(
            {
                "code": item["code"],
                "name": item["name"],
                "v2_roe": old.get("roe"),
                "v21_roe": item["roe"],
                "v2_base": (old.get("values") or {}).get("base"),
                "v21_base": item["values"].get("base"),
                "v2_conclusion": old.get("conclusion"),
                "v21_conclusion": item["conclusion"],
                "reason": "v2.1 基准 ROE 纳入最新财年、乐观 ROE 改近 3 年平均，悲观 ROE 增加近 3 年下滑外推；因此三档 ROE 与价值随之变化。",
            }
        )

    v1 = json.loads(V1.read_text(encoding="utf-8"))
    v1_by_code = {str(item["code"]).zfill(6): item for item in v1.get("banks", [])}
    comparison_v1 = []
    for item in base:
        old = v1_by_code.get(item["code"])
        if old is None:
            continue
        old_value = (old.get("values") or {}).get("base")
        diff = (
            (item["values"]["base"] - old_value) / old_value
            if item["values"].get("base") is not None and old_value not in (None, 0)
            else None
        )
        comparison_v1.append(
            {
                "code": item["code"],
                "name": item["name"],
                "v1_base": old_value,
                "v21_base": item["values"].get("base"),
                "diff_pct": diff,
                "v1_conclusion": old.get("conclusion"),
                "v21_conclusion": item["conclusion"],
                "v1_terminal_share": old.get("terminal_share"),
            }
        )

    history = read_table(BANK_DIR / "valuation-v2" / "inputs.csv.gz")
    history_by_code = {}
    for code in {str(row["code"]).zfill(6) for row in history}:
        row = next((item for item in history if str(item["code"]).zfill(6) == code), {})
        history_by_code[code] = {
            "code": code,
            "name": row.get("name"),
            "status": row.get("history_status"),
            "months": int(float(row["history_months"])) if row.get("history_months") not in (None, "") else 0,
            "pb_percentile": _num(row.get("pb_percentile")),
            "dy_percentile": _num(row.get("dy_percentile")),
        }

    generated_at = datetime.now(TZ).isoformat(timespec="seconds")
    meta = {
        "as_of": datetime.now(TZ).date().isoformat(),
        "generated_at": generated_at,
        "policy": policy["policy_id"],
        "policy_status": policy["status"],
        "policy_approved": False,
        "policy_sha256": _digest(POLICY_FILE),
        "baseline_v2": str(V2.relative_to(ROOT)),
        "baseline_v2_sha256": _digest(V2),
        "equity_policy": equity_policy.policy_id,
        "risk_free": base[0]["risk_free"] if base else None,
        "equity_risk_premium": base[0]["equity_risk_premium"] if base else None,
        "cost_of_equity": base[0]["cost_of_equity"] if base else None,
        "inputs": str(INPUTS.relative_to(ROOT)),
        "warning": "银行估值 v2.1 为未批准草案，只做试运行；不写正式账本，不改变 CLI 银行结论。",
    }
    payload = {
        "meta": meta,
        "summary": {
            "total": len(base),
            **counts,
            "values_distribution": values_distribution,
        },
        "banks": base,
        "matrix": matrix,
        "comparison_v2": comparison_v2,
        "comparison_v1": comparison_v1,
        "history": list(history_by_code.values()),
        "history_summary": {
            "total": len(history_by_code),
            "ok": sum(1 for item in history_by_code.values() if item["status"] == "ok"),
            "insufficient": sum(1 for item in history_by_code.values() if item["status"] != "ok"),
        },
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# 银行专用估值 v2.1 试运行（未批准草案）",
        "",
        f"生成：{generated_at}｜政策：`{meta['policy']}`（{meta['policy_status']}）｜政策哈希：`{meta['policy_sha256'][:16]}`",
        "",
        "> 只做试运行，不写正式结论账本、不改变 CLI 银行结论。v2.1 相对 v2 修订三档 ROE 规则，"
        "并新增描述性“隐含长期回报率”。参数与依据见 `data/policies/bank-valuation-v2.1-draft.json`。",
        "",
        "## 1. 方法与参数",
        "",
        "- 公式：每股价值 = 最新每股净资产 × (ROE − g) ÷ (k − g)。",
        f"- 规范化无风险利率：{_pct(meta['risk_free'], 4)}；股权风险溢价：{_pct(meta['equity_risk_premium'], 4)}；"
        f"银行溢价：{_pct(base_premium, 1)}；k = {_pct(meta['cost_of_equity'], 4)}。",
        "- 基准 ROE = min(最新财年 ROE, 近 3 年平均, 近 5 年平均)；"
        "悲观 ROE = min(近 5 年最低 − 1pp, 最新财年 − 3 × 近 3 年年均下滑幅度)，未下滑时后一项不适用；"
        "乐观 ROE = 近 3 年平均；资产质量扣减规则不变。",
        "- g：基准 2.0%、悲观 1.5%、乐观 2.5%，且不超过无风险利率；安全边际 30%。",
        "- 隐含长期回报率 = 2% + (最新财年 ROE − 2%) ÷ 现价市净率；只描述，不参与结论。",
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
        "| 代码 | 名称 | 三档 ROE（悲观/基准/乐观） | 下滑外推 | 扣减 | k | g（悲观/基准/乐观） | 每股净资产 | 三档价值（悲观/基准/乐观） | 现价 | 合理 PB（基准） | 门槛价 | 预演结论 | 起约束条件 | 隐含长期回报 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in base:
        decline = (
            f"{_pct(item['decline_extrapolated'])}"
            if item["decline_extrapolated"] is not None
            else "不适用"
        )
        implicit = (
            f"{_pct(item['implicit_return'])}（{'高于' if item['implicit_return_vs_k'] >= 0 else '低于'} k）"
            if item["implicit_return"] is not None
            else "—"
        )
        lines.append(
            "| {code} | {name} | {pess}/{base}/{opt} | {decline} | {deduct} | {k} | {gp}/{gb}/{go} | {bvps} | "
            "{vp}/{vb}/{vo} | {price} | {pb} | {threshold} | {conclusion} | {binding} | {implicit} |".format(
                code=item["code"],
                name=item["name"],
                pess=_pct(item["roe"]["pessimistic"]),
                base=_pct(item["roe"]["base"]),
                opt=_pct(item["roe"]["optimistic"]),
                decline=decline,
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
                implicit=implicit,
            )
        )
        if item["reasons"]:
            lines.append("")
            lines.append(f"> {item['code']} 无法判断原因：" + "；".join(item["reasons"]))
            lines.append("")
    lines += [
        "## 4. 敏感性：k 溢价 × 安全边际（3×3）",
        "",
        "每格为“候选买点数量：名单”。k 溢价 1.0/2.0/3.0 个百分点；安全边际 20/30/40%。",
        "",
        "| k 溢价 \\ 安全边际 | 20% | 30% | 40% |",
        "|---|---|---|---|",
    ]
    for row in matrix:
        cells = []
        for cell in row:
            cells.append(
                f"{cell['candidate_count']}：{'、'.join(cell['candidates']) or '无'}"
            )
        lines.append(f"| {_pct(row[0]['bank_premium'], 1)} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## 5. v2 → v2.1 逐家对照",
        "",
        "| 代码 | 名称 | v2 ROE（悲观/基准/乐观） | v2.1 ROE（悲观/基准/乐观） | v2 基准价值 | v2.1 基准价值 | v2 结论 | v2.1 结论 | 变化原因 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for item in comparison_v2:
        old_roe = item["v2_roe"] or {}
        new_roe = item["v21_roe"]
        lines.append(
            f"| {item['code']} | {item['name']} | "
            f"{_pct(old_roe.get('pessimistic'))}/{_pct(old_roe.get('base'))}/{_pct(old_roe.get('optimistic'))} | "
            f"{_pct(new_roe['pessimistic'])}/{_pct(new_roe['base'])}/{_pct(new_roe['optimistic'])} | "
            f"{_money(item['v2_base'])} | {_money(item['v21_base'])} | "
            f"{CONCLUSION.get(item['v2_conclusion'], item['v2_conclusion'])} | "
            f"{CONCLUSION.get(item['v21_conclusion'], item['v21_conclusion'])} | {item['reason']} |"
        )
    lines += [
        "",
        "## 6. 与第八批通用股息折现对照（v2.1）",
        "",
        "| 代码 | 名称 | v1 基准价值 | v2.1 基准价值 | 差异 | v1 结论 | v2.1 结论 | v1 终值占比 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in comparison_v1:
        diff_text = "—" if item["diff_pct"] is None else f"{item['diff_pct']:.1%}"
        terminal = "—" if item["v1_terminal_share"] is None else f"{item['v1_terminal_share']:.1%}"
        lines.append(
            f"| {item['code']} | {item['name']} | {_money(item['v1_base'])} | {_money(item['v21_base'])} | "
            f"{diff_text} | {CONCLUSION.get(item['v1_conclusion'], item['v1_conclusion'])} | "
            f"{CONCLUSION.get(item['v21_conclusion'], item['v21_conclusion'])} | {terminal} |"
        )
    lines += [
        "",
        "## 7. 银行历史估值位置（描述性，沿用 v2 数据）",
        "",
        f"42 家中可用 {payload['history_summary']['ok']} 家；不足 5 年 "
        f"{payload['history_summary']['insufficient']} 家；只描述相对自身历史位置，不给买点。",
        "",
        "## 8. 限制与边界",
        "",
        "- v2.1 仍是未批准草案；正式 CLI 银行结论仍为「无法判断：银行估值未启用」。",
        "- 隐含长期回报率是描述性指标，不参与结论，也不代表预测收益。",
        "- 不写正式账本，不改银行池、个股政策、三池与账本旧记录。",
        "- 完整结构化数据见 `data/bank-pool/valuation-v2.1.json`；v2 基线保留在 `valuation-v2.json`。",
        "",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"summary": payload["summary"], "history": payload["history_summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
