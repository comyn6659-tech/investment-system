"""银行池试运行报告：首页、名单、只差一条、漏斗、敏感性、两家测试银行、所有制与预演摘要。"""

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
    if unit == "percent":
        return f"{float(value):.2f}%"
    if unit == "pp":
        return f"{float(value):+.2f}pp"
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
        "roe_recent_average": _metric(metrics, "roe_recent_average"),
        "loss_years": _metric(metrics, "loss_years"),
        "npl_ratio": _metric(metrics, "npl_ratio"),
        "npl_change_5y": _metric(metrics, "npl_change_5y"),
        "provision_coverage": _metric(metrics, "provision_coverage"),
        "core_tier1_ratio": _metric(metrics, "core_tier1_ratio"),
        "net_interest_margin": metrics.get("net_interest_margin"),
        "dividend_missing_years": _metric(metrics, "dividend_missing_years"),
        "dividend_payout": _metric(metrics, "dividend_payout"),
        "audit_nonstandard_years": _metric(metrics, "audit_nonstandard_years"),
        "penalty_count": _metric(metrics, "penalty_count"),
        "pledge_ratio": _metric(metrics, "pledge_ratio"),
        "ownership": (metrics or {}).get("ownership"),
        "failing_conditions": [cond.condition_id for cond in item.failing],
        "pending_conditions": [cond.condition_id for cond in item.pending],
        "condition_status": {cid: cond.status for cid, cond in conditions.items()},
    }


def build_report(run: PoolRun, policy: QualityPolicy, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    in_pool = [_scorecard(item) for item in run.results if item.status == STATUS_IN_POOL]
    in_pool.sort(key=lambda row: row["code"])
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
        "sources": snapshot.get("sources") or [],
        "snapshot_untested_conditions": snapshot.get("untested_conditions") or [],
    }


def _condition_cell(row: Mapping[str, Any], order: list[str]) -> str:
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


