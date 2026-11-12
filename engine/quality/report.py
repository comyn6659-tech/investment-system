"""好公司池试运行报告：把评估结果整理成机器可读 JSON 与 Markdown。

报告首页固定写明：草案未批准、入池不等于买入、未检验条件、结果不用于凑数量。
"""

from __future__ import annotations

from typing import Any, Mapping

from engine.quality.models import (
    CONDITION_FAIL,
    CONDITION_PASS,
    CONDITION_PENDING,
    CONDITION_UNTESTED,
    STATUS_IN_POOL,
    STATUS_NOT_IN_POOL,
    STATUS_OUT_OF_SCOPE,
    STATUS_PENDING,
    PoolRun,
)
from engine.quality.policy import QualityPolicy

STATUS_LABELS = {
    STATUS_IN_POOL: "入池",
    STATUS_NOT_IN_POOL: "未入池",
    STATUS_PENDING: "待定",
    STATUS_OUT_OF_SCOPE: "范围外",
}
CONDITION_LABELS = {
    CONDITION_PASS: "通过",
    CONDITION_FAIL: "不通过",
    CONDITION_PENDING: "待定",
    CONDITION_UNTESTED: "未检验",
}


def _fmt(value: Any, unit: str | None = None) -> str:
    if value is None:
        return "—"
    if unit == "ratio":
        return f"{float(value):.3%}"
    if unit == "currency":
        return f"{float(value):,.0f}"
    if unit == "multiple":
        return f"{float(value):.2f}"
    if unit == "years":
        return f"{float(value):.0f}"
    if unit == "count":
        return f"{int(value)}"
    return f"{float(value):.4f}"


def _metric(metrics: Mapping[str, Any], key: str) -> Any:
    node = metrics.get(key)
    if isinstance(node, Mapping):
        return node.get("value")
    return None


def _scorecard(item: Any) -> dict[str, Any]:
    metrics = item.metrics or {}
    conditions = {cond.condition_id: cond for cond in item.conditions}
    return {
        "code": item.code,
        "name": item.name,
        "board": item.board,
        "industry": item.industry,
        "status": item.status,
        "reason": item.reason,
        "window_years": len(item.window_years),
        "available_years": len(item.available_years),
        "roe_average": _metric(metrics, "roe_average"),
        "roe_above_share": _metric(metrics, "roe_above_share"),
        "roe_recent_average": _metric(metrics, "roe_recent_average"),
        "cash_conversion": _metric(metrics, "cash_conversion"),
        "fcf_total": _metric(metrics, "fcf_total"),
        "loss_years": _metric(metrics, "loss_years"),
        "net_debt_to_ebitda": _metric(metrics, "net_debt_to_ebitda"),
        "dividend_payout": _metric(metrics, "dividend_payout"),
        "audit_nonstandard_years": _metric(metrics, "audit_nonstandard_years"),
        "penalty_count": _metric(metrics, "penalty_count"),
        "pledge_ratio": _metric(metrics, "pledge_ratio"),
        "cycle_label": (metrics.get("cycle") or {}).get("label"),
        "is_cyclical": (metrics.get("cycle") or {}).get("is_cyclical"),
        "failing_conditions": [cond.condition_id for cond in item.failing],
        "pending_conditions": [cond.condition_id for cond in item.pending],
        "untested_count": len(item.untested),
        "condition_status": {cid: cond.status for cid, cond in conditions.items()},
    }


