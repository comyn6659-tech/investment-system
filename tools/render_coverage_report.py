#!/usr/bin/env python
"""把 data/coverage/coverage-report.json 渲染成 docs/data-coverage.md。

只做格式转换，不产生新事实：报告里的每一行都来自实际接口调用记录。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "data" / "coverage" / "coverage-report.json"
OUTPUT = ROOT / "docs" / "data-coverage.md"

STATUS_LABEL = {"ok": "可用", "partial": "部分可用", "failed": "不可用"}


def _target(probe: dict) -> str:
    suffix = probe.get("probe_id", "").split(".")[-1]
    return suffix if suffix and suffix[0].isdigit() else "—"


def cell(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, (list, tuple)):
        return " ~ ".join(str(v) for v in value)
    return str(value).replace("|", "\\|").replace("\n", " ")


def main() -> int:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    summary = report["summary"]
    lines: list[str] = []

    lines.append("# 数据覆盖与口径表（阶段一）")
    lines.append("")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append(
        f"- 环境：Python {report['environment']['python']}，akshare {report['environment']['akshare']}，"
        f"pandas {report['environment']['pandas']}"
    )
    lines.append(f"- 访问限制：{report['environment']['note']}")
    lines.append(
        f"- 探针结果：共 {summary['total']} 项，可用 {summary['ok']}，部分可用 "
        f"{summary['partial']}，不可用 {summary['failed']}"
    )
    lines.append("")
    lines.append("> 本表只记录「接口实际返回了什么」。可用不等于口径已核实；")
    lines.append("> 抓取时间不等于「当时市场知道的时间」。")
    lines.append("")

    for scope in ("个股", "指数", "ETF"):
        rows = [p for p in report["probes"] if p["scope"] == scope]
        if not rows:
            continue
        lines.append(f"## {scope}")
        lines.append("")
        lines.append("| 探针对象 | 接口 | 提供方 | 状态 | 字段 | 日期区间 | 行数 | 频率 | 可获得时间 | 缺失率 | 结论 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for p in rows:
            lines.append(
                "| {target} | {iface} | {provider} | {status} | {fields} | {range} | {rows} | {freq} | {avail} | {miss} | {verdict} |".format(
                    target=cell(_target(p)),
                    iface=cell(p["interface"]),
                    provider=cell(p["provider"]),
                    status=STATUS_LABEL.get(p["status"], p["status"]),
                    fields=cell(p["fields"][:6]) + (" 等" if len(p["fields"]) > 6 else ""),
                    range=cell(p["date_range"]),
                    rows=cell(p["rows"]),
                    freq=cell(p["frequency"]),
                    avail=cell(p["availability"]),
                    miss=cell(p["missing_rate"]),
                    verdict=cell(p["verdict"]),
                )
            )
        lines.append("")

    failed = [p for p in report["probes"] if p["status"] == "failed"]
    if failed:
        lines.append("## 失败的接口（按缺口处理，不猜测）")
        lines.append("")
        for p in failed:
            lines.append(f"- **{p['interface']}**（{p['provider']}）：{p['verdict']}")
            if p.get("error"):
                lines.append(f"  - 错误：`{p['error']}`")
        lines.append("")

    lines.append("## 尚未解决的数据缺口")
    lines.append("")
    lines.append("| 缺口 | 范围 | 影响 | 说明 |")
    lines.append("|---|---|---|---|")
    for gap in report["gaps"]:
        lines.append(
            f"| {cell(gap['gap_id'])} | {cell(gap['scope'])} | {cell(gap['impact'])} | {cell(gap['detail'])} |"
        )
    lines.append("")
    lines.append("## 三只宽基的真实覆盖（来自固定快照的 coverage 字段）")
    lines.append("")
    lines.append("| 指数 | 官方估值区间 | 官方观测数 | 第三方PE/PB区间 | 第三方观测数 | 频率 | 点位日线区间 | 点位观测数 | 全收益 | 周度5年可用 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")

    snapshot_index = ROOT / "data" / "snapshots" / "manifest.json"
    if snapshot_index.exists():
        manifest = json.loads(snapshot_index.read_text(encoding="utf-8"))["snapshots"]
        for entry in manifest:
            if entry["asset_class"] != "index":
                continue
            path = ROOT / "data" / "snapshots" / entry["file"]
            data = json.loads(path.read_text(encoding="utf-8"))
            cov = data["coverage"]
            official = cov["valuation_source_official"]
            monthly = cov["valuation_source_monthly"]
            price = cov["price_series"]
            lines.append(
                "| {name} | {o1} ~ {o2} | {on} | {m1} ~ {m2} | {mn} | {freq} | {p1} ~ {p2} | {pn} | {tr} | {wk} |".format(
                    name=data["instrument_id"],
                    o1=official["start"],
                    o2=official["end"],
                    on=official["observations"],
                    m1=monthly["start"],
                    m2=monthly["end"],
                    mn=monthly["observations"],
                    freq=monthly["frequency"],
                    p1=price["start"],
                    p2=price["end"],
                    pn=price["observations"],
                    tr="可用" if cov["total_return_series"]["available"] else "不可用",
                    wk="可用" if cov["supports_weekly_5y_history"] else "不可用",
                )
            )
    lines.append("")
    lines.append("说明：官方估值与第三方估值口径不同（聚合方法、亏损股处理、是否回溯编制均未获说明），")
    lines.append("不能拼成一条看起来连续的历史序列。")
    lines.append("")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"写入 {OUTPUT}（{len(lines)} 行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
