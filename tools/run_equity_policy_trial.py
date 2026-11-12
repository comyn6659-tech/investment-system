#!/usr/bin/env python
"""个股政策 v1 草案试运行：跑 30 只固定测试集，生成报告与机器可读结果。

范围（第四阶段第一批 A）：
- 只写试运行报告的「预演结论」字段；CLI、model_status、decision 全部不变；
- 30 只测试集全部有结果（资料不足/路线不支持也如实记录）；
- 报告包含：每只标的的路线、状态、三档价值、近似项影响、确定性等级与未满足条件、
  所需安全边际、预演结论、折现率与终值增长 ±0.5pp 敏感性；全集分布；
  安全边际 20/25/30% 与 35/40/45% 的敏感性；600519 两模型分歧。

用法：
    .venv/bin/python tools/run_equity_policy_trial.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.policy.equity import load_policy  # noqa: E402
from engine.policy.trial import run_trial  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")

POLICY_PATH = ROOT / "data" / "policies" / "equity-policy-v1-draft.json"
SNAPSHOT_DIR = ROOT / "data" / "policy-trial" / "snapshots"
EVIDENCE_DIR = ROOT / "data" / "policy-trial" / "evidence"
MACRO_PATH = ROOT / "data" / "policy-trial" / "macro" / "cn-10y-yield.json"
JSON_OUT = ROOT / "data" / "policy-trial" / "trial-report.json"
MD_OUT = ROOT / "docs" / "phase4a-equity-policy-trial.md"

CODES = (
    "600519", "000858", "600887", "603288", "000895", "600436", "000333",
    "000651", "600660", "600031", "600309", "002415", "300750", "002594",
    "600276", "600900", "601088", "601006", "600941", "601668", "601919",
    "600019", "601899", "601857", "000002", "601012", "600036", "601398",
    "601318", "600030",
)

_ROUTE_LABEL = {
    "non_financial": "非金融",
    "bank": "银行",
    "insurance": "保险",
    "broker": "券商",
    "cyclical": "明显周期",
    "distressed": "困境",
    "holding": "控股",
    "unknown": "未判定",
}

_MODEL_LABEL = {
    "equity_dcf_fcff": "FCFF",
    "equity_dividend_discount": "股息折现",
    "bank_dividend_discount": "银行股息",
    None: "—",
}

_STATUS_LABEL = {
    "complete": "完整",
    "partial": "部分完成",
    "insufficient_data": "资料不足",
    "unsupported": "路线不支持",
}

_CONCLUSION_LABEL = {
    "candidate_buy_point": "候选买点",
    "wait": "等待",
    "avoid": "回避",
    "undetermined": "无法判断",
}

_CONDITION_LABEL = {
    "available_fiscal_years": "可用财年不足",
    "loss_years": "有亏损年份",
    "ocf_to_profit": "现金流/利润不足",
    "net_debt_to_ebitda": "净负债/EBITDA 超限",
    "cross_check_divergence": "交叉检查分歧超限",
    "approximation_impact": "近似项影响超限",
    "bank_risk_evidence": "银行风险证据缺失",
    "route_not_evaluable": "路线不可评估",
}


def _pct(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{digits}f}%"


def _money(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def _num(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def _unmet(record: dict) -> str:
    unmet = (record.get("grade") or {}).get("unmet_conditions") or []
    if not unmet:
        return "—"
    labels = [_CONDITION_LABEL.get(name, name) for name in unmet]
    if len(labels) <= 2:
        return "、".join(labels)
    return "、".join(labels[:2]) + f" 等 {len(labels)} 项"


def _approx(record: dict) -> str:
    approximation = record.get("approximations") or {}
    if approximation.get("unquantified"):
        return "未量化（" + "、".join(approximation["unquantified"]) + "）"
    total = approximation.get("total_impact_per_share")
    value = (record.get("values") or {}).get("base")
    if total is None or not value:
        return "—"
    return f"{total:,.2f} 元（{total / value:.1%}）"


def _sensitivity_lookup(record: dict) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for row in record.get("sensitivity") or []:
        key = f"{row.get('variable')}:{row.get('delta')}"
        out[key] = row.get("value_per_share")
    return out


def render_markdown(payload: dict) -> str:
    generated = payload["generated_at"]
    policy = payload["policy"]
    rf = payload["risk_free"]
    records = payload["records"]
    distribution = payload["distribution"]

    lines: list[str] = [
        "# 个股政策 v1 草案试运行报告（第四阶段第一批 A）",
        "",
        f"生成时间：{generated}｜生成脚本：`tools/run_equity_policy_trial.py`",
        f"政策：`{policy['policy_id']}`（{policy['policy_digest']}，状态 `{policy['status']}`，"
        f"approved={str(policy['approved']).lower()}）",
        "",
        "读法（重要）：",
        "",
        "- 这是**未批准政策**的试运行：结论只写在「预演结论」字段，不是正式结论，也不构成投资建议。",
        "- CLI 输出仍为 `research` / `undetermined`；本报告不改变任何对外结论。",
        "- 30 只标的是工程测试集，不是推荐列表；候选买点数量不作为验收条件。",
        "- 资料不足、路线不支持的标的按事实记为「无法判断」，没有用其他数据凑。",
        "",
        "## 1. 政策版本与折现率",
        "",
        "| 项目 | 取值 | 来源/规则 |",
        "|---|---|---|",
        f"| 政策 | `{policy['policy_id']}` | 设计方草案（2026-11-12）第一部分，本报告只按原文实现 |",
        f"| 无风险利率 | {rf['normalized_pct']:.4f}% | max(最新 {rf['latest']['pct']:.4f}%（{rf['latest']['date']}），"
        f"近 10 年月末均值 {rf['month_end_mean']['pct']:.4f}%（{rf['month_end_mean']['start']}→{rf['month_end_mean']['end']}，"
        f"{rf['month_end_mean']['months']} 个月）)，取{('最新值' if rf['chosen_because'] == 'latest' else '均值')} |",
        f"| 股权风险溢价 | 5.14% | Damodaran 中国总股权风险溢价（2026-01），版本化保存 |",
        "| 公司特定溢价 | 0% | 政策取消，不由实现方添加 |",
        f"| 股权成本 | {rf['normalized_pct'] / 100 + 0.0514:.4%} | 无风险利率 + 股权风险溢价 |",
        f"| 无风险利率序列 | `{rf['series_id']}` | 中债 10 年期国债到期收益率（AKShare bond_zh_us_rate），"
        "观测值与来源见 `data/policy-trial/macro/cn-10y-yield.json` |",
        "",
        "政策文件：`data/policies/equity-policy-v1-draft.json`（每个参数均带来源或理由；"
        "代码不另设业务数字）。",
        "",
        "## 2. 测试集与数据",
        "",
        f"固定测试集 {payload['test_set_size']} 只。财务快照取自合并报表（利润表/资产负债表/现金流量表），"
        "目标 10 个完整财年（本次实际 11 个）；价格为已收盘不复权收盘价。",
        "",
        "| # | 代码 | 名称 | 行业（线索） | 年报数 | 快照 | 快照日 | 状态 |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for index, record in enumerate(records, start=1):
        inputs = record.get("inputs") or {}
        snapshot_years = (record.get("snapshot") or {}).get("annual_years") or []
        years = len(snapshot_years) if snapshot_years else inputs.get("available_years")
        lines.append(
            f"| {index} | {record['code']} | {record.get('name') or '—'} | "
            f"{record.get('industry') or '—'} | {years if years is not None else '—'} | "
            f"`{record.get('snapshot_id') or '无'}` | {record.get('as_of') or '—'} | "
            f"{_STATUS_LABEL.get(record.get('status'), record.get('status'))} |"
        )

    year_counts: dict[int, int] = {}
    short_history: list[str] = []
    missing_debt_total = 0
    for record in records:
        snapshot = record.get("snapshot") or {}
        years = snapshot.get("annual_years") or []
        if years:
            year_counts[len(years)] = year_counts.get(len(years), 0) + 1
            if len(years) < 10:
                short_history.append(f"{record['code']}（{len(years)} 年）")
        missing = (record.get("inputs") or {}).get("missing_debt_components") or []
        if missing:
            missing_debt_total += 1

    coverage_bits = [
        f"{count} 个财年 {number} 只" for count, number in sorted(year_counts.items(), reverse=True)
    ]
    coverage_text = "、".join(coverage_bits) if coverage_bits else "无可用年报"
    lines += [
        "",
        f"**数据覆盖**：{coverage_text}；不足 10 个财年："
        f"{'、'.join(short_history) if short_history else '无'}。"
        f"报表未列示个别有息负债科目（按零处理）的标的 {missing_debt_total} 只，"
        "已在各自记录中列出科目名。",
        "",
        "数据来源：东方财富合并报表（`stock_profit_sheet_by_report_em` / "
        "`stock_balance_sheet_by_report_em` / `stock_cash_flow_sheet_by_report_em`）；"
        "分红：巨潮资讯；价格：新浪财经不复权日线（东方财富 push2 被限流时的回退路径，"
        "快照内已记录来源）；公司资料：巨潮资讯股本变动与行业分类（东方财富个股信息不可用时的回退）。"
        "逐只来源与缺失见 `data/policy-trial/snapshots/` 与 `data/policy-trial/evidence/`。",
        "",
        "## 3. 预演结果（每只标的三档价值与结论）",
        "",
        "| # | 代码 | 名称 | 路线 | 状态 | V 悲观 | V 基准 | V 乐观 | 现价 | 近似影响 | 等级 | A 级未满足条件 | 安全边际 | 预演结论 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    for index, record in enumerate(records, start=1):
        values = record.get("values") or {}
        grade = (record.get("grade") or {}).get("grade", "C")
        margin = (record.get("grade") or {}).get("margin_of_safety")
        conclusion = (record.get("trial_conclusion") or {}).get("conclusion")
        route = record.get("route") or {}
        family = _ROUTE_LABEL.get(route.get("family"), route.get("family") or "—")
        model = _MODEL_LABEL.get(route.get("primary_model"), route.get("primary_model") or "—")
        lines.append(
            f"| {index} | {record['code']} | {record.get('name') or '—'} | {family} / {model} | "
            f"{_STATUS_LABEL.get(record.get('status'), record.get('status'))} | "
            f"{_money(values.get('pessimistic'))} | {_money(values.get('base'))} | "
            f"{_money(values.get('optimistic'))} | {_money(record.get('price'))} | "
            f"{_approx(record)} | {grade} | {_unmet(record)} | "
            f"{_pct(margin, 0) if margin is not None else '—'} | "
            f"**{_CONCLUSION_LABEL.get(conclusion, conclusion)}** |"
        )

    lines += [
        "",
        "### 3.1 折现率与增长输入",
        "",
        "| # | 代码 | 股权成本 | WACC | 三年平均派息率 | 终值派息率（基准） | g 基准 | 第 1 年增速 | 第 2–5 年增速 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for index, record in enumerate(records, start=1):
        discount = record.get("discount") or {}
        inputs = record.get("inputs") or {}
        growth = record.get("growth") or {}
        terminal = (record.get("terminal") or {}).get("base") or {}
        if not discount:
            lines.append(f"| {index} | {record['code']} | — | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {index} | {record['code']} | {_pct(discount.get('cost_of_equity'))} | "
            f"{_pct(discount.get('wacc'))} | {_pct(inputs.get('payout'), 1)} | "
            f"{_pct(terminal.get('terminal_payout_rate'), 1)} | {_pct(terminal.get('growth'))} | "
            f"{_pct(growth.get('year1_growth'))} | {_pct(growth.get('steady_growth'))} |"
        )

    lines += [
        "",
        "### 3.2 折现率 ±0.5pp 与终值增长 ±0.5pp 敏感性（基准情景每股价值，元）",
        "",
        "| # | 代码 | 基准 | 折现率 −0.5pp | 折现率 +0.5pp | 终值增长 −0.5pp | 终值增长 +0.5pp |",
        "|---|---|---|---|---|---|---|",
    ]
    for index, record in enumerate(records, start=1):
        values = record.get("values") or {}
        lookup = _sensitivity_lookup(record)
        if not values:
            lines.append(f"| {index} | {record['code']} | — | — | — | — | — |")
            continue
        lines.append(
            f"| {index} | {record['code']} | {_money(values.get('base'))} | "
            f"{_money(lookup.get('discount_rate:-0.005'))} | {_money(lookup.get('discount_rate:0.005'))} | "
            f"{_money(lookup.get('terminal_growth:-0.005'))} | "
            f"{_money(lookup.get('terminal_growth:0.005'))} |"
        )

    lines += [
        "",
        "说明：基准情景的新投资回报率取股权成本，此时终值等于「末期 NOPAT（或 EPS）÷ 折现率」，"
        "终值增长 g 在公式中相互抵消，因此 g ±0.5pp 对基准价值几乎无影响；"
        "g 的实际影响体现在乐观情景（新投资回报率取近 5 年平均 ROE）与显式预测期增速上。",
        "",
        "## 4. 全集分布",
        "",
        "| 维度 | 分布 |",
        "|---|---|",
        f"| 预演结论 | 候选买点 {distribution['conclusions']['candidate_buy_point']}、"
        f"等待 {distribution['conclusions']['wait']}、回避 {distribution['conclusions']['avoid']}、"
        f"无法判断 {distribution['conclusions']['undetermined']} |",
        f"| 确定性等级 | A {distribution['grades']['A']}、B {distribution['grades']['B']}、C {distribution['grades']['C']} |",
        "| 状态 | " + "、".join(f"{key} {value}" for key, value in distribution["status"].items()) + " |",
        "| 企业族 | " + "、".join(
            f"{_ROUTE_LABEL.get(key, key)} {value}" for key, value in distribution["families"].items()
        ) + " |",
        "",
        "### 4.1 安全边际敏感性（20/25/30% 与 35/40/45%）",
        "",
        "| 安全边际 | 候选买点 | 等待 | 回避 | 无法判断 |",
        "|---|---|---|---|---|",
    ]
    for row in payload["margin_sensitivity"]:
        codes = "、".join(row.get("candidate_codes") or [])
        lines.append(
            f"| {row['margin_of_safety']:.0%} | {row['candidate_buy_point']}"
            f"{f'（{codes}）' if codes else ''} | {row['wait']} | "
            f"{row['avoid']} | {row['undetermined']} |"
        )

    lines += [
        "",
        "说明：安全边际只改变「候选买点」的价格门槛；无法判断的数量不随安全边际变化。"
        "上表对全部 A/B 级标的使用同一安全边际做敏感性，不等于政策按等级给出的 25%/40%。",
        "",
        "### 4.2 基期异常变动清单",
        "",
    ]

    thresholds = payload.get("reporting_thresholds") or {}
    anomaly_rows = []
    for record in records:
        change = record.get("base_year_change") or {}
        revenue_yoy = change.get("revenue_yoy")
        profit_yoy = change.get("profit_yoy")
        revenue_hit = (
            revenue_yoy is not None
            and abs(revenue_yoy) > float(thresholds.get("base_year_revenue_yoy_abs") or 1.0)
        )
        profit_hit = (
            profit_yoy is not None
            and abs(profit_yoy) > float(thresholds.get("base_year_profit_yoy_abs") or 1.0)
        )
        if revenue_hit or profit_hit:
            anomaly_rows.append((record, change, revenue_hit, profit_hit))
    if anomaly_rows:
        lines += [
            f"触发条件：基期营业收入同比绝对值 > {float(thresholds.get('base_year_revenue_yoy_abs') or 0):.0%}，"
            f"或基期归母净利润同比绝对值 > {float(thresholds.get('base_year_profit_yoy_abs') or 0):.0%}。"
            "清单只做披露，不改任何结论。",
            "",
            "| 代码 | 名称 | 财年 | 营业收入（亿元） | 上年营业收入（亿元） | 营收同比 | 归母净利润（亿元） | 上年（亿元） | 净利润同比 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for record, change, _revenue_hit, _profit_hit in anomaly_rows:
            revenue = change.get("revenue")
            prior_revenue = change.get("prior_revenue")
            profit = change.get("net_profit_parent")
            prior_profit = change.get("prior_net_profit_parent")
            lines.append(
                f"| {record['code']} | {record.get('name') or '—'} | {change.get('fiscal_year')} | "
                f"{_money(revenue / 1e8 if revenue is not None else None)} | "
                f"{_money(prior_revenue / 1e8 if prior_revenue is not None else None)} | "
                f"{_pct(change.get('revenue_yoy'), 1)} | "
                f"{_money(profit / 1e8 if profit is not None else None)} | "
                f"{_money(prior_profit / 1e8 if prior_profit is not None else None)} | "
                f"{_pct(change.get('profit_yoy'), 1)} |"
            )
        lines.append("")
    else:
        lines += ["没有标的触发基期异常变动阈值。", ""]

    lines += [
        "## 5. 关键标的与未满足条件",
        "",
    ]

    graded = [r for r in records if (r.get("grade") or {}).get("grade") in ("A", "B")]
    if graded:
        lines.append("达到 A/B 级（可给预演结论）的标的：")
        lines.append("")
        for record in graded:
            grade = record["grade"]
            conclusion = record["trial_conclusion"]
            failed = "、".join(
                _CONDITION_LABEL.get(name, name) for name in grade.get("unmet_conditions") or []
            ) or "无"
            lines.append(
                f"- **{record['code']} {record.get('name')}**：{grade['grade']} 级，"
                f"安全边际 {grade['margin_of_safety']:.0%}，预演结论「{_CONCLUSION_LABEL.get(conclusion.get('conclusion'))}」；"
                f"候选买点门槛 {_money((conclusion.get('thresholds') or {}).get('candidate_entry_price'))} 元"
                f"（还须不高于悲观价值 {_money((conclusion.get('thresholds') or {}).get('pessimistic_value'))} 元），"
                f"现价 {_money(record.get('price'))} 元；A 级未满足条件：{failed}。"
            )
        lines.append("")
    else:
        lines.append("本次没有标的达到 A/B 级：全部为 C 级，按政策一律「无法判断」。")
        lines.append("")

    c_records = [r for r in records if (r.get("grade") or {}).get("grade") == "C"]
    if c_records:
        lines.append("C 级标的的未满足条件（按 A 级门槛统计，取前两项）：")
        lines.append("")
        for record in c_records:
            reason = _unmet(record)
            status = record.get("status")
            note = record.get("status_reason") if status != "complete" else ""
            lines.append(
                f"- {record['code']} {record.get('name') or '—'}：{reason}"
                + (f"（{note}）" if note else "")
            )
        lines.append("")

    # 5.1 关键异常：高杠杆企业的 WACC 机械结果（用记录里的数字，不写死）
    leverage = next((r for r in records if r["code"] == "601668"), None)
    if leverage and leverage.get("discount"):
        discount = leverage["discount"]
        base_value = (leverage.get("values") or {}).get("base")
        lines += [
            "### 5.1 关键异常与口径说明",
            "",
            f"- **601668 中国建筑**：账面有息负债 {discount.get('debt', 0) / 1e8:,.0f} 亿元、"
            f"权益市值 {discount.get('market_cap', 0) / 1e8:,.0f} 亿元，债务权重 "
            f"{discount.get('debt_weight', 0):.1%}，WACC {discount.get('wacc', 0):.2%}；"
            f"在 g = 2.0% 下 FCFF 基准价值 {_money(base_value)} 元/股（现价 {_money(leverage.get('price'))} 元）。"
            "这不是数据错误：有息负债、货币资金与净利润均来自合并报表；这是政策"
            "「债务权重用账面值、债务成本下限为无风险利率」的机械结果。"
            "按 M4 新增的杠杆护栏（账面有息负债占比 > 50%），该标的占比超过上限，"
            "状态按 partial、等级 C、预演结论无法判断；研究价值仅供研究参考，不用于结论。"
            "政策护栏与 WACC 算法本身本批均未改动。",
            "",
        ]

    lines += ["## 6. 600519 两模型分歧（政策 §2 终值再投资一致性的效果）", ""]
    maotai = next((r for r in records if r["code"] == "600519"), None)
    if maotai and maotai.get("cross_check"):
        cross = maotai["cross_check"]
        base = (maotai.get("values") or {}).get("base")
        lines += [
            f"- 主模型 FCFF：{_money(base)} 元/股；交叉检查股息折现：{_money(cross.get('value_per_share'))} 元/股；"
            f"分歧 {_pct(cross.get('divergence'))}。",
            "- 第二阶段同一标的的两模型分歧约 24%，主要来自终值增长与留存率不一致；"
            "本政策要求终值再投资率 = g ÷ 新投资回报率，两个模型的终值派息率都被同一条规则约束，"
            "因此分歧缩小。",
            "- 剩余分歧来自两条路线的显式期口径不同：FCFF 用 NOPAT + 折旧摊销 − 资本开支 − 营运资本增加，"
            "股息路线只把「EPS × 派息率」当作可分配现金流；近 3 年平均派息率低于 1，"
            "这部分留存现金在股息路线里没有被计价。这不是需要取平均的误差，而是两条路线的定义差异。",
            "",
        ]
    else:
        lines += ["600519 未产生可比较的两模型结果。", ""]

    lines += [
        "## 7. 设计决定与待确认问题（2026-11-12 验收复函）",
        "",
        "试运行提出的 6 个问题已由设计方逐条决定；本批按决定执行，并把高杠杆护栏写入政策：",
        "",
    ]
    for decision in payload.get("designer_decisions") or []:
        lines.append(
            f"- **{decision.get('item')}**（{decision.get('status')}）：{decision.get('decision')}"
        )
    if payload.get("open_questions"):
        lines += ["", "仍未解决的问题：", ""]
        for question in payload["open_questions"]:
            lines.append(f"- {question}")
    lines += [
        "",
        "## 8. 复现与产物",
        "",
        "```bash",
        "cd /Users/comyn/Documents/deepseek-harness/投资/investment-system",
        ".venv/bin/python tools/build_policy_trial_data.py --as-of 2026-11-11   # 采集 30 只快照 + 宏观序列",
        ".venv/bin/python tools/run_equity_policy_trial.py                     # 生成本报告与 JSON",
        "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider",
        "```",
        "",
        "| 产物 | 路径 |",
        "|---|---|",
        "| 政策文件 | `data/policies/equity-policy-v1-draft.json` |",
        "| 试运行机器可读结果 | `data/policy-trial/trial-report.json` |",
        "| 30 只快照 | `data/policy-trial/snapshots/`（manifest 见同目录 `manifest.json`） |",
        "| 业务证据 | `data/policy-trial/evidence/` |",
        "| 无风险利率序列 | `data/policy-trial/macro/cn-10y-yield.json` |",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="个股政策 v1 草案试运行")
    parser.add_argument("--only", default=None, help="只跑指定代码，逗号分隔（调试用）")
    args = parser.parse_args(argv)

    now = datetime.now(TZ)
    policy = load_policy(POLICY_PATH)
    codes = tuple(x.strip() for x in args.only.split(",")) if args.only else CODES
    payload = run_trial(
        policy,
        snapshot_dir=SNAPSHOT_DIR,
        evidence_dir=EVIDENCE_DIR,
        macro_path=MACRO_PATH,
        codes=codes,
        generated_at=now.isoformat(timespec="seconds"),
    )
    payload["open_questions"] = list(policy.data.get("open_questions_for_designer") or [])

    JSON_OUT.parent.mkdir(parents=True, exist_ok=True)
    JSON_OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.only is None:
        MD_OUT.write_text(render_markdown(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "json": str(JSON_OUT.relative_to(ROOT)),
                "markdown": str(MD_OUT.relative_to(ROOT)) if args.only is None else None,
                "records": len(payload["records"]),
                "distribution": payload["distribution"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
