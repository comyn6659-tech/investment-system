#!/usr/bin/env python
"""把真实研究结果渲染成 Markdown 证据表（docs/phase2-samples.md）。

内容全部来自真实运行：快照 + 业务证据 + 假设文件 → CLI 结果。
不手写任何数字，避免「文档里的数字与代码跑出来的不一致」。

用法：
    .venv/bin/python tools/render_phase2_report.py
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

from engine.assumptions.schema import load_assumptions  # noqa: E402
from engine.pipeline import Analyzer, AnalyzerConfig  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
ASSUMPTIONS = ROOT / "data" / "assumptions"
TARGET = ROOT / "docs" / "phase2-samples.md"

CASES = (
    {
        "name": "600519 贵州茅台（非金融 / FCFF 主 + 股息交叉检查）",
        "query": "600519",
        "assumptions": "600519-fcff-base.json",
        "cross_check": "600519-ddm-crosscheck.json",
    },
    {
        "name": "600900 长江电力（成熟非金融 / 股息主）",
        "query": "600900",
        "assumptions": "600900-ddm-base.json",
        "cross_check": None,
    },
    {
        "name": "601398 工商银行（银行路线：资料不足）",
        "query": "601398",
        "assumptions": None,
        "cross_check": None,
    },
    {
        "name": "601318 中国平安（保险：首版未实现）",
        "query": "601318",
        "assumptions": None,
        "cross_check": None,
    },
    {
        "name": "601088 中国神华（明显周期：首版未实现）",
        "query": "601088",
        "assumptions": None,
        "cross_check": None,
    },
    {
        "name": "600030 中信证券（券商：首版未实现）",
        "query": "600030",
        "assumptions": None,
        "cross_check": None,
    },
)


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}"


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2%}"


def _analyzer() -> Analyzer:
    return Analyzer(
        AnalyzerConfig(
            registry_path=ROOT / "data" / "registry" / "instruments.json",
            equity_list_path=ROOT / "data" / "registry" / "cn_a_equities.json",
            snapshot_dir=ROOT / "data" / "snapshots",
            evidence_dir=ROOT / "data" / "evidence",
        )
    )


def run_case(analyzer: Analyzer, case: dict) -> dict:
    assumptions = (
        load_assumptions(ASSUMPTIONS / case["assumptions"]) if case.get("assumptions") else None
    )
    cross = (
        load_assumptions(ASSUMPTIONS / case["cross_check"]) if case.get("cross_check") else None
    )
    return analyzer.analyze(
        case["query"], assumptions=assumptions, cross_check_assumptions=cross
    ).to_dict()


def render(cases: list[dict], results: list[dict], *, generated_at: str) -> str:
    lines: list[str] = [
        "# 阶段二真实研究输出（自动生成）",
        "",
        f"生成时间：{generated_at}｜生成脚本：`tools/render_phase2_report.py`",
        "",
        "本文件所有数字都来自真实运行：固定快照 + 结构化业务证据 + 研究假设文件 → `engine.cli`。",
        "模型状态一律为 `research`，决策一律为 `undetermined`；研究估值不是买点，也不是安全边际结论。",
        "",
        "## 一、样本总览",
        "",
        "| 样本 | 分析状态 | 企业族 | 主模型 | 基准财年 | 研究估值（元/股） | 情景区间 | 现价 | 与现价差距 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for case, result in zip(cases, results):
        research = (result.get("payload") or {}).get("research_valuation") or {}
        plan = result.get("model_plan") or {}
        price = research.get("price") or {}
        relation = research.get("price_relationship") or {}
        rng = research.get("valuation_range") or {}
        if research:
            value = f"{research.get('headline_value_per_share'):,.2f}"
            span = f"{rng.get('low'):,.2f} – {rng.get('high'):,.2f}"
            price_text = f"{price.get('value'):,.2f}" if price.get("value") else "—"
            diff = f"{relation.get('difference_pct'):+.2%}" if relation.get("difference_pct") is not None else "—"
        else:
            value = span = price_text = diff = "—"
        lines.append(
            f"| {case['name']} | `{result['analysis_status']}` | "
            f"{plan.get('family_label') or plan.get('family') or '—'} | "
            f"{plan.get('primary_model') or '未进入模型'} | "
            f"{research.get('base_fiscal_year') or '—'} | {value} | {span} | {price_text} | {diff} |"
        )

    for case, result in zip(cases, results):
        research = (result.get("payload") or {}).get("research_valuation") or {}
        plan = result.get("model_plan") or {}
        lines.append("")
        lines.append(f"## {case['name']}")
        lines.append("")
        lines.append(f"- 分析状态 / 模型状态 / 决策：`{result['analysis_status']}` / "
                     f"`{result.get('model_status')}` / `{result['decision']}`")
        lines.append(f"- 原因码：{'、'.join('`%s`' % c for c in result.get('reason_codes') or [])}")
        if plan.get("selection_reason"):
            lines.append(f"- 选择理由：{plan['selection_reason']}")
        for item in ((research.get("applicability") or {}).get("rejected_models") or []):
            lines.append(
                f"- 未选路线 `{item['model_id']}`：{item['reason']}（`{item['reason_code']}`）"
            )
        if not research:
            missing = result.get("missing_data") or []
            if missing:
                lines.append("")
                lines.append("| 缺失字段 | 状态 | 说明 |")
                lines.append("|---|---|---|")
                for item in missing[:8]:
                    lines.append(
                        f"| `{item.get('fact_id')}` | {item.get('status')} | {item.get('note') or ''} |"
                    )
            continue

        lines.append("")
        lines.append("### 情景与驱动")
        lines.append("")
        lines.append("| 情景 | 研究估值（元/股） | 终值占比 | 说明 |")
        lines.append("|---|---|---|---|")
        for scenario in research.get("scenarios") or []:
            lines.append(
                f"| {scenario['name']} | {scenario['value_per_share']:,.2f} | "
                f"{_pct(scenario.get('terminal_share_of_value'))} | {scenario.get('note') or ''} |"
            )
        lines.append("")
        lines.append("### 敏感性（单变量变动，其余假设不变）")
        lines.append("")
        lines.append("| 变量 | 变动 | 研究估值（元/股） | 相对基准 |")
        lines.append("|---|---|---|---|")
        for row in research.get("sensitivity") or []:
            if row.get("value_per_share") is None:
                lines.append(f"| {row['variable']} | {row['delta']:+.4f} | 拒绝计算 | — |")
                continue
            lines.append(
                f"| {row['variable']} | {row['delta']:+.4f} | {row['value_per_share']:,.2f} | "
                f"{_pct(row.get('change_pct'))} |"
            )

        data = research.get("data_used") or {}
        bridge = data.get("fcff_bridge")
        if bridge:
            lines.append("")
            lines.append("### FCFF 桥接表（基准财年，合并口径，元）")
            lines.append("")
            lines.append("| 科目 | 金额 | 状态 | 说明 |")
            lines.append("|---|---|---|---|")
            for line in bridge.get("lines") or []:
                sign = "−" if line["sign"] < 0 else ""
                lines.append(
                    f"| {line['label']} | {sign}{_money(abs(line['amount']) if line['amount'] is not None else None)} | "
                    f"{line['status']} | {line['note']} |"
                )
            lines.append("")
            lines.append(
                f"与经营现金流勾稽：经营现金流反推的 FCFF {_money(bridge.get('ocf_implied_fcff'))}，"
                f"差额 {_money(bridge.get('reconciliation_gap'))}"
                f"（{_pct(bridge.get('reconciliation_relative'))}），"
                f"是否在容差内：{'是' if bridge.get('reconciled') else '否'}"
            )

        book = data.get("dividend_book")
        if book and book.get("fiscal_years"):
            lines.append("")
            lines.append("### 分红归集（每 10 股已换算为每股）")
            lines.append("")
            lines.append("| 财年 | 普通分配（元/股） | 特别分配（元/股） | 含年度末期 |")
            lines.append("|---|---|---|---|")
            for entry in book["fiscal_years"][:6]:
                lines.append(
                    f"| {entry['fiscal_year']} | {entry['ordinary_dps']:.4f} | "
                    f"{entry['special_dps']:.4f} | {'是' if entry.get('has_annual_final') else '否'} |"
                )
            if book.get("unlabeled_special"):
                lines.append("")
                lines.append(
                    "所属财年未标注、单列不永续的特别分配："
                    + "、".join(f"{item['per_share']:.4f} 元/股" for item in book["unlabeled_special"])
                )
            if book.get("ltm_paid_per_share") is not None:
                lines.append(
                    f"过去十二个月实际支付（按除权日滚动）：{book['ltm_paid_per_share']:.4f} 元/股"
                )

        cross = research.get("cross_check")
        if cross:
            lines.append("")
            lines.append("### 交叉检查")
            lines.append("")
            if cross.get("value_per_share") is None:
                lines.append(f"- {cross.get('model_id')}：{cross.get('note')}")
            else:
                lines.append(
                    f"- `{cross['model_id']}`（{cross['model_version']}）"
                    f"假设 `{cross['assumption_id']}` → {cross['value_per_share']:,.2f} 元/股，"
                    f"相对主模型 {cross['difference_pct']:+.2%}"
                )
                lines.append(f"- {cross.get('note')}")

        lines.append("")
        lines.append("### 失效条件")
        for item in research.get("invalidation") or []:
            lines.append(f"- {item}")

    lines.append("")
    lines.append("## 二、假设文件参数与来源")
    lines.append("")
    for path in sorted(ASSUMPTIONS.glob("*.json")):
        assumptions = load_assumptions(path)
        lines.append(f"### `{path.name}`")
        lines.append("")
        lines.append(
            f"- 标的 `{assumptions.instrument_id}`｜模型 `{assumptions.model_id}` "
            f"（{assumptions.model_version}）｜状态 `{assumptions.status}`"
        )
        lines.append(
            f"- 假设 id `{assumptions.assumption_id}`｜适用日期 {assumptions.applicable_as_of}"
            f"｜基准财年 {assumptions.base_fiscal_year}｜预测期 {assumptions.horizon_years} 年"
        )
        lines.append(
            f"- 折现率 {assumptions.discount_rate.value:.4%}"
            f"（`{assumptions.discount_rate.provenance.kind}`）："
            f"{assumptions.discount_rate.provenance.source}"
        )
        lines.append(
            f"- 终值 {assumptions.terminal.method}，g = {assumptions.terminal.growth:.2%}"
            f"：{assumptions.terminal.basis}"
        )
        lines.append("")
        lines.append("| 参数 | 种类 | 来源 / 推导 |")
        lines.append("|---|---|---|")
        for name, provenance in sorted(assumptions.provenance.items()):
            lines.append(f"| `{name}` | `{provenance.kind}` | {provenance.source} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="渲染阶段二真实输出证据表")
    parser.add_argument("--out", default=str(TARGET))
    args = parser.parse_args(argv)

    analyzer = _analyzer()
    results = [run_case(analyzer, case) for case in CASES]
    rendered = render(
        list(CASES), results, generated_at=datetime.now(TZ).isoformat(timespec="seconds")
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    summary = [
        {
            "name": case["name"],
            "analysis_status": result["analysis_status"],
            "decision": result["decision"],
            "headline": (
                (result.get("payload") or {}).get("research_valuation") or {}
            ).get("headline_value_per_share"),
        }
        for case, result in zip(CASES, results)
    ]
    print(json.dumps({"out": str(out), "cases": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
