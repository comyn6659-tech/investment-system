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

POLICY_PATH = ROOT / "data" / "policies" / "equity-policy-v1.1-draft.json"
SNAPSHOT_DIR = ROOT / "data" / "policy-trial" / "snapshots"
EVIDENCE_DIR = ROOT / "data" / "policy-trial" / "evidence"
MACRO_PATH = ROOT / "data" / "policy-trial" / "macro" / "cn-10y-yield.json"
JSON_OUT = ROOT / "data" / "policy-trial" / "trial-report.json"
MD_OUT = ROOT / "docs" / "archive" / "phase4a-equity-policy-trial.md"

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
    "loss_years": "亏损年份或窗口数据不足",
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


def _terminal_distance(share: float | None, limit: float) -> str:
    if share is None:
        return "—"
    gap = limit - share
    if gap >= 0:
        return f"{gap * 100:.1f} pp"
    return f"超 {-gap * 100:.1f} pp"


def _grade_distance(record: dict) -> str:
    nearest = (record.get("grade") or {}).get("nearest_condition") or {}
    if not nearest or nearest.get("distance") is None:
        return "—"
    text = f"{nearest['condition']} {nearest['distance']:.1%}"
    if nearest.get("alert"):
        return f"**临界** {text}"
    return text


def _binding_text(record: dict) -> str:
    thresholds = (record.get("trial_conclusion") or {}).get("thresholds") or {}
    if not thresholds:
        return "—"
    label = thresholds.get("binding_condition_text") or "—"
    price = thresholds.get("binding_price")
    return f"{label}（{_money(price)} 元）" if price is not None else label


def _approx_counted(record: dict) -> str:
    approximation = record.get("approximations") or {}
    if not approximation.get("items"):
        return "—"
    total = approximation.get("counted_total_per_share")
    value = (record.get("values") or {}).get("base")
    if total is None:
        return "未量化（计入项）"
    if not value:
        return f"{total:,.2f} 元"
    return f"{total:,.2f} 元（{total / value:.1%}）"


def _approx_disclosed(record: dict) -> str:
    approximation = record.get("approximations") or {}
    total = approximation.get("disclosed_total_per_share") or 0.0
    return f"{total:,.2f} 元/股"


def _value_text(key: str, value) -> str:
    if value is None:
        return "—"
    if key == "pessimistic" and value <= 0:
        return "≤0（不展示）"
    return _money(value)


def _research_row(index: int, record: dict, *, limit: float) -> str:
    values = record.get("values") or {}
    grade = (record.get("grade") or {}).get("grade", "C")
    route = record.get("route") or {}
    family = _ROUTE_LABEL.get(route.get("family"), route.get("family") or "—")
    model = _MODEL_LABEL.get(route.get("primary_model"), route.get("primary_model") or "—")
    status = record.get("status")
    reason = record.get("status_reason") or ""
    conclusion = (record.get("trial_conclusion") or {}).get("conclusion")
    conclusion_text = (
        "无法判断"
        if conclusion == "undetermined"
        else _CONCLUSION_LABEL.get(conclusion, conclusion)
    )
    if status != "complete" and reason:
        conclusion_text = f"无法判断（{reason[:40]}…）" if len(reason) > 40 else f"无法判断（{reason}）"
    return (
        f"| {index} | {record['code']} | {record.get('name') or '—'} | {family} / {model} | "
        f"{_STATUS_LABEL.get(status, status)} | {_value_text('pessimistic', values.get('pessimistic'))} | "
        f"{_money(values.get('base'))} | {_money(values.get('optimistic'))} | "
        f"{_money(record.get('price'))} | {_pct((record.get('terminal_share') or {}).get('base'), 1)} | "
        f"{_approx_counted(record)} | {grade} | {_grade_distance(record)} | — | — | "
        f"{conclusion_text} | 仅研究参考，不用于结论 |"
    )


def _usable_row(index: int, record: dict, *, limit: float) -> str:
    values = record.get("values") or {}
    grade = (record.get("grade") or {}).get("grade", "C")
    margin = (record.get("grade") or {}).get("margin_of_safety")
    route = record.get("route") or {}
    family = _ROUTE_LABEL.get(route.get("family"), route.get("family") or "—")
    model = _MODEL_LABEL.get(route.get("primary_model"), route.get("primary_model") or "—")
    conclusion = (record.get("trial_conclusion") or {}).get("conclusion")
    return (
        f"| {index} | {record['code']} | {record.get('name') or '—'} | {family} / {model} | "
        f"{_STATUS_LABEL.get(record.get('status'), record.get('status'))} | "
        f"{_value_text('pessimistic', values.get('pessimistic'))} | {_money(values.get('base'))} | "
        f"{_money(values.get('optimistic'))} | {_money(record.get('price'))} | "
        f"{_pct((record.get('terminal_share') or {}).get('base'), 1)} | "
        f"{_approx_counted(record)} | {grade} | {_grade_distance(record)} | "
        f"{_binding_text(record)} | {_pct(margin, 0) if margin is not None else '—'} | "
        f"**{_CONCLUSION_LABEL.get(conclusion, conclusion)}** | 可用于预演结论 |"
    )


