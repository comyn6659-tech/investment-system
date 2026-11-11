#!/usr/bin/env python
"""估值分组的事后诊断：生成机器可读结果与 Markdown 报告。

本脚本只做描述性统计：
- 不实现进出状态机、不模拟持仓、不计交易成本、不做回测；
- 不搜索参数、不产出买卖判断；
- 分组阈值固定为总设计冻结值，脚本没有修改入口。

用法：
    .venv/bin/python tools/run_ex_post_diagnostics.py
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

from engine.diagnostics import (  # noqa: E402
    FROZEN_THRESHOLDS,
    GROUPS,
    GROUP_LABELS,
    GUARDRAILS,
    HORIZONS,
    build_index_diagnostics,
)
from engine.series.models import (  # noqa: E402
    FREQ_DAILY,
    FREQ_MONTHLY_REFERENCE,
    METRIC_PB,
    METRIC_PE_ROLLING,
    METRIC_PRICE_INDEX_CLOSE,
    SeriesStore,
)

TZ = ZoneInfo("Asia/Singapore")
JSON_OUT = ROOT / "data" / "diagnostics" / "ex-post-diagnostics.json"
MD_OUT = ROOT / "docs" / "phase3b-diagnostics.md"

INDICES = (("000300", "沪深300"), ("000905", "中证500"), ("000852", "中证1000"))


def _num(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def _pct(value) -> str:
    if value is None:
        return "—"
    return f"{value:.1%}"


def _stat_cells(stats: dict) -> str:
    """把中位数/四分位/最小最大/正收益比例拆成独立单元格。"""
    if stats["matured"] == 0:
        return "— | — | — | —"
    return (
        f"{_num(stats['median'])}% | "
        f"{_num(stats['q1'])} ~ {_num(stats['q3'])}% | "
        f"{_num(stats['min'])}% / {_num(stats['max'])}% | "
        f"{_pct(stats['positive_ratio'])}"
    )


def render_markdown(payload: dict) -> str:
    generated_at = payload["generated_at"]
    lines: list[str] = [
        "# 估值分组的事后诊断（第三阶段第二批 A）",
        "",
        f"生成时间：{generated_at}｜生成脚本：`tools/run_ex_post_diagnostics.py`",
        "",
        "读法（重要）：",
        "",
    ]
    for item in GUARDRAILS:
        lines.append(f"- {item}")
    lines += [
        "",
        "## 1. 口径",
        "",
        "| 项目 | 定义 |",
        "|---|---|",
        "| 信号时点 t | 月度估值序列的每个观测日 |",
        "| 分位 | 复用画像的 `build_window`（as_of = t），60 个完整自然月、可用月份门槛 54、当前值不入池 |",
        "| 起点价格 | 不晚于 t 的最后一个交易日收盘（事后同日对齐） |",
        "| 终点价格 | 不晚于「t + h 个日历月」月末的最后一个交易日收盘 |",
        "| 前瞻期 h | 3 / 6 / 12 个日历月 |",
        "| 未到期 | 价格数据未覆盖到终点 → 不计入，不截断 |",
        "| 敏感性 | 起点改为「下一个有分位的月度观测日」，终点不变 |",
        "| 收益 | 价格指数收益，**不含分红** |",
        "",
        "分组阈值（总设计冻结值，本批不得增改）：",
        "",
        "| 组 | 条件 |",
        "|---|---|",
    ]
    for name in GROUPS:
        condition = GROUP_LABELS[name]
        lines.append(f"| {name} | {condition} |")
    lines += [
        "",
        f"阈值：PE 低 ≤{FROZEN_THRESHOLDS['pe_low_max']:.0f}、高 ≥{FROZEN_THRESHOLDS['pe_high_min']:.0f}；"
        f"PB 低 ≤{FROZEN_THRESHOLDS['pb_low_max']:.0f}、高 ≥{FROZEN_THRESHOLDS['pb_high_min']:.0f}。"
        "边界归属：等于下限归「低」、等于上限归「高」，不重复计入两组。",
        "",
    ]
    for name, label in INDICES:
        entry = payload["indices"][name]
        lines += [
            f"## 2.{INDICES.index((name, label)) + 1} {label}（{entry['instrument_id']}）",
            "",
            f"- 观测日 {entry['observation_dates']} 个；可评估 {entry['evaluable_months']} 个；"
            f"分位不足 {entry['not_evaluable_months']} 个（不插补，计入不可评估）",
            f"- 可评估区间：{entry['first_evaluable']} → {entry['last_evaluable']}",
            f"- 序列：PE `{entry['pe_series_id']}`｜PB `{entry['pb_series_id']}`"
            f"｜价格 `{entry['price_series_id']}`（价格指数，不含分红）",
            "",
            "| 组 | 月份数 | 独立段数 | 样本起止 | h | 到期 n | 未到期 | 中位数 | 四分位 | 最小/最大 | 正收益比例 |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for group in GROUPS:
            block = entry["groups"][group]
            for h in HORIZONS:
                stats = block["horizons"][str(h)]
                lines.append(
                    f"| `{group}` | {block['months']} | {block['segments']} | "
                    f"{block['sample_start'] or '—'} → {block['sample_end'] or '—'} | {h} 月 | "
                    f"{stats['matured']} | {stats['not_matured']} | {_stat_cells(stats)} |"
                )
        lines += [
            "",
            "敏感性（起点改为下一个有分位的月度观测日，终点不变）：",
            "",
            "| 组 | h | 到期 n | 中位数 | 四分位 | 正收益比例 |",
            "|---|---|---|---|---|---|",
        ]
        for group in GROUPS:
            block = entry["groups"][group]
            for h in HORIZONS:
                stats = block["horizons_next_observation_start"][str(h)]
                lines.append(
                    f"| `{group}` | {h} 月 | {stats['matured']} | "
                    f"{_num(stats['median'])}% | "
                    f"{_num(stats['q1'])} ~ {_num(stats['q3'])} | "
                    f"{_pct(stats['positive_ratio'])} |"
                )
        lines.append("")
    lines += [
        "## 3. 与画像的一致性核对",
        "",
        "每个指数最新观测日的分位必须等于画像输出；不一致说明复核失败，而不是数据差异。",
        "",
        "| 指数 | 最新观测日 | PE 分位 | PB 分位 |",
        "|---|---|---|---|",
    ]
    for name, label in INDICES:
        entry = payload["indices"][name]
        last = entry["samples"][-1] if entry["samples"] else {}
        lines.append(
            f"| {label} | {last.get('t') or '—'} | {_num(last.get('pe_percentile'), 4)}"
            f" | {_num(last.get('pb_percentile'), 4)} |"
        )
    lines += [
        "",
        "## 4. 已知限制",
        "",
        "- 月度采样、重叠样本：同一段行情会被 3/6/12 个月的前瞻期反复计入，观测数不是独立交易次数；",
        "- 估值公布时刻未知：起点用「不晚于 t 的最后一个交易日」，敏感性组改用下一个观测日，"
        "两组都只是事后近似；",
        "- 收益不含分红，不能与全收益或基金净值收益直接比较；",
        "- 样本区间受估值序列覆盖限制（000852 自 2014-10 起），不同指数之间不可合并计数；",
        "- 本报告不分层、不控制市场环境影响，只给出分组后的分布描述。",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="估值分组事后诊断")
    parser.add_argument("--json-out", default=str(JSON_OUT))
    parser.add_argument("--md-out", default=str(MD_OUT))
    args = parser.parse_args(argv)

    store = SeriesStore(ROOT / "data" / "series")
    now = datetime.now(TZ)
    indices: dict[str, dict] = {}
    for code, label in INDICES:
        instrument_id = f"INDEX:{code}.SH"
        pe = store.find(instrument_id, METRIC_PE_ROLLING, frequency=FREQ_MONTHLY_REFERENCE)
        pb = store.find(instrument_id, METRIC_PB, frequency=FREQ_MONTHLY_REFERENCE)
        price = store.find(
            instrument_id, METRIC_PRICE_INDEX_CLOSE, frequency=FREQ_DAILY
        )
        if pe is None or pb is None or price is None:
            raise SystemExit(f"{instrument_id} 缺少序列：pe={pe is not None} pb={pb is not None} price={price is not None}")
        indices[code] = build_index_diagnostics(
            instrument_id=instrument_id,
            pe_series=pe,
            pb_series=pb,
            price_series=price,
        )

    payload = {
        "schema_version": "ex-post-diagnostics/1",
        "generated_at": now.isoformat(timespec="seconds"),
        "purpose": "估值分组的事后价格表现描述；不是策略、不是回测、不是买卖结论",
        "thresholds": FROZEN_THRESHOLDS,
        "thresholds_note": "总设计 §7.2 冻结值，本批不得增改；脚本没有修改入口",
        "horizons_months": list(HORIZONS),
        "return_basis": "price_index_close（价格指数，不含分红）",
        "guardrails": list(GUARDRAILS),
        "indices": indices,
    }

    json_path = Path(args.json_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md_path = Path(args.md_out)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(f"written {json_path.relative_to(ROOT)} and {md_path.relative_to(ROOT)}")
    for code, label in INDICES:
        entry = indices[code]
        print(f"  {label}: evaluable={entry['evaluable_months']} not={entry['not_evaluable_months']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
