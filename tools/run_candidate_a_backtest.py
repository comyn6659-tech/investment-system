#!/usr/bin/env python
"""候选 A 的周度研究回测：生成机器可读结果与 Markdown 报告。

范围（第三批施工单 §3）：只做候选 A、只做理论指数、只做研究测算。
不做候选 B、不做 ETF 可执行回测、不搜索任何参数、不改 CLI、不升级 validated。

用法：
    .venv/bin/python tools/run_candidate_a_backtest.py
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

from engine.backtest.runner import (  # noqa: E402
    DEFAULT_COSTS,
    DEFAULT_LAGS,
    SPLIT_DATE,
    run_index_backtest,
)
from engine.series.models import (  # noqa: E402
    FREQ_DAILY,
    METRIC_OFFICIAL_CLOSE,
    METRIC_OFFICIAL_PE_ROLLING,
    METRIC_TOTAL_RETURN_CLOSE,
    SeriesStore,
)

TZ = ZoneInfo("Asia/Singapore")
JSON_OUT = ROOT / "data" / "backtest" / "candidate-a.json"
MD_OUT = ROOT / "docs" / "phase3c-backtest.md"

INDICES = (
    ("000300", "沪深300", "2005-04-08"),
    ("000905", "中证500", "2007-01-15"),
    ("000852", "中证1000", "2014-10-17"),
)

GUARDRAILS = (
    "这是研究回测，不构成任何买卖结论，也不构成投资结论。",
    "数据可得时点为**假设**：官方文件不带公布时刻，本批假定当日估值只在收盘后可得，"
    "信号在下一交易日收盘执行（T+1），并另报 T+2。历史修订情况未知。",
    "理论指数不可直接投资：没有管理费、申赎、冲击成本，也没有跟踪误差。",
    "三个指数高度相关，分开报告不等于三个独立样本。",
    "交易次数很少（个位数到十几次），任何差异都可能来自个别交易，不能据此推断规则好坏。",
    "全收益为主口径、价格指数并列；两者不能互相替代。",
)


def _pct(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value * 100:,.{digits}f}%"


def _num(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def render_markdown(payload: dict) -> str:
    lines: list[str] = [
        "# 候选 A 周度研究回测（第三阶段第三批）",
        "",
        f"生成时间：{payload['generated_at']}｜生成脚本：`tools/run_candidate_a_backtest.py`",
        "",
        "读法（重要）：",
        "",
    ]
    for item in GUARDRAILS:
        lines.append(f"- {item}")
    lines += [
        "",
        "## 1. 冻结口径",
        "",
        f"- 入场：周度分位 ≤ {payload['thresholds']['entry_percentile']:.0f}（空仓时）",
        f"- 退出：周度分位 ≥ {payload['thresholds']['exit_percentile']:.0f}（持仓时）",
        f"- 到期退出：持有满 {payload['thresholds']['max_hold_months']} 个日历月",
        "- 观察点：每个 ISO 周最后一个交易日；样本为之前 5 个日历年的周度观测，当前周不入池",
        "- 覆盖：可用周数 ≥ 区间内有交易日的周数 × 90%，否则该观察点不可评估（不插补）",
        "- 分位公式复用画像的 `percentile_of`",
        f"- 执行：T+1 收盘为主口径，T+2 为敏感性；成本单边 {int(DEFAULT_COSTS[0])}/{int(DEFAULT_COSTS[1])}/{int(DEFAULT_COSTS[2])} bp",
        "- 基准：同期全收益指数买入持有 + 价格指数买入持有；现金按 0% 计息",
        f"- 时序划分：{SPLIT_DATE} 之前为开发区间，之后为留出区间（规则已冻结，不因留出结果调整）",
        "",
    ]

    for code, label, launch in INDICES:
        entry = payload["indices"][code]
        weekly = entry["weekly"]
        quality = entry["data_quality"]
        window = entry.get("evaluation_window") or {}
        lines += [
            f"## 2.{INDICES.index((code, label, launch)) + 1} {label}（{entry['instrument_id']}）",
            "",
            f"- 指数发布日 {launch}；样本自 {quality['first_close']} 起，回测区间 "
            f"{window.get('start')} → {window.get('end')}",
            f"- 周度观察点 {weekly['total_weeks']} 个：可评估 {weekly['evaluable']} 个、"
            f"不可评估 {weekly['not_evaluable']} 个",
            f"- 滚动市盈率：首个 {quality['first_pe']}，最后 {quality['last_pe']}；"
            f"交易日 {quality['trading_days_total']} 天、其中 {quality['pe_present_total']} 天有 PE",
            f"- 序列：收盘 `{entry['series_ids'].get('close')}`｜PE `{entry['series_ids'].get('pe')}`"
            f"｜全收益 `{entry['series_ids'].get('tr')}`",
            "",
        ]
        if not entry.get("runs"):
            lines += [f"- 说明：{entry.get('note')}", ""]
            continue

        lines += [
            "### 数据质量（逐年，发布日之后）",
            "",
            "| 年份 | 交易日 | 有 PE 天数 | PE 覆盖率 | PE 非正天数 |",
            "|---|---|---|---|---|",
        ]
        for row in quality["years"]:
            lines.append(
                f"| {row['year']} | {row['trading_days']} | {row['pe_present']} | "
                f"{_pct(row['pe_coverage'], 1)} | {row['pe_non_positive']} |"
            )
        lines += [
            "",
            f"PE 出现在收盘日历之外的交易日：{quality['pe_days_not_in_close_calendar']} 条",
            "",
            "不可评估原因（按类计数）："
            + "；".join(
                f"{key} {value} 个"
                for key, value in (weekly.get("not_evaluable_summary") or {}).items()
            ),
            "",
            "### 主口径：全收益指数（价格指数并列）",
            "",
            "| 口径 | 执行 | 成本 | 成交笔数 | 累计收益 | 年化 | 最大回撤 | 市场暴露 | 年换手 | 最差单笔 | 到期退出占比 |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for basis, basis_label in (("total_return", "全收益"), ("price", "价格指数")):
            for lag in ("lag1", "lag2"):
                for cost in payload["costs"]:
                    block = entry["runs"][basis][lag][str(int(cost))]["full"]
                    lines.append(
                        f"| {basis_label} | T+{lag[-1]} | {int(cost)}bp | {block['trades']} | "
                        f"{_pct(block['cumulative_return'])} | {_pct(block['annualized_return'])} | "
                        f"{_pct((block['max_drawdown'] or {}).get('max_drawdown'))} | "
                        f"{_pct(block['market_exposure'], 1)} | {_num(block['turnover_per_year'], 1)} | "
                        f"{_pct(block['worst_trade'])} | {_pct(block['expiry_exit_ratio'], 1)} |"
                    )
        bench = entry["benchmarks"]
        lines += [
            "",
            f"同期基准：全收益指数买入持有 {_pct(bench['total_return_buy_hold'])}；"
            f"价格指数买入持有 {_pct(bench['price_buy_hold'])}；现金 0%。",
            "",
        ]

        base_cost = str(int(DEFAULT_COSTS[1]))
        tr_lag1 = entry["runs"]["total_return"]["lag1"][base_cost]
        lines += [
            "### 开发 / 留出区间（全收益、T+1、成本 10bp）",
            "",
            "| 区间 | 起止 | 成交笔数 | 累计收益 | 年化 | 最大回撤 | 市场暴露 |",
            "|---|---|---|---|---|---|---|",
        ]
        for key, name in (("development", "开发"), ("holdout", "留出"), ("full", "全样本")):
            block = tr_lag1[key]
            lines.append(
                f"| {name} | {block['start']} → {block['end']} | {block['trades']} | "
                f"{_pct(block['cumulative_return'])} | {_pct(block['annualized_return'])} | "
                f"{_pct((block['max_drawdown'] or {}).get('max_drawdown'))} | "
                f"{_pct(block['market_exposure'], 1)} |"
            )
        lines += [
            "",
            "### 逐年表现（全收益、T+1、成本 10bp）",
            "",
            "| 年份 | 收益 | 成交笔数 | 市场暴露 |",
            "|---|---|---|---|",
        ]
        for row in tr_lag1["yearly"]:
            lines.append(
                f"| {row['year']} | {_pct(row['return'])} | {row['trades']} | {_pct(row['exposure'], 1)} |"
            )

        trades = tr_lag1["trade_list"]
        lines += [
            "",
            "### 逐笔交易（全收益、T+1、成本 10bp）",
            "",
            "| # | 信号日 | 成交日 | 买入 | 分位 | 退出信号日 | 退出成交日 | 卖出 | 退出原因 | 持有天数 | 毛收益 | 净收益 |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for idx, trade in enumerate(trades, 1):
            lines.append(
                f"| {idx} | {trade['entry_signal_date']} | {trade['entry_exec_date']} | "
                f"{_num(trade['entry_price'], 2)} | {_num(trade['entry_percentile'], 1)} | "
                f"{trade['exit_signal_date'] or '—'} | {trade['exit_exec_date']} | "
                f"{_num(trade['exit_price'], 2)} | {trade['exit_reason']} | "
                f"{trade['holding_days']} | {_pct(trade['gross_return'])} | {_pct(trade['net_return'])} |"
            )
        holding = tr_lag1["full"]["holding_days"]
        lines += [
            "",
            f"持有天数：最短 {holding['min']}、中位 {holding['median']}、最长 {holding['max']}；"
            f"退出原因分布 {tr_lag1['full']['exit_reasons']}。",
            "",
        ]

    lines += [
        "## 3. 结果怎么读",
        "",
        "- 三个指数的交易次数都在个位数到十几次，任何差异都可能来自一两笔交易；",
        "- 全收益与价格指数的差别主要来自分红，两个口径分开看；",
        "- T+1 与 T+2 的差别说明执行时点假设对结果的影响；",
        "- 开发/留出区间分开报告；规则在事前冻结，留出结果没有用于调整任何参数；",
        "- 本报告不做统计检验，也不给出规则好坏的结论。",
        "",
        "## 4. 已知限制",
        "",
        "- 估值公布时刻未知：T+1 是假设，不是已证明的无未来信息；",
        "- 官方滚动市盈率的加权方式与亏损处理未在文件中说明；",
        "- 理论指数不含管理费、申赎与冲击成本，成本情景只是研究假设；",
        "- 现金按 0% 计息，未考虑无风险收益；",
        "- 交易次数少，统计上不足以区分规则与运气。",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="候选 A 周度研究回测")
    parser.add_argument("--json-out", default=str(JSON_OUT))
    parser.add_argument("--md-out", default=str(MD_OUT))
    args = parser.parse_args(argv)

    store = SeriesStore(ROOT / "data" / "series")
    now = datetime.now(TZ)
    indices: dict[str, dict] = {}
    for code, label, launch in INDICES:
        instrument_id = f"INDEX:{code}.SH"
        close = store.find(instrument_id, METRIC_OFFICIAL_CLOSE, frequency=FREQ_DAILY)
        pe = store.find(instrument_id, METRIC_OFFICIAL_PE_ROLLING, frequency=FREQ_DAILY)
        tr = store.find(instrument_id, METRIC_TOTAL_RETURN_CLOSE, frequency=FREQ_DAILY)
        if close is None or pe is None or tr is None:
            raise SystemExit(
                f"{instrument_id} 缺少序列：close={close is not None} pe={pe is not None} tr={tr is not None}"
            )
        indices[code] = run_index_backtest(
            instrument_id=instrument_id,
            close_observations=close.observations,
            pe_observations=pe.observations,
            tr_observations=tr.observations,
            launch_date=launch,
            series_ids={
                "close": close.series_id,
                "pe": pe.series_id,
                "tr": tr.series_id,
            },
        )

    payload = {
        "schema_version": "candidate-a-backtest/1",
        "generated_at": now.isoformat(timespec="seconds"),
        "strategy": "candidate_a_weekly_valuation_percentile",
        "purpose": "周度分位规则的研究回测（理论指数）；不是投资建议",
        "thresholds": {
            "entry_percentile": 20.0,
            "exit_percentile": 60.0,
            "max_hold_months": 12,
            "window_years": 5,
            "min_coverage": 0.9,
        },
        "costs": list(DEFAULT_COSTS),
        "execution_lags": list(DEFAULT_LAGS),
        "split_date": SPLIT_DATE,
        "return_basis": {"primary": "total_return", "secondary": "price"},
        "cash_return": 0.0,
        "guardrails": list(GUARDRAILS),
        "indices": indices,
    }

    json_path = Path(args.json_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path = Path(args.md_out)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(payload), encoding="utf-8")

    def _display(path: Path) -> str:
        try:
            return str(path.resolve().relative_to(ROOT))
        except ValueError:
            return str(path.resolve())

    print(f"written {_display(json_path)} and {_display(md_path)}")
    for code, label, _ in INDICES:
        entry = indices[code]
        if not entry.get("runs"):
            print(f"  {label}: 无可评估观察点")
            continue
        block = entry["runs"]["total_return"]["lag1"]["10"]["full"]
        print(
            f"  {label}: trades={block['trades']} cum={block['cumulative_return']:.2%} "
            f"bench={entry['benchmarks']['total_return_buy_hold']:.2%}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