def build_report(run: PoolRun, policy: QualityPolicy, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    in_pool = [_scorecard(item) for item in run.results if item.status == STATUS_IN_POOL]
    in_pool.sort(key=lambda row: (row["industry"] or "", row["code"]))
    return {
        "meta": {
            "as_of": run.as_of,
            "policy_id": run.policy_id,
            "policy_digest": run.policy_digest,
            "policy_status": policy.status,
            "approved": policy.approved,
            "snapshot_id": run.snapshot_id,
            "generated_at": run.generated_at,
            "window": snapshot.get("window"),
            "warning": "规则为未批准草案；入池只说明历史公开数据符合规则，不说明现在便宜，也不构成买入建议。",
        },
        "counts": run.counts,
        "untested_conditions": run.untested_conditions,
        "in_pool": in_pool,
        "near_misses": run.near_misses,
        "funnel": run.funnel,
        "sensitivity": run.sensitivity,
        "test_set": run.test_set,
        "industry_distribution": run.industry_distribution,
        "sources": snapshot.get("sources") or [],
        "snapshot_untested_conditions": snapshot.get("untested_conditions") or [],
    }


def _condition_cell(row: Mapping[str, Any]) -> str:
    order = [
        "roe_average",
        "roe_consistency",
        "roe_recent",
        "cash_conversion",
        "fcf_positive",
        "loss_years",
        "net_debt_ebitda",
        "audit_opinion",
        "no_penalty",
        "pledge_ratio",
        "dividend_payout",
    ]
    status = row.get("condition_status") or {}
    if not status and row.get("conditions"):
        status = {item["condition_id"]: item["status"] for item in row["conditions"]}
    if not status:
        return "—"
    marks = []
    for cid in order:
        if cid not in status:
            continue
        marks.append(
            {
                CONDITION_PASS: "✓",
                CONDITION_FAIL: "✗",
                CONDITION_PENDING: "?",
                CONDITION_UNTESTED: "—",
            }.get(status[cid], "?")
        )
    return "".join(marks)


def _condition_lines(row: Mapping[str, Any], conditions: list[Mapping[str, Any]]) -> list[str]:
    lines = []
    for cond in conditions:
        if cond.get("evaluation") == "untested":
            continue
        cid = cond["id"]
        status = (row.get("condition_status") or {}).get(cid)
        lines.append(f"- {cond['label']}：{CONDITION_LABELS.get(status, status or '—')}")
    return lines


def render_markdown(report: Mapping[str, Any], run: PoolRun, policy: QualityPolicy) -> str:
    meta = report["meta"]
    counts = report["counts"]
    lines: list[str] = []
    lines.append(f"# {policy.label} 试运行报告")
    lines.append("")
    lines.append(
        f"> **规则状态：{policy.status}（approved={str(policy.approved).lower()}）**。"
        "入池只回答“过去十年用公开数据看是不是一家好公司”，不回答“现在便不便宜”，"
        "不预言未来；**入池 ≠ 买入**。结果不用于调整阈值。"
    )
    lines.append("")
    lines.append(f"- as_of：{meta['as_of']}")
    lines.append(f"- 规则：`{meta['policy_id']}`，摘要 `{meta['policy_digest'][:16]}`")
    lines.append(f"- 快照：`{meta['snapshot_id']}`")
    lines.append(f"- 生成时间：{meta['generated_at']}")
    lines.append("")

    lines.append("## 1. 首页：范围与结果")
    lines.append("")
    evaluated = counts[STATUS_IN_POOL] + counts[STATUS_NOT_IN_POOL] + counts[STATUS_PENDING]
    lines.append("| 项目 | 数量 |")
    lines.append("|---|---:|")
    lines.append(f"| 全部沪深 A 股 | {counts['total']} |")
    lines.append(f"| 评价范围（非 ST、非金融、完整年报 ≥ 7 年） | {evaluated} |")
    lines.append(f"| 其中：进入逐条判定 | {counts[STATUS_IN_POOL] + counts[STATUS_NOT_IN_POOL] + counts[STATUS_PENDING]} |")
    lines.append(f"| 入池 | {counts[STATUS_IN_POOL]} |")
    lines.append(f"| 未入池 | {counts[STATUS_NOT_IN_POOL]} |")
    lines.append(f"| 待定 | {counts[STATUS_PENDING]} |")
    lines.append(f"| 范围外 | {counts[STATUS_OUT_OF_SCOPE]} |")
    breakdown = counts.get("out_of_scope_breakdown") or {}
    for reason, label in (
        ("ST", "ST/*ST"),
        ("FINANCIAL_INDUSTRY", "金融业（指标不适用）"),
        ("SHORT_HISTORY", "完整年报不足 7 年"),
        ("OUT_OF_SCOPE_MARKET", "交易所不在范围"),
    ):
        if breakdown.get(reason):
            lines.append(f"| 　范围外：{label} | {breakdown[reason]} |")
    lines.append("")
    lines.append("**本版未检验的条件：**")
    lines.append("")
    if report["untested_conditions"]:
        lines.append("| 组 | 条件 | 原因 |")
        lines.append("|---|---|---|")
        for item in report["untested_conditions"]:
            lines.append(f"| {item['group_id']} | {item['label']} | {item['reason']} |")
    else:
        lines.append("无。")
    lines.append("")
    if report["untested_conditions"]:
        lines.append(
            "未检验不等于通过：这些条件在本版没有全市场可重放的数据，池子照常发布，"
            "但报告首页和每只入池公司的成绩单都保留该标记。"
        )
    lines.append("")

    coverage = report.get("governance_coverage") or {}
    if coverage:
        lines.append("## 1b. 治理三项覆盖率（252 家候选）")
        lines.append("")
        lines.append("| 项目 | 取得 | 覆盖率 | 说明 |")
        lines.append("|---|---:|---:|---|")
        for key, label in (
            ("audit", "审计意见（2021–2025 年报）"),
            ("penalty", "处罚/立案/公开谴责公告检索"),
            ("pledge", "控股股东质押比例"),
        ):
            item = coverage.get(key) or {}
            lines.append(
                f"| {label} | {item.get('available', 0)} / {item.get('total', 0)} | "
                f"{item.get('ratio', 0):.1%} | {item.get('note', '')} |"
            )
        lines.append("")

    removed = report.get("governance_removed") or []
    if removed:
        lines.append("## 1c. 因治理条件出池的公司")
        lines.append("")
        lines.append("| 代码 | 名称 | 条件 | 证据 |")
        lines.append("|---|---|---|---|")
        for row in removed:
            lines.append(
                f"| {row['code']} | {row['name']} | {row['condition']} | {row['evidence']} |"
            )
        lines.append("")

    lines.append("## 2. 入池名单")
    lines.append("")
    if not report["in_pool"]:
        lines.append("本版没有公司通过全部已检验条件。")
    else:
        lines.append(
            "| 代码 | 名称 | 行业 | 板块 | 周期 | 窗口 | ROE均值 | ROE≥10%占比 | 近3年ROE | 现金转换 | 5年FCF(元) | 亏损年 | 净负债/EBITDA | 分红率 | 审计非标年 | 处罚件数 | 控股质押 |"
        )
        lines.append("|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for row in report["in_pool"]:
            lines.append(
                "| {code} | {name} | {industry} | {board} | {cycle} | {window} | {roe} | {share} | {recent} | {cash} | {fcf} | {loss} | {debt} | {div} | {audit} | {penalty} | {pledge} |".format(
                    code=row["code"],
                    name=row["name"],
                    industry=row["industry"] or "—",
                    board=row["board"],
                    cycle=row.get("cycle_label") or "—",
                    window=row["window_years"],
                    roe=_fmt(row["roe_average"], "ratio"),
                    share=_fmt(row["roe_above_share"], "ratio"),
                    recent=_fmt(row["roe_recent_average"], "ratio"),
                    cash=_fmt(row["cash_conversion"], "ratio"),
                    fcf=_fmt(row["fcf_total"], "currency"),
                    loss=_fmt(row["loss_years"], "years"),
                    debt=_fmt(row["net_debt_to_ebitda"], "multiple"),
                    div=_fmt(row["dividend_payout"], "ratio"),
                    audit=_fmt(row.get("audit_nonstandard_years"), "years"),
                    penalty=_fmt(row.get("penalty_count"), "count") if row.get("penalty_count") is not None else "—",
                    pledge=_fmt(row.get("pledge_ratio"), "ratio"),
                )
            )
    lines.append("")

    lines.append("## 3. 只差一条入池")
    lines.append("")
    if not report["near_misses"]:
        lines.append("没有“只差一条”的公司。")
    else:
        lines.append("| 代码 | 名称 | 行业 | 差哪一条 | 观测值 | 门槛 | 距门槛 | 相对距离 |")
        lines.append("|---|---|---|---|---:|---:|---:|---:|")
        for row in report["near_misses"]:
            lines.append(
                "| {code} | {name} | {industry} | {label} | {value} | {threshold} | {distance} | {rel} |".format(
                    code=row["code"],
                    name=row["name"],
                    industry=row["industry"] or "—",
                    label=row["label"],
                    value=_fmt(row["value"], row["unit"]),
                    threshold=_fmt(row["threshold"], row["unit"]),
                    distance=_fmt(row["distance_abs"], row["unit"]),
                    rel="—" if row["distance_rel"] is None else f"{row['distance_rel']:.1%}",
                )
            )
    lines.append("")

    lines.append("## 4. 漏斗：每条条件淘汰了多少家")
    lines.append("")
    lines.append("| 条件 | 单独不通过 | 顺序淘汰 | 顺序待定 | 剩余 |")
    lines.append("|---|---:|---:|---:|---:|")
    for row in report["funnel"]:
        lines.append(
            f"| {row['label']} | {row['independent_fail_count']} | {row['sequential_eliminated']} | {row['sequential_pending']} | {row['remaining_after']} |"
        )
    lines.append("")
    lines.append(
        "“单独不通过”是全部评价公司中该条不通过的家数；“顺序淘汰”按表中顺序，"
        "公司一旦被前一条淘汰就不再计入后面的淘汰数。"
    )
    lines.append("")

    lines.append("## 5. 敏感性（只报告，不选用）")
    lines.append("")
    lines.append("| 参数 | 取值 | 入池 | 未入池 | 待定 | 说明 |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for parameter, node in report["sensitivity"].items():
        if not node.get("applicable", True):
            lines.append(
                f"| {parameter} | — | — | — | — | {node.get('note') or '不适用'} |"
            )
            continue
        for row in node["values"]:
            lines.append(
                f"| {parameter} | {row['value']} | {row['in_pool']} | {row['not_in_pool']} | {row['pending']} | — |"
            )
    lines.append("")

    lines.append("## 6. 固定 30 只测试集成绩单")
    lines.append("")
    lines.append("条件列顺序：ROE均值、ROE≥10%占比、近3年ROE、现金转换、5年FCF、亏损年、净负债/EBITDA、审计意见、处罚、质押、分红。✓通过 ✗不通过 ?待定 —未检验/范围外。")
    lines.append("")
    lines.append("| 代码 | 名称 | 行业 | 周期 | 状态 | 条件 | 说明 |")
    lines.append("|---|---|---|---|---|---|---|")
    for row in report["test_set"]:
        if row.get("status") == "missing_from_snapshot":
            lines.append(f"| {row['code']} | — | — | 快照缺失 | — | — |")
            continue
        reason = (row.get("reason") or "").replace("|", "／")
        if len(reason) > 90:
            reason = reason[:88] + "…"
        lines.append(
            f"| {row['code']} | {row['name']} | {row['industry'] or '—'} | {(row.get('metrics') or {}).get('cycle', {}).get('label') or '—'} | {STATUS_LABELS.get(row['status'], row['status'])} | {_condition_cell(row)} | {reason} |"
        )
    lines.append("")

    lines.append("## 7. 行业分布（入池公司）")
    lines.append("")
    if not report["industry_distribution"]:
        lines.append("无入池公司。")
    else:
        lines.append("| 行业 | 入池 | 评价公司数 |")
        lines.append("|---|---:|---:|")
        for row in report["industry_distribution"]:
            lines.append(f"| {row['industry']} | {row['in_pool']} | {row['evaluated']} |")
    lines.append("")

    lines.append("## 8. 数据来源与口径")
    lines.append("")
    for source in report["sources"]:
        lines.append(
            f"- **{source.get('id')}**：{source.get('publisher')}；{source.get('interface')}；"
            f"{source.get('note', '')}"
        )
    lines.append("")
    lines.append("## 9. 复现")
    lines.append("")
    lines.append("```bash")
    lines.append(".venv/bin/python tools/build_quality_pool_data.py --fetch-only")
    lines.append(".venv/bin/python tools/build_quality_pool_data.py")
    lines.append(".venv/bin/python tools/run_quality_pool.py")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)
