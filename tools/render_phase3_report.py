#!/usr/bin/env python
"""把第三阶段真实运行结果渲染成 Markdown 证据表（docs/phase3-samples.md）。

所有数字来自真实运行：固定快照 + 版本化序列 → engine.cli。不手写任何数字。

用法：
    .venv/bin/python tools/render_phase3_report.py
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.pipeline import Analyzer, AnalyzerConfig  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
TARGET = ROOT / "docs" / "phase3-samples.md"

INDEX_CASES = (("沪深300", "000300"), ("中证500", "000905"), ("中证1000", "000852"))
ETF_CASES = (("510300", "000300"), ("510500", "000905"), ("512100", "000852"))


def _analyzer() -> Analyzer:
    return Analyzer(
        AnalyzerConfig(
            registry_path=ROOT / "data" / "registry" / "instruments.json",
            equity_list_path=ROOT / "data" / "registry" / "cn_a_equities.json",
            snapshot_dir=ROOT / "data" / "snapshots",
            evidence_dir=ROOT / "data" / "evidence",
            series_dir=ROOT / "data" / "series",
        )
    )


def _num(value, digits=2):
    if value is None:
        return "—"
    return f"{float(value):,.{digits}f}"


def _cny(value):
    if value is None:
        return "—"
    amount = float(value)
    if abs(amount) >= 1e8:
        return f"{amount / 1e8:,.2f} 亿元"
    if abs(amount) >= 1e4:
        return f"{amount / 1e4:,.2f} 万元"
    return f"{amount:,.0f} 元"


def _pct(value):
    if value is None:
        return "—"
    return f"{float(value):.1f}%"


def render(results: dict, *, generated_at: str) -> str:
    lines: list[str] = [
        "# 第三阶段首轮真实输出（自动生成）",
        "",
        f"生成时间：{generated_at}｜生成脚本：`tools/render_phase3_report.py`",
        "",
        "所有数字来自真实运行：固定快照 + 版本化指数序列 → `engine.cli`。",
        "画像是描述性统计：不是交易信号，不含目标点位，也没有波段买点。",
        "",
        "## 一、指数画像总览",
        "",
        "| 指数 | 分析状态 | 点位（日期） | PE 当前 | PE 分位 | PE 有效样本 | PB 当前 | PB 分位 | PB 有效样本 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, code in INDEX_CASES:
        result = results[("index", name)]["result"]
        payload = result["payload"]
        level = payload.get("level") or {}
        profile = payload.get("valuation_profile") or {}
        pe = (profile.get("metrics") or {}).get("pe_rolling") or {}
        pb = (profile.get("metrics") or {}).get("pb") or {}
        pe_win = pe.get("window") or {}
        pb_win = pb.get("window") or {}
        pe_cur = pe_win.get("comparison") or {}
        pb_cur = pb_win.get("comparison") or {}
        lines.append(
            f"| {name}（{code}） | `{result['analysis_status']}` | "
            f"{_num(level.get('value'))}（{level.get('as_of') or '—'}） | "
            f"{_num(pe_cur.get('value'), 2)} | {_pct(pe.get('percentile'))} | "
            f"{(pe_win.get('pool') or {}).get('size', '—')} | "
            f"{_num(pb_cur.get('value'), 2)} | {_pct(pb.get('percentile'))} | "
            f"{(pb_win.get('pool') or {}).get('size', '—')} |"
        )

    for name, code in INDEX_CASES:
        entry = results[("index", name)]
        result = entry["result"]
        profile = result["payload"].get("valuation_profile") or {}
        lines.append("")
        lines.append(f"## 指数：{name}（{code}）")
        lines.append("")
        lines.append(
            f"- 分析状态 / 模型状态 / 决策：`{result['analysis_status']}` / "
            f"`{result.get('model_status')}` / `{result['decision']}`"
        )
        lines.append(
            f"- 快照 `{result.get('snapshot_id')}`｜数据截至 {result.get('as_of')}"
            f"｜序列 {profile.get('series_ids')}"
        )
        rule = profile.get("window_rule") or {}
        lines.append(
            f"- 窗口：{rule.get('months')} 个完整自然月，有效月份门槛 {rule.get('min_valid_months')}"
        )
        lines.append("")
        lines.append("| 指标 | 原列名 | 当前值 | 当前日期 | 分位 | 有效样本 | 参考区间 | 中位数 | 覆盖 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for metric in ("pe_rolling", "pb"):
            block = (profile.get("metrics") or {}).get(metric)
            if not block:
                continue
            win = block.get("window") or {}
            cur = win.get("comparison") or {}
            pool = win.get("pool") or {}
            lines.append(
                f"| {metric} | {block.get('label')} | {_num(cur.get('value'), 2)} | "
                f"{cur.get('date') or '—'} | {_pct(block.get('percentile'))} | {pool.get('size', '—')} | "
                f"{_num(pool.get('min'), 2)} – {_num(pool.get('max'), 2)} | "
                f"{_num(pool.get('median'), 2)} | {win.get('valid_months')}/{win.get('expected_months')} |"
            )
        official = profile.get("official_short_history") or {}
        for metric, block in official.items():
            latest = block.get("latest") or {}
            lines.append("")
            lines.append(
                f"官方短历史（并列事实，不参与长历史排名）：{block.get('label')} "
                f"{_num(latest.get('value'), 2)}（{latest.get('date')}，"
                f"仅 {block.get('observation_count')} 个交易日，序列 `{block.get('series_id')}`）"
            )
        missing = profile.get("missing") or []
        if missing:
            lines.append("")
            lines.append("覆盖不足的指标：" + "；".join(f"{m['metric']}（{m['reason']}）" for m in missing))
        lines.append("")
        lines.append("限制：" + "；".join(profile.get("limitations") or []))

    lines.append("")
    lines.append("## 二、ETF 产品资料检查")
    lines.append("")
    lines.append(
        "| ETF | 跟踪指数 | 状态 | 价格（日期/状态） | 单位净值（日期） | 披露时刻 | 事后同日折溢价 | 近 20 日均额（有效天数） |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for etf_code, _ in ETF_CASES:
        result = results[("etf", etf_code)]["result"]
        check = (result["payload"].get("product_check") or {})
        price = (check.get("price") or {}).get("price") or {}
        bar = (check.get("price") or {}).get("bar_status") or {}
        nav = (check.get("nav") or {}).get("value") or {}
        nav_date = (check.get("nav") or {}).get("nav_date") or {}
        premium = check.get("premium_discount_same_day") or {}
        turnover = check.get("turnover") or {}
        lines.append(
            f"| {etf_code} | {((check.get('identity') or {}).get('underlying_index') or {}).get('instrument_id')} | "
            f"`{result['analysis_status']}` | {_num(price.get('value'), 3)}"
            f"（{price.get('period')} / {bar.get('value')}） | {_num(nav.get('value'), 4)}"
            f"（{nav_date.get('value')}） | "
            f"{'已知' if check.get('nav', {}).get('disclosure_time_known') else '未知'} | "
            f"{_num(premium.get('value'), 3)}% | "
            f"{_cny((turnover.get('average') or {}).get('value'))}"
            f"（{turnover.get('days_used')} 天） |"
        )

    for etf_code, _ in ETF_CASES:
        result = results[("etf", etf_code)]["result"]
        check = (result["payload"].get("product_check") or {})
        fees = check.get("fees") or {}
        trading = check.get("trading") or {}
        lines.append("")
        lines.append(f"### ETF：{etf_code}")
        lines.append("")
        lines.append(f"- 分析状态 / 决策：`{result['analysis_status']}` / `{result['decision']}`")
        lines.append(
            f"- 费率：管理费 {_num((fees.get('management') or {}).get('value'), 2)}%/年，"
            f"托管费 {_num((fees.get('custody') or {}).get('value'), 2)}%/年（生效日未知，以基金合同为准）"
        )
        lines.append(
            f"- 交易状态：停复牌 "
            f"{'已知' if (trading.get('secondary_market_status') or {}).get('value') else '未取得'}"
            f"｜买卖价差 {'已知' if (trading.get('bid_ask_spread') or {}).get('value') else '未取得'}"
        )
        for item in check.get("missing") or []:
            lines.append(f"- 缺失：{item.get('item')}（{item.get('status')}）{item.get('note') or ''}")
        profile = check.get("underlying_index_profile") or {}
        pe = (profile.get("metrics") or {}).get("pe_rolling") or {}
        pb = (profile.get("metrics") or {}).get("pb") or {}
        lines.append(
            f"- 关联指数画像：PE 分位 {_pct(pe.get('percentile'))}，PB 分位 {_pct(pb.get('percentile'))}"
            f"（与从指数名称进入完全一致）"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="渲染第三阶段真实输出证据表")
    parser.add_argument("--out", default=str(TARGET))
    args = parser.parse_args(argv)

    analyzer = _analyzer()
    results: dict = {}
    for name, _ in INDEX_CASES:
        results[("index", name)] = {"result": analyzer.analyze(name).to_dict()}
    for etf_code, _ in ETF_CASES:
        results[("etf", etf_code)] = {"result": analyzer.analyze(etf_code).to_dict()}

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render(results, generated_at=datetime.now(TZ).isoformat(timespec="seconds")),
        encoding="utf-8",
    )
    print(f"written {out}")
    for key, entry in results.items():
        print(key, entry["result"]["analysis_status"], entry["result"]["decision"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