def _comparison_reason(current: dict, previous: dict) -> str:
    """按 R1–R14 的机械差异说明变化来源（不是因果证明，便于逐只复核）。"""
    bits: list[str] = []
    prev_inputs = previous.get("inputs") or {}
    cur_inputs = current.get("inputs") or {}
    prev_discount = previous.get("discount") or {}
    cur_discount = current.get("discount") or {}
    prev_codes = previous.get("reason_codes") or []
    cur_codes = current.get("reason_codes") or []

    if previous.get("status") != current.get("status"):
        bits.append(
            f"状态 {_STATUS_LABEL.get(previous.get('status'), previous.get('status'))}→"
            f"{_STATUS_LABEL.get(current.get('status'), current.get('status'))}"
        )

    # R3：期末现金口径
    prev_cash = prev_inputs.get("cash_equivalents_end")
    cur_cash = cur_inputs.get("cash_equivalents_end")
    if prev_cash and cur_cash and abs(cur_cash / prev_cash - 1.0) > 0.001:
        bits.append(f"R3 期末现金 {prev_cash / 1e8:,.0f}→{cur_cash / 1e8:,.0f} 亿")

    # R10：派息率总额口径
    prev_payout = prev_inputs.get("payout")
    cur_payout = cur_inputs.get("payout")
    if prev_payout is not None and cur_payout is not None and abs(cur_payout - prev_payout) > 0.02:
        bits.append(f"R10 派息率 {prev_payout:.1%}→{cur_payout:.1%}")

    # 路线变化
    prev_model = (previous.get("route") or {}).get("primary_model")
    cur_model = (current.get("route") or {}).get("primary_model")
    if prev_model != cur_model:
        bits.append(
            f"路线 {_MODEL_LABEL.get(prev_model, prev_model)}→{_MODEL_LABEL.get(cur_model, cur_model)}"
            "（R6/R10 改变主模型选择）"
        )

    # R9：WACC 与净债务权重
    prev_wacc = prev_discount.get("wacc")
    cur_wacc = cur_discount.get("wacc")
    if prev_wacc is not None and cur_wacc is not None and abs(cur_wacc - prev_wacc) > 0.001:
        bits.append(f"R9 WACC {prev_wacc:.2%}→{cur_wacc:.2%}")

    # R7：增速路径
    prev_growth = previous.get("growth") or {}
    cur_growth = current.get("growth") or {}
    prev_steady = prev_growth.get("steady_growth")
    cur_steady = cur_growth.get("steady_growth")
    if prev_steady is not None and cur_steady is not None and abs(cur_steady - prev_steady) > 0.005:
        bits.append(f"R7 稳态增速 {prev_steady:.1%}→{cur_steady:.1%}")
    prev_y1 = prev_growth.get("year1_growth") or prev_inputs.get("year1_growth")
    cur_y1 = cur_growth.get("year1_growth") or cur_inputs.get("year1_growth")
    if prev_y1 is not None and cur_y1 is not None and abs(cur_y1 - prev_y1) > 0.005:
        bits.append(f"R7 第 1 年增速 {prev_y1:.1%}→{cur_y1:.1%}")

    # R1/R2：终值口径与护栏
    prev_ts = (previous.get("terminal_share") or {}).get("base")
    cur_ts = (current.get("terminal_share") or {}).get("base")
    if prev_ts is not None and cur_ts is not None and abs(cur_ts - prev_ts) > 0.02:
        bits.append(f"R1 终值占比 {prev_ts:.1%}→{cur_ts:.1%}")
    if any(code.startswith("TERMINAL_GUARDRAIL") for code in cur_codes):
        bits.append("R2 触发终值连续性护栏")
    if "BASE_YEAR_ANOMALY" in cur_codes:
        bits.append("R11 触发基期异常")
    if "LEVERAGE_GUARDRAIL_EXCEEDED" in cur_codes:
        bits.append("R9 触发杠杆护栏")
    if "PESSIMISTIC_VALUE_NONPOSITIVE" in cur_codes:
        bits.append("R8 悲观价值非正")
    if "BRIDGE_RECONCILIATION_GAP" in cur_codes and "BRIDGE_RECONCILIATION_GAP" not in prev_codes:
        bits.append("R6 股息路线也执行现金流勾稽")

    # 等级与结论
    prev_grade = (previous.get("grade") or {}).get("grade")
    cur_grade = (current.get("grade") or {}).get("grade")
    if prev_grade != cur_grade:
        bits.append(f"等级 {prev_grade}→{cur_grade}")
        if not any(bit.startswith("R") for bit in bits):
            bits.append("（R4/R5/R13 近似项方向、少数股东与缺数据规则）")
    prev_vals = previous.get("values") or {}
    cur_vals = current.get("values") or {}
    if prev_vals.get("base") and cur_vals.get("base"):
        change = cur_vals["base"] / prev_vals["base"] - 1.0
        bits.append(f"基准价值 {change:+.1%}")
    prev_con = (previous.get("trial_conclusion") or {}).get("conclusion")
    cur_con = (current.get("trial_conclusion") or {}).get("conclusion")
    if prev_con != cur_con:
        bits.append(
            f"结论 {_CONCLUSION_LABEL.get(prev_con, prev_con)}→{_CONCLUSION_LABEL.get(cur_con, cur_con)}"
        )
    if not bits:
        bits.append("无实质变化")
    return "；".join(bits)