def render_markdown(report: Mapping[str, Any], run: PoolRun, policy: QualityPolicy) -> str:
    meta = report["meta"]
    counts = report["counts"]
    order = [str(condition["id"]) for condition in policy.conditions()]
    lines: list[str] = []
    lines.append(f"# {policy.label}：试运行报告")
    lines.append("")
    lines.append(
        f"> **规则状态：{policy.status}（approved={str(policy.approved).lower()}）**。"
        "入池只回答“过去十年用公开数据看是不是一家会赚钱、资产干净、资本厚、分红稳的银行”，"
        "不回答“现在便不便宜”，不预言未来；**入池 ≠ 买入**。"
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
    lines.append(f"| 评价范围（沪深上市银行、非 ST、完整年报 ≥ 7 年） | {evaluated} |")
    lines.append(f"| 入池 | {counts[STATUS_IN_POOL]} |")
    lines.append(f"| 未入池 | {counts[STATUS_NOT_IN_POOL]} |")
    lines.append(f"| 待定 | {counts[STATUS_PENDING]} |")
    lines.append(f"| 范围外 | {counts[STATUS_OUT_OF_SCOPE]} |")
    breakdown = counts.get("out_of_scope_breakdown") or {}
    for reason, label in (
        ("NON_BANK", "非银行（券商、保险及其他行业不做）"),
        ("ST", "ST/*ST"),
        ("SHORT_HISTORY", "完整年报不足 7 年"),
        ("OUT_OF_SCOPE_MARKET", "交易所不在范围"),
    ):
        if breakdown.get(reason):
            lines.append(f"| 　范围外：{label} | {breakdown[reason]} |")
    lines.append("")

    coverage = report.get("data_coverage") or {}
    if coverage:
        lines.append("## 1b. 银行数据来源与覆盖率")
        lines.append("")
        lines.append("| 指标 | 取得 | 覆盖率 | 来源 / 说明 |")
        lines.append("|---|---:|---:|---|")
        for key, label in (
            ("roe", "10 年 ROE（加权，年报）"),
            ("npl", "最新不良贷款率"),
            ("npl_change", "不良率 5 年变化"),
            ("provision", "最新拨备覆盖率"),
            ("core_tier1", "最新核心一级资本充足率"),
            ("nim", "最新净息差（只展示）"),
            ("dividend", "2016–2025 现金分红"),
            ("audit", "审计意见（2021–2025）"),
            ("penalty", "处罚/立案/公开谴责"),
            ("pledge", "控股股东/第一大股东质押"),
        ):
            item = coverage.get(key) or {}
            lines.append(
                f"| {label} | {item.get('available', 0)} / {item.get('total', 0)} | "
                f"{item.get('ratio', 0):.1%} | {item.get('note', '')} |"
            )
        lines.append("")

    lines.append("## 2. 入池名单")
    lines.append("")
    if not report["in_pool"]:
        lines.append("本版没有银行通过全部条件。")
    else:
        lines.append(
            "| 代码 | 名称 | 板块 | 窗口 | ROE均值 | 近3年ROE | 亏损年 | 不良率 | 不良5年变化 | 拨备覆盖率 | 核心一级 | 净息差 | 未分红年 | 分红率 | 审计非标年 | 处罚件数 | 质押比例 | 所有制 |"
        )
        lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
        for row in report["in_pool"]:
            lines.append(
                "| {code} | {name} | {board} | {window} | {roe} | {roe3} | {loss} | {npl} | {nplc} | {prov} | {core} | {nim} | {divmiss} | {payout} | {audit} | {penalty} | {pledge} | {own} |".format(
                    code=row["code"],
                    name=row["name"],
                    board=row["board"],
                    window=row["window_years"],
                    roe=_fmt(row["roe_average"], "percent"),
                    roe3=_fmt(row["roe_recent_average"], "percent"),
                    loss=_fmt(row["loss_years"], "years"),
                    npl=_fmt(row["npl_ratio"], "percent"),
                    nplc=_fmt(row["npl_change_5y"], "pp"),
                    prov=_fmt(row["provision_coverage"], "percent"),
                    core=_fmt(row["core_tier1_ratio"], "percent"),
                    nim=_fmt(row.get("net_interest_margin"), "percent"),
                    divmiss=_fmt(row["dividend_missing_years"], "years"),
                    payout=_fmt(row["dividend_payout"], "ratio"),
                    audit=_fmt(row.get("audit_nonstandard_years"), "years"),
                    penalty=_fmt(row.get("penalty_count"), "count"),
                    pledge=_fmt(row.get("pledge_ratio"), "ratio"),
                    own=row.get("ownership") or "未标记",
                )
            )
    lines.append("")

    lines.append("## 3. 只差一条入池")
    lines.append("")
    if not report["near_misses"]:
        lines.append("没有“只差一条”的银行。")
    else:
        lines.append("| 代码 | 名称 | 差哪一条 | 观测值 | 门槛 | 距门槛 | 相对距离 |")
        lines.append("|---|---|---|---:|---:|---:|---:|")
        for row in report["near_misses"]:
            lines.append(
                "| {code} | {name} | {label} | {value} | {threshold} | {distance} | {rel} |".format(
                    code=row["code"],
                    name=row["name"],
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
            f"| {row['label']} | {row['independent_fail_count']} | {row['sequential_eliminated']} | "
            f"{row['sequential_pending']} | {row['remaining_after']} |"
        )
    lines.append("")

    lines.append("## 5. 敏感性（只报告，不选用）")
    lines.append("")
    lines.append("| 参数 | 取值 | 入池 | 未入池 | 待定 |")
    lines.append("|---|---:|---:|---:|---:|")
    for parameter, node in report["sensitivity"].items():
        for row in node["values"]:
            lines.append(
                f"| {parameter} | {row['value']} | {row['in_pool']} | {row['not_in_pool']} | {row['pending']} |"
            )
    lines.append("")

    lines.append("## 6. 两家测试集银行成绩单")
    lines.append("")
    lines.append(
        "条件列顺序：" + "、".join(str(condition["label"]) for condition in policy.conditions()) + "。"
        "✓通过 ✗不通过 ?待定 —未检验/范围外。"
    )
    lines.append("")
    lines.append("| 代码 | 名称 | 状态 | 条件 | 所有制 | 说明 |")
    lines.append("|---|---|---|---|---|---|")
    for row in report["test_set"]:
        if row.get("status") == "missing_from_snapshot":
            lines.append(f"| {row['code']} | — | 快照缺失 | — | — | — |")
            continue
        reason = (row.get("reason") or "").replace("|", "／")
        if len(reason) > 100:
            reason = reason[:98] + "…"
        lines.append(
            f"| {row['code']} | {row['name']} | {STATUS_LABELS.get(row['status'], row['status'])} | "
            f"{_condition_cell(row, order)} | {(row.get('metrics') or {}).get('ownership') or '—'} | {reason} |"
        )
    lines.append("")

    ownership = report.get("ownership_distribution") or {}
    if ownership:
        lines.append("## 7. 所有制分布")
        lines.append("")
        lines.append("| 类别 | 银行池 |")
        lines.append("|---|---:|")
        for category in ("央企", "地方国企", "民企", "外资", "其他", "未标记"):
            lines.append(f"| {category} | {ownership.get(category, 0)} |")
        lines.append("")
        lines.append(
            f"标记覆盖：{ownership.get('marked', 0)} / {ownership.get('total', 0)}。"
            "只标记，不参与判定；来源见快照 `ownership.csv.gz`。"
        )
        lines.append("")

    rehearsal = report.get("valuation_rehearsal_summary") or {}
    if rehearsal:
        lines.append("## 8. 银行估值预演摘要")
        lines.append("")
        lines.append(
            f"范围内银行 {rehearsal.get('total', 0)} 家：B 级预演 {rehearsal.get('grade_b', 0)} 家、"
            f"C 级预演 {rehearsal.get('grade_c', 0)} 家、资料不足 {rehearsal.get('insufficient', 0)} 家。"
            "只写预演报告，不写入正式结论账本，CLI 结论不变；详见 "
            "[phase4h-bank-valuation-rehearsal.md](phase4h-bank-valuation-rehearsal.md)。"
        )
        lines.append("")

    lines.append("## 9. 数据来源与口径")
    lines.append("")
    for source in report["sources"]:
        lines.append(
            f"- **{source.get('id')}**：{source.get('publisher')}；{source.get('interface')}；"
            f"{source.get('note', '')}"
        )
    lines.append("")
    lines.append("## 10. 复现")
    lines.append("")
    lines.append("```bash")
    lines.append(".venv/bin/python tools/build_bank_pool_data.py --fetch-bank")
    lines.append(".venv/bin/python tools/build_bank_pool_data.py --fetch-governance --workers 6")
    lines.append(".venv/bin/python tools/build_bank_pool_data.py --fetch-ownership")
    lines.append(".venv/bin/python tools/build_bank_pool_data.py --build-tables")
    lines.append(".venv/bin/python tools/build_bank_pool_data.py --build-snapshot")
    lines.append(".venv/bin/python tools/run_bank_pool.py")
    lines.append(".venv/bin/python tools/run_bank_valuation_rehearsal.py")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)