def render_markdown(payload: dict) -> str:
    generated = payload["generated_at"]
    policy = payload["policy"]
    rf = payload["risk_free"]
    records = payload["records"]
    distribution = payload["distribution"]
    ke = payload.get("cost_of_equity") or (rf["normalized"] + 0.0514)
    reporting = payload.get("reporting") or {}
    limit = float(reporting.get("terminal_share_max") or 0.9)
    previous = payload.get("previous") or {}

    usable = [
        r
        for r in records
        if r.get("status") == "complete"
        and (r.get("grade") or {}).get("grade") in ("A", "B")
    ]
    research = [r for r in records if r not in usable]

    lines: list[str] = [
        "# 个股政策 v1.1 草案试运行报告（第四阶段 4a-R）",
        "",
        f"生成时间：{generated}｜生成脚本：`tools/run_equity_policy_trial.py`",
        f"政策：`{policy['policy_id']}`（{policy['policy_digest']}，状态 `{policy['status']}`，"
        f"approved={str(policy['approved']).lower()}）",
        f"修订来源：{policy.get('revision_note') or '—'}",
        "",
        "读法（重要）：",
        "",
        "- 这是**未批准政策**的试运行：结论只写在「预演结论」字段，不是正式结论，也不构成投资建议。",
        "- CLI 输出仍为 `research` / `undetermined`；本报告不改变任何对外结论。",
        "- **A/B/C 衡量的是数据完整度与模型稳定性，不是公司质量。** 等级高不等于公司好，"
        "等级低也不等于公司差。",
        "- **「回避」指现价高于乐观情景价值**，是价格判断，不是对公司的负面判断。",
        "- 30 只标的是工程测试集，不是推荐列表；候选买点数量不作为验收条件。",
        "- 资料不足、路线不支持的标的按事实记为「无法判断」，没有用其他数据凑。",
        "",
        "## 1. 政策版本与折现率",
        "",
        "| 项目 | 取值 | 来源/规则 |",
        "|---|---|---|",
        f"| 政策 | `{policy['policy_id']}` | 设计方《Fable 审查意见的判断 + 个股政策 v1.1 草案修订 + 施工单 4a-R》 |",
        f"| 无风险利率 | {rf['normalized_pct']:.4f}% | max(最新 {rf['latest']['pct']:.4f}%（{rf['latest']['date']}），"
        f"近 10 年月末均值 {rf['month_end_mean']['pct']:.4f}%（{rf['month_end_mean']['start']}→{rf['month_end_mean']['end']}，"
        f"{rf['month_end_mean']['months']} 个月）)，取{('最新值' if rf['chosen_because'] == 'latest' else '均值')} |",
        "| 股权风险溢价 | 5.14% | Damodaran 中国总股权风险溢价（2026-01），版本化保存 |",
        "| 公司特定溢价 | 0% | 政策取消，不由实现方添加 |",
        f"| 股权成本 | {ke:.4%} | 无风险利率 + 股权风险溢价 |",
        f"| 无风险利率序列 | `{rf['series_id']}` | 中债 10 年期国债到期收益率（AKShare bond_zh_us_rate） |",
        "",
        "政策文件：`data/policies/equity-policy-v1.1-draft.json`（每个参数均带来源或理由；"
        "代码不另设业务数字）。v1 文件保留在 `data/policies/equity-policy-v1-draft.json`，未删除。",
        "",
        "### 1.1 无风险利率的预期漂移（R12、审查 F5a）",
        "",
        "规则只取「最新值」与「120 个月均值」的较高者。即使收益率不变，均值也会因 2016–2018 年"
        "的高值滚出而下降，规范化值随之每年下移；价值因此每年自动上移几个百分点。这是披露项，"
        "不改变规则。",
        "",
        "| 时点 | 120 个月均值 | 规范化值 | 取值来源 |",
        "|---|---|---|---|",
        f"| 当前 | {rf['month_end_mean']['pct']:.4f}% | {rf['normalized_pct']:.4f}% | "
        f"{('最新值' if rf['chosen_because'] == 'latest' else '120 个月均值')} |",
    ]
    for row in payload.get("risk_free_drift") or []:
        lines.append(
            f"| +{row['months']} 个月 | {row['month_end_mean_pct']:.4f}% | {row['normalized_pct']:.4f}% | "
            f"{('最新值' if row['chosen_because'] == 'latest' else '120 个月均值')} |"
        )
    lines += [
        "",
        "## 2. 测试集与数据",
        "",
        f"固定测试集 {payload['test_set_size']} 只。财务快照取自合并报表（利润表/资产负债表/现金流量表），"
        "目标 10 个完整财年；价格为已收盘不复权收盘价。",
        "",
        "**R3 现金口径**：期末现金余额只用 `END_CCE`（现金流量表期末现金及现金等价物余额）。"
        "东方财富 `END_CASH_EQUIVALENTS` 是「现金等价物」分项，已改名为 `cash_equivalents_component`，"
        "不参与估值。30 只快照已按此重建；只有贵州茅台该分项有正值（84.7 亿元），修正后"
        "「非现金等价物货币资金」为 0。",
        "",
        "**R11 重述**：000858 五粮液的 2025 年一季报/中报/三季报在 2025 年年报中被前期差错更正，"
        "快照记录的是更正后数值，已标记 `restated=true`，可获得时间改为更正公告日 2026-04-30；"
        "2025 年三季报原始披露的累计营业收入 609.45 亿元已写入该事实的备注。",
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
    lines += [
        "",
        f"**数据覆盖**：{'、'.join(coverage_bits) if coverage_bits else '无可用年报'}；不足 10 个财年："
        f"{'、'.join(short_history) if short_history else '无'}。"
        f"报表未列示个别有息负债科目（按零处理）的标的 {missing_debt_total} 只，"
        "已在各自记录中列出科目名。",
        "",
    ]
    gap_records = [
        (record["code"], (record.get("inputs") or {}).get("loss_missing_10") or [])
        for record in records
        if (record.get("inputs") or {}).get("loss_missing_10")
    ]
    if gap_records:
        lines.append(
            "**R13 窗口缺口**：以下标的最近 10 年亏损窗口中缺年报，A 级"
            "「无亏损」条件按不通过处理（B 级 7 年窗口另行判断）："
            + "；".join(f"{code}（缺 {'、'.join(str(y) for y in years)}）" for code, years in gap_records)
            + "。"
        )
        lines.append("")
    lines += [
        "数据来源：东方财富合并报表；分红：巨潮资讯；价格：新浪财经不复权日线"
        "（东方财富 push2 被限流时的回退路径，快照内已记录来源）；公司资料：巨潮资讯股本变动与行业分类。",
        "",
        "## 3. 预演结果",
        "",
        "### 3.1 可给预演结论的标的（状态完整且等级 A/B）",
        "",
        "| # | 代码 | 名称 | 路线 | 状态 | V 悲观 | V 基准 | V 乐观 | 现价 | 终值占比 | 计入等级的近似影响 | 等级 | 距最近门槛 | 起约束条件 | 安全边际 | 预演结论 | 价值用途 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for index, record in enumerate(usable, start=1):
        lines.append(_usable_row(index, record, limit=limit))
    if not usable:
        lines.append("| — | — | 本次没有标的达到 A/B 级 | — | — | — | — | — | — | — | — | — | — | — | — | — | — |")

    lines += [
        "",
        "### 3.2 仅研究参考、不用于结论的标的",
        "",
        "C 级、partial、资料不足、路线不支持的行，价值一栏只作研究参考，**不用于任何结论**；"
        "与上表分表展示（R12）。",
        "",
        "| # | 代码 | 名称 | 路线 | 状态 | V 悲观 | V 基准 | V 乐观 | 现价 | 终值占比 | 计入等级的近似影响 | 等级 | 距最近门槛 | 起约束条件 | 安全边际 | 预演结论 | 价值用途 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for index, record in enumerate(research, start=1):
        lines.append(_research_row(index, record, limit=limit))
    lines += [
        "",
        "### 3.3 终值占比与距 90% 上限的距离（全部标的，R2/R12）",
        "",
        "R2 的三项连续性护栏：主模型按 `*_by_default` 落入；基准情景显式期 FCFF 合计 ≤ 0；"
        "基准情景终值占比 ≥ 90%。任一项触发即状态 partial、无法判断。",
        "",
        "| # | 代码 | 名称 | 基准终值占比 | 距 90% | 悲观终值占比 | 乐观终值占比 | 触发护栏 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for index, record in enumerate(records, start=1):
        terminal_share = record.get("terminal_share") or {}
        codes = record.get("reason_codes") or []
        hits = [
            code
            for code in codes
            if code
            in (
                "TERMINAL_GUARDRAIL_BY_DEFAULT",
                "TERMINAL_GUARDRAIL_EXPLICIT_CASH",
                "TERMINAL_GUARDRAIL_TERMINAL_SHARE",
            )
        ]
        lines.append(
            f"| {index} | {record['code']} | {record.get('name') or '—'} | "
            f"{_pct(terminal_share.get('base'), 1)} | {_terminal_distance(terminal_share.get('base'), limit)} | "
            f"{_pct(terminal_share.get('pessimistic'), 1)} | {_pct(terminal_share.get('optimistic'), 1)} | "
            f"{('、'.join(hits) if hits else '—')} |"
        )

    lines += [
        "",
        "### 3.4 折现率与增长输入",
        "",
        "| # | 代码 | 股权成本 | WACC | 净有息负债 | 债务权重 | 债务成本（显示） | 派息率 | 终值派息率（基准） | g 基准 | 第 1 年增速 | 稳态增速 | 3 年 CAGR | 5 年 CAGR |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for index, record in enumerate(records, start=1):
        discount = record.get("discount") or {}
        inputs = record.get("inputs") or {}
        growth = record.get("growth") or {}
        terminal = (record.get("terminal") or {}).get("base") or {}
        if not discount:
            lines.append(f"| {index} | {record['code']} | — | — | — | — | — | — | — | — | — | — | — | — |")
            continue
        lines.append(
            f"| {index} | {record['code']} | {_pct(discount.get('cost_of_equity'))} | "
            f"{_pct(discount.get('wacc'))} | {_money((discount.get('net_debt') or 0) / 1e8, 0)} 亿 | "
            f"{_pct(discount.get('debt_weight'), 1)} | {discount.get('cost_of_debt_display') or '—'} | "
            f"{_pct(inputs.get('payout'), 1)} | {_pct(terminal.get('terminal_payout_rate'), 1)} | "
            f"{_pct(terminal.get('growth'))} | {_pct(growth.get('year1_growth'))} | "
            f"{_pct(growth.get('steady_growth'))} | {_pct(inputs.get('revenue_cagr3'))} | "
            f"{_pct(inputs.get('revenue_cagr5'))} |"
        )

    nwc_missing_codes = [
        record["code"] for record in records if (record.get("inputs") or {}).get("nwc_missing")
    ]
    interest_missing_codes = [
        record["code"] for record in records if (record.get("inputs") or {}).get("interest_missing")
    ]
    lines += [
        "",
        f"**R13 补充披露**：营运资本强度不可用、按 0 处理："
        f"{'、'.join(nwc_missing_codes) if nwc_missing_codes else '无'}；"
        f"利息费用缺失、债务成本取下限："
        f"{'、'.join(interest_missing_codes) if interest_missing_codes else '无'}。",
        "",
        "### 3.5 折现率 ±0.5pp 与终值增长 ±0.5pp 敏感性（基准情景每股价值，元）",
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

    # R9：杠杆护栏 30/40/50% 三档触发
    leverage_rows = []
    for threshold in (0.3, 0.4, 0.5):
        codes = [
            record["code"]
            for record in records
            if (record.get("leverage_guardrail") or {}).get("ratio") is not None
            and (record["leverage_guardrail"]["ratio"] > threshold)
        ]
        leverage_rows.append((threshold, codes))
    lines += [
        "",
        "### 3.6 杠杆护栏 30% / 40% / 50% 三档触发（R9）",
        "",
        "| 阈值 | 触发标的（净有息负债 ÷（净有息负债 + 权益市值）> 阈值） |",
        "|---|---|",
    ]
    for threshold, codes in leverage_rows:
        lines.append(f"| {threshold:.0%} | {'、'.join(codes) if codes else '无'} |")

    # R4：近似项明细（方向与每股金额）
    lines += [
        "",
        "### 3.7 近似项明细（R4：方向与每股金额）",
        "",
        "「计入等级」只含可能让价值变低的项；「非现金等价物货币资金」只披露、不计入，"
        "但仍从可抵扣现金中扣除（保守）。",
        "",
        "| 代码 | 计入等级（元/股） | 只披露（元/股） | 逐项方向 |",
        "|---|---|---|---|",
    ]
    for record in records:
        approximation = record.get("approximations") or {}
        items = approximation.get("items") or []
        if not items:
            continue
        counted = approximation.get("counted_total_per_share")
        disclosed = approximation.get("disclosed_total_per_share")
        detail = "；".join(
            f"{item['label']}={item['direction']}"
            for item in items
            if item.get("quantified")
        )
        lines.append(
            f"| {record['code']} | {_money(counted)} | {_money(disclosed)} | {detail or '—'} |"
        )

    g_changes = [
        (abs(row["change_pct"]), record["code"])
        for record in records
        if (record.get("route") or {}).get("primary_model") == "equity_dcf_fcff"
        for row in (record.get("sensitivity") or [])
        if row.get("variable") == "terminal_growth" and row.get("change_pct") is not None
    ]
    def _has_terminal_guardrail(code: str) -> bool:
        record = next(item for item in records if item["code"] == code)
        return any(
            str(code_).startswith("TERMINAL_GUARDRAIL")
            for code_ in (record.get("reason_codes") or [])
        )

    valid_g = [item for item in g_changes if not _has_terminal_guardrail(item[1])]
    max_g = max(valid_g, default=None)
    max_all = max(g_changes, default=(None, None))
    lines += [
        "",
        f"说明（R1 之后按实际数字写）：FCFF 主模型的基准情景新投资回报率取 WACC，与折现率同口径，"
        f"终值化简为「末期 NOPAT × (1+g) ÷ WACC」，g 在公式中相互抵消。"
        f"本次未触发 R2 的 FCFF 标的，终值增长 ±0.5pp 对基准价值的最大变动为 "
        f"{_pct(max_g[0]) if max_g else '—'}"
        f"{f'（{max_g[1]}）' if max_g else ''}（验收上限 1%，N4）；"
        f"全部 FCFF 标的最大变动为 {_pct(max_all[0]) if max_all[0] is not None else '—'}"
        f"{f'（{max_all[1]}，终值占比过高已触发 R2、价值不用于结论）' if max_all[1] else ''}。"
        "股息路线的新投资回报率仍取股权成本，g 不抵消，敏感性表按实际值列出。",
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
        "### 4.2 基期异常变动清单（R11：触发即 partial / 无法判断）",
        "",
    ]
    thresholds = payload.get("reporting_thresholds") or {}
    anomaly_rows = []
    for record in records:
        if "BASE_YEAR_ANOMALY" in (record.get("reason_codes") or []):
            anomaly_rows.append(record)
    if anomaly_rows:
        lines += [
            f"触发条件：基期营业收入同比绝对值 > {float(thresholds.get('base_year_revenue_yoy_abs') or 0):.0%}，"
            f"或基期归母净利润同比绝对值 > {float(thresholds.get('base_year_profit_yoy_abs') or 0):.0%}。"
            "触发后状态 partial、预演结论无法判断；清单继续披露（R11）。",
            "",
            "| 代码 | 名称 | 财年 | 营业收入（亿元） | 上年（亿元） | 营收同比 | 归母净利润（亿元） | 上年（亿元） | 净利润同比 | 预演结论 |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for record in anomaly_rows:
            change = record.get("base_year_change") or {}
            conclusion = (record.get("trial_conclusion") or {}).get("conclusion")
            lines.append(
                f"| {record['code']} | {record.get('name') or '—'} | {change.get('fiscal_year')} | "
                f"{_money(change.get('revenue') / 1e8 if change.get('revenue') is not None else None)} | "
                f"{_money(change.get('prior_revenue') / 1e8 if change.get('prior_revenue') is not None else None)} | "
                f"{_pct(change.get('revenue_yoy'), 1)} | "
                f"{_money(change.get('net_profit_parent') / 1e8 if change.get('net_profit_parent') is not None else None)} | "
                f"{_money(change.get('prior_net_profit_parent') / 1e8 if change.get('prior_net_profit_parent') is not None else None)} | "
                f"{_pct(change.get('profit_yoy'), 1)} | {_CONCLUSION_LABEL.get(conclusion, conclusion)} |"
            )
        lines.append("")
    else:
        lines += ["没有标的触发基期异常变动阈值。", ""]

    lines += [
        "## 5. 关键标的与未满足条件",
        "",
    ]
    graded = [r for r in usable]
    if graded:
        lines.append("达到 A/B 级（可给预演结论）的标的：")
        lines.append("")
        for record in graded:
            grade = record["grade"]
            conclusion = record["trial_conclusion"]
            failed = "、".join(
                _CONDITION_LABEL.get(name, name) for name in grade.get("unmet_conditions") or []
            ) or "无"
            thresholds = conclusion.get("thresholds") or {}
            lines.append(
                f"- **{record['code']} {record.get('name')}**：{grade['grade']} 级，"
                f"安全边际 {grade['margin_of_safety']:.0%}，预演结论「{_CONCLUSION_LABEL.get(conclusion.get('conclusion'))}」；"
                f"起约束条件：{_binding_text(record)}；候选买点门槛 "
                f"{_money(thresholds.get('candidate_entry_price'))} 元，"
                f"现价 {_money(record.get('price'))} 元；A 级未满足条件：{failed}。"
            )
        lines.append("")
    else:
        lines.append("本次没有标的达到 A/B 级：全部为 C 级，按政策一律「无法判断」。")
        lines.append("")

    lines += ["### 5.1 审查发现对应的四个关键标的", ""]
    maotai = next((r for r in records if r["code"] == "600519"), None)
    if maotai and maotai.get("values"):
        inputs = maotai.get("inputs") or {}
        lines.append(
            f"- **600519 贵州茅台（F2a / R3）**：期末现金取 `END_CCE` "
            f"{_money((inputs.get('cash_equivalents_end') or 0) / 1e8, 1)} 亿元；"
            f"「非现金等价物货币资金」{_money((inputs.get('non_cash_equivalents') or 0) / 1e8, 1)} 亿元"
            f"（每股 {_approx_disclosed(maotai)}，只披露、不计入等级）；"
            f"等级 {maotai['grade']['grade']}，基准价值 {_money((maotai['values'] or {}).get('base'))} 元，"
            f"候选买点门槛 {_money(((maotai.get('trial_conclusion') or {}).get('thresholds') or {}).get('candidate_entry_price'))} 元。"
            f"未满足条件：{'、'.join(_CONDITION_LABEL.get(x, x) for x in maotai['grade'].get('unmet_conditions') or []) or '无'}。"
        )
    wanhua = next((r for r in records if r["code"] == "600309"), None)
    if wanhua:
        detail = ((wanhua.get("scenario_detail") or {}).get("base") or {})
        lines.append(
            f"- **600309 万华化学（F1 / R1、R2）**：状态 {_STATUS_LABEL.get(wanhua.get('status'), wanhua.get('status'))}，"
            f"等级 {wanhua['grade']['grade']}；基准价值 {_money((wanhua.get('values') or {}).get('base'))} 元，"
            f"基准终值占比 {_pct((wanhua.get('terminal_share') or {}).get('base'), 1)}；"
            f"基准显式期 FCFF 合计 {_money((detail.get('explicit_fcff_sum') or 0) / 1e8, 1)} 亿元；"
            f"原因码：{'、'.join(wanhua.get('reason_codes') or []) or '—'}。"
        )
    wuliangye = next((r for r in records if r["code"] == "000858"), None)
    if wuliangye:
        change = wuliangye.get("base_year_change") or {}
        lines.append(
            f"- **000858 五粮液（F7 / R11）**：2025 年营业收入同比 {_pct(change.get('revenue_yoy'), 1)}、"
            f"归母净利润同比 {_pct(change.get('profit_yoy'), 1)}，触发基期异常；"
            f"状态 partial、预演结论无法判断；快照 2025 年中期数据已标 `restated=true`，"
            f"可获得时间改为 2026-04-30。"
        )
    jianzhu = next((r for r in records if r["code"] == "601668"), None)
    if jianzhu and jianzhu.get("discount"):
        discount = jianzhu["discount"]
        lines.append(
            f"- **601668 中国建筑（F5 / R9）**：净有息负债 {_money((discount.get('net_debt') or 0) / 1e8, 0)} 亿元，"
            f"净债务权重 {_pct(discount.get('debt_weight'), 1)}，WACC {_pct(discount.get('wacc'))}；"
            f"杠杆护栏 {_pct((jianzhu.get('leverage_guardrail') or {}).get('limit'), 0)} 下占比 "
            f"{_pct((jianzhu.get('leverage_guardrail') or {}).get('ratio'), 1)}，状态 partial、等级 C、无法判断。"
        )
    lines.append("")

    lines += ["### 5.2 600519 两模型分歧（R1/R6 之后）", ""]
    if maotai and maotai.get("cross_check"):
        cross = maotai["cross_check"]
        lines += [
            f"- 主模型 FCFF：{_money((maotai.get('values') or {}).get('base'))} 元/股；"
            f"交叉检查股息折现：{_money(cross.get('value_per_share'))} 元/股；"
            f"分歧 {_pct(cross.get('divergence'))}。",
            "- R1 让 FCFF 终值的新投资回报率改用 WACC，终值再投资率 = g ÷ WACC，"
            "终值化简为末期 NOPAT ÷ WACC；"
            "R6 让股息路线也执行现金流勾稽并加 3 年分红覆盖条件。两条路线的剩余差异来自口径："
            "FCFF 计全部可分配现金流，股息路线只计实际派息。",
            "",
        ]
    else:
        lines += ["600519 未产生可比较的两模型结果。", ""]

    lines += ["## 6. 候选买点出现条件（R12 / F9）", ""]
    table = payload.get("candidate_multiple_table") or []
    growths = [float(x) for x in (reporting.get("candidate_growth_grid") or [])]
    lines += [
        "全权益公司、基准情景、5 年显式期；p = 显式期 FCFF ÷ NOPAT（近似可分配比例）。"
        "终值按 R1 取新投资回报率 = 股权成本，再投资率 = g / Ke，因此终值 = 末期 NOPAT × (1+g) / Ke。"
        "未分配的留存现金不计入价值（保守）。",
        "",
        "基准价值 ÷ 基期每股收益（倍）：",
        "",
        "| 显式期可分配比例 p | " + " | ".join(f"增速 {g:+.0%}" for g in growths) + " |",
        "|---|" + "---|" * len(growths),
    ]
    for row in table:
        cells = " | ".join(
            f"{row['multiples'][g]:.1f}" if row["multiples"].get(g) is not None else "—"
            for g in growths
        )
        lines.append(f"| {row['payout']:.1f} | {cells} |")
    ranges = payload.get("candidate_multiple_ranges") or {}
    if ranges:
        lines += [
            "",
            f"对应的候选买点市盈率区间（倍数 × (1 − 安全边际)）：A 级约 "
            f"{ranges['A']['pe_low']:.1f}–{ranges['A']['pe_high']:.1f} 倍，B 级约 "
            f"{ranges['B']['pe_low']:.1f}–{ranges['B']['pe_high']:.1f} 倍；"
            f"乐观价值（回避线）约 {ranges['min_multiple']:.1f}–{ranges['max_multiple']:.1f} 倍。"
            "这是这套参数的正常结果，不是 bug；**是否接受这种严格度，是决策②要股东决定的第一件事。**",
            "",
        ]
    if graded:
        lines += [
            "本次 A/B 级标的的候选门槛对应基期市盈率与现价所需跌幅：",
            "",
            "| 代码 | 名称 | 等级 | 基期每股收益 | 现价 PE | 候选门槛 PE | 现价需再跌 |",
            "|---|---|---|---|---|---|---|",
        ]
        for record in graded:
            inputs = record.get("inputs") or {}
            profit = inputs.get("net_profit_parent")
            shares = record.get("shares") or 0
            price = record.get("price")
            entry = ((record.get("trial_conclusion") or {}).get("thresholds") or {}).get(
                "candidate_entry_price"
            )
            eps = profit / shares if profit and shares else None
            if not eps or not price or entry is None:
                continue
            lines.append(
                f"| {record['code']} | {record.get('name') or '—'} | {record['grade']['grade']} | "
                f"{eps:.2f} | {price / eps:.1f} | {entry / eps:.1f} | "
                f"{(1 - entry / price) * 100:.0f}% |"
            )
        lines.append("")

    lines += [
        "## 7. v1 → v1.1 逐只对照",
        "",
        "v1 数据来自修订前的试运行结果（`data/policy-trial/trial-report-v1.json`，未覆盖）；"
        "v1.1 为本次重跑。变化原因由状态、等级、基准价值、结论与原因码的机械差异生成，"
        "具体条款见第三节与政策文件的 R1–R14。",
        "",
        "| # | 代码 | 名称 | 状态 v1→v1.1 | 等级 v1→v1.1 | V 悲观 v1→v1.1 | V 基准 v1→v1.1 | V 乐观 v1→v1.1 | 结论 v1→v1.1 | 变化原因 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for index, record in enumerate(records, start=1):
        prev = previous.get(record["code"]) or {}
        pv = prev.get("values") or {}
        cv = record.get("values") or {}
        pcon = (prev.get("trial_conclusion") or {}).get("conclusion")
        ccon = (record.get("trial_conclusion") or {}).get("conclusion")
        lines.append(
            f"| {index} | {record['code']} | {record.get('name') or '—'} | "
            f"{_STATUS_LABEL.get(prev.get('status'), prev.get('status') or '—')}→"
            f"{_STATUS_LABEL.get(record.get('status'), record.get('status'))} | "
            f"{(prev.get('grade') or {}).get('grade') or '—'}→{(record.get('grade') or {}).get('grade')} | "
            f"{_value_text('pessimistic', pv.get('pessimistic'))}→{_value_text('pessimistic', cv.get('pessimistic'))} | "
            f"{_money(pv.get('base'))}→{_money(cv.get('base'))} | "
            f"{_money(pv.get('optimistic'))}→{_money(cv.get('optimistic'))} | "
            f"{_CONCLUSION_LABEL.get(pcon, pcon or '—')}→{_CONCLUSION_LABEL.get(ccon, ccon)} | "
            f"{_comparison_reason(record, prev)} |"
        )

    lines += [
        "",
        "## 8. 设计决定与已知局限",
        "",
    ]
    for decision in payload.get("designer_decisions") or []:
        lines.append(
            f"- **{decision.get('item')}**（{decision.get('status')}）：{decision.get('decision')}"
        )
    lines += [
        "",
        "已知局限（写进决策②材料，不在本版修改）：",
        "",
        "- 同一股权成本适用于所有公司，不区分风险高低；公用事业、电信与高波动公司用同一把尺。",
        "- 股权风险溢价（Damodaran 中国总溢价，美元口径发布）与规范化人民币无风险利率的搭配口径，"
        "审查认为有道理但不确定；本版不改。",
        "- 只能前向验证，不能历史回测；候选买点是否出现取决于未来价格。",
        "- 有息负债只含短期借款、长期借款、应付债券、一年内到期的非流动负债、租赁负债；"
        "短期融资券、永续债未计入（F12），本版只披露、未改口径。",
        "- 股本无生效日（F11）：除权日前后抓取时，不复权价格与股本可能错配；本批快照日未发现错配。",
        "",
        "## 9. 复现与产物",
        "",
        "```bash",
        "cd /Users/comyn/Documents/投资/投资分析系统/investment-system",
        ".venv/bin/python tools/rebuild_policy_trial_snapshots.py          # R3/R11 重建快照（不联网）",
        ".venv/bin/python tools/run_equity_policy_trial.py                # 生成本报告与 JSON",
        "PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider",
        "```",
        "",
        "| 产物 | 路径 |",
        "|---|---|",
        "| 政策文件 v1.1 | `data/policies/equity-policy-v1.1-draft.json` |",
        "| 政策文件 v1（保留） | `data/policies/equity-policy-v1-draft.json` |",
        "| 试运行机器可读结果 | `data/policy-trial/trial-report.json` |",
        "| v1 试运行结果（对照用） | `data/policy-trial/trial-report-v1.json` |",
        "| 30 只快照 | `data/policy-trial/snapshots/`（manifest 见同目录 `manifest.json`） |",
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
    previous_path = ROOT / "data" / "policy-trial" / "trial-report-v1.json"
    if previous_path.exists():
        previous_payload = json.loads(previous_path.read_text(encoding="utf-8"))
        payload["previous"] = {
            record["code"]: {
                key: record.get(key)
                for key in (
                    "status",
                    "grade",
                    "values",
                    "trial_conclusion",
                    "inputs",
                    "discount",
                    "growth",
                    "terminal_share",
                    "route",
                    "reason_codes",
                    "approximations",
                    "base_year_change",
                )
            }
            for record in previous_payload.get("records") or []
        }

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
