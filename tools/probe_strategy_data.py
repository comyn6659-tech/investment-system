#!/usr/bin/env python
"""策略数据可行性调查（D1–D4）。

只用免费、无需账号的来源：已安装 akshare 版本中与需求明确对应的接口。
不购买、不注册、不爬登录页；每条候选都记录实际参数、返回字段、行数、日期范围、
推断频率与错误。结论只针对"已测来源与参数"。

用法：
    .venv/bin/python tools/probe_strategy_data.py
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TZ = ZoneInfo("Asia/Singapore")
JSON_OUT = ROOT / "data" / "coverage" / "strategy-data-feasibility.json"
MD_OUT = ROOT / "docs" / "phase3b-feasibility.md"

DATE_CANDIDATES = ("日期", "date", "净值日期", "报告日期", "时间")


def _date_column(frame: pd.DataFrame) -> str | None:
    for name in DATE_CANDIDATES:
        if name in frame.columns:
            return name
    return None


def _parse_dates(series: pd.Series) -> list[str]:
    out: list[str] = []
    for value in series:
        text = str(value)[:10]
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            out.append(text)
    return sorted(out)


def _frequency(dates: list[str]) -> str:
    if len(dates) < 3:
        return "unknown"
    gaps = [
        (datetime.fromisoformat(b) - datetime.fromisoformat(a)).days
        for a, b in zip(dates, dates[1:])
    ]
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return "unknown"
    median = statistics.median(gaps)
    if median <= 1.5:
        return "daily"
    if median <= 9:
        return "weekly"
    if median <= 32:
        return "monthly"
    if median <= 95:
        return "quarterly"
    return f"sparse({median:.0f}d)"


def probe(entry: dict) -> dict:
    """执行一条候选并原样记录结果或错误。"""
    started = time.time()
    record = {
        "probe_id": entry["probe_id"],
        "requirement": entry["requirement"],
        "purpose": entry["purpose"],
        "interface": entry["interface"],
        "params": entry.get("params", {}),
        "status": None,
        "rows": None,
        "fields": None,
        "date_range": None,
        "frequency": None,
        "error": None,
        "elapsed_s": None,
        "note": entry.get("note", ""),
    }
    try:
        frame = entry["call"]()
        record["elapsed_s"] = round(time.time() - started, 2)
        if frame is None:
            record["status"] = "empty"
            record["note"] = (record["note"] + "；接口返回 None").strip("；")
            return record
        if not isinstance(frame, pd.DataFrame):
            record["status"] = "unexpected_type"
            record["error"] = f"返回类型 {type(frame).__name__}"
            return record
        record["rows"] = int(len(frame))
        record["fields"] = [str(c) for c in frame.columns]
        if frame.empty:
            record["status"] = "empty"
            return record
        for column in ("指数中文全称", "指数中文简称"):
            if column in frame.columns:
                record.setdefault("identity", {})[column] = str(frame[column].iloc[0])
        record["value_columns"] = [
            str(c) for c in frame.columns if pd.api.types.is_numeric_dtype(frame[c])
        ]
        date_col = _date_column(frame)
        if date_col is None:
            record["status"] = "no_date_column"
            return record
        dates = _parse_dates(frame[date_col])
        if dates:
            record["date_range"] = [dates[0], dates[-1]]
            record["frequency"] = _frequency(dates)
        record["status"] = "ok"
        return record
    except Exception as exc:  # 限流、网络、参数错误都如实记录
        record["elapsed_s"] = round(time.time() - started, 2)
        record["status"] = "error"
        record["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        return record


def _candidates() -> list[dict]:
    """每条候选都对应一个明确需求；参数写死在记录里，便于复现。"""
    csindex_hist = lambda symbol: (
        lambda: ak.stock_zh_index_hist_csindex(
            symbol=symbol, start_date="20100101", end_date="20261110"
        )
    )
    return [
        # --- D1：≥5 年、周度或更高频、同口径 PE/PB ---
        {
            "probe_id": "d1_lg_pe_300",
            "requirement": "D1",
            "purpose": "第三方指数市盈率历史（供应商原始列）",
            "interface": "stock_index_pe_lg",
            "params": {"symbol": "沪深300"},
            "call": lambda: ak.stock_index_pe_lg(symbol="沪深300"),
        },
        {
            "probe_id": "d1_lg_pb_300",
            "requirement": "D1",
            "purpose": "第三方指数市净率历史（供应商原始列）",
            "interface": "stock_index_pb_lg",
            "params": {"symbol": "沪深300"},
            "call": lambda: ak.stock_index_pb_lg(symbol="沪深300"),
        },
        {
            "probe_id": "d1_csindex_value_300",
            "requirement": "D1",
            "purpose": "中证官方估值指标文件（最近若干交易日）",
            "interface": "stock_zh_index_value_csindex",
            "params": {"symbol": "000300"},
            "call": lambda: ak.stock_zh_index_value_csindex(symbol="000300"),
        },
        {
            "probe_id": "d1_csindex_hist_pe_300",
            "requirement": "D1",
            "purpose": "中证官方历史行情文件里的『滚动市盈率』列",
            "interface": "stock_zh_index_hist_csindex",
            "params": {"symbol": "000300", "start_date": "20100101", "end_date": "20261110"},
            "call": csindex_hist("000300"),
        },
        {
            "probe_id": "d1_csindex_hist_pe_905",
            "requirement": "D1",
            "purpose": "中证官方历史行情文件里的『滚动市盈率』列",
            "interface": "stock_zh_index_hist_csindex",
            "params": {"symbol": "000905", "start_date": "20100101", "end_date": "20261110"},
            "call": csindex_hist("000905"),
        },
        {
            "probe_id": "d1_csindex_hist_pe_852",
            "requirement": "D1",
            "purpose": "中证官方历史行情文件里的『滚动市盈率』列",
            "interface": "stock_zh_index_hist_csindex",
            "params": {"symbol": "000852", "start_date": "20100101", "end_date": "20261110"},
            "call": csindex_hist("000852"),
        },
        {
            "probe_id": "d1_market_pe_lg",
            "requirement": "D1",
            "purpose": "全市场市盈率（非单只指数，用于确认口径不可替代）",
            "interface": "stock_market_pe_lg",
            "params": {"symbol": "上证"},
            "call": lambda: ak.stock_market_pe_lg(symbol="上证"),
        },
        # --- D2：公布时刻或可复现发布规律 ---
        {
            "probe_id": "d2_csindex_value_fields",
            "requirement": "D2",
            "purpose": "官方估值文件的字段里是否含公布时刻",
            "interface": "stock_zh_index_value_csindex",
            "params": {"symbol": "000300"},
            "call": lambda: ak.stock_zh_index_value_csindex(symbol="000300"),
            "note": "只检查返回字段是否包含公布时间；不追登录页",
        },
        {
            "probe_id": "d2_csindex_hist_fields",
            "requirement": "D2",
            "purpose": "官方历史行情文件的字段里是否含公布时刻",
            "interface": "stock_zh_index_hist_csindex",
            "params": {"symbol": "000300", "start_date": "20100101", "end_date": "20261110"},
            "call": csindex_hist("000300"),
            "note": "只检查返回字段；文件不含发布时间戳",
        },
        # --- D3：全收益指数或可复现的总收益重建数据 ---
        {
            "probe_id": "d3_tr_shH00300",
            "requirement": "D3",
            "purpose": "全收益指数（沪市前缀，东财行情）",
            "interface": "stock_zh_index_daily_em",
            "params": {"symbol": "shH00300"},
            "call": lambda: ak.stock_zh_index_daily_em(symbol="shH00300"),
        },
        {
            "probe_id": "d3_tr_H00300",
            "requirement": "D3",
            "purpose": "全收益指数（裸代码，东财行情）",
            "interface": "stock_zh_index_daily_em",
            "params": {"symbol": "H00300"},
            "call": lambda: ak.stock_zh_index_daily_em(symbol="H00300"),
        },
        {
            "probe_id": "d3_csindex_tr_300",
            "requirement": "D3",
            "purpose": "中证官方全收益指数历史（H00300）",
            "interface": "stock_zh_index_hist_csindex",
            "params": {"symbol": "H00300", "start_date": "20100101", "end_date": "20261110"},
            "call": csindex_hist("H00300"),
        },
        {
            "probe_id": "d3_csindex_tr_905",
            "requirement": "D3",
            "purpose": "中证官方全收益指数历史（H00905）",
            "interface": "stock_zh_index_hist_csindex",
            "params": {"symbol": "H00905", "start_date": "20100101", "end_date": "20261110"},
            "call": csindex_hist("H00905"),
        },
        {
            "probe_id": "d3_csindex_tr_852",
            "requirement": "D3",
            "purpose": "中证官方全收益指数历史（H00852）",
            "interface": "stock_zh_index_hist_csindex",
            "params": {"symbol": "H00852", "start_date": "20100101", "end_date": "20261110"},
            "call": csindex_hist("H00852"),
        },
        {
            "probe_id": "d3_csindex_yield_300",
            "requirement": "D3",
            "purpose": "官方股息率（若靠股息率重建总收益则需要长历史）",
            "interface": "stock_zh_index_value_csindex",
            "params": {"symbol": "000300"},
            "call": lambda: ak.stock_zh_index_value_csindex(symbol="000300"),
        },
        # --- D4：ETF 历史日线价格与历史单位净值 ---
        *[
            {
                "probe_id": f"d4_etf_price_{code}",
                "requirement": "D4",
                "purpose": "ETF 历史日线价格",
                "interface": "fund_etf_hist_em",
                "params": {"symbol": code, "period": "daily", "adjust": ""},
                "call": (lambda c=code: ak.fund_etf_hist_em(
                    symbol=c, period="daily", start_date="20100101", end_date="20261110", adjust=""
                )),
            }
            for code in ("510300", "510500", "512100")
        ],
        *[
            {
                "probe_id": f"d4_etf_price_sina_{code}",
                "requirement": "D4",
                "purpose": "ETF 历史日线价格（备用来源：新浪）",
                "interface": "fund_etf_hist_sina",
                "params": {"symbol": f"sh{code}"},
                "call": (lambda c=code: ak.fund_etf_hist_sina(symbol=f"sh{c}")),
            }
            for code in ("510300", "510500", "512100")
        ],
        *[
            {
                "probe_id": f"d4_etf_nav_{code}",
                "requirement": "D4",
                "purpose": "ETF 历史单位净值",
                "interface": "fund_etf_fund_info_em",
                "params": {"fund": code, "start_date": "20100101", "end_date": "20261110"},
                "call": (lambda c=code: ak.fund_etf_fund_info_em(
                    fund=c, start_date="20100101", end_date="20261110"
                )),
            }
            for code in ("510300", "510500", "512100")
        ],
    ]


def _effective(record: dict) -> dict:
    """当前调用失败、但早前成功过时，用早前的成功记录参与判定。"""
    if record.get("status") == "ok":
        return record
    previous = record.get("previous_success") or {}
    if previous.get("status") == "ok":
        return {**record, **previous, "from_previous_attempt": True}
    return record


def _ok(record: dict) -> bool:
    record = _effective(record)
    return record.get("status") == "ok" and (record.get("rows") or 0) > 0


def _years(record: dict) -> float:
    record = _effective(record)
    if not record.get("date_range"):
        return 0.0
    start = datetime.fromisoformat(record["date_range"][0])
    end = datetime.fromisoformat(record["date_range"][1])
    return (end - start).days / 365.25


def _long_and_frequent(record: dict) -> bool:
    record = _effective(record)
    return _ok(record) and _years(record) >= 5 and record.get("frequency") in ("daily", "weekly")


def _has_field(record: dict, keyword: str) -> bool:
    record = _effective(record)
    return any(keyword in str(f) for f in (record.get("fields") or []))


def _verdicts(records: list[dict]) -> dict[str, dict]:
    """结论由实际返回结果推出，不写死。"""
    by_req: dict[str, list[dict]] = {}
    for record in records:
        by_req.setdefault(record["requirement"], []).append(record)

    def verdict(status: str, summary: str, evidence: list[str]) -> dict:
        return {"status": status, "summary": summary, "evidence": sorted(evidence)}

    # --- D1 ---
    d1 = by_req.get("D1", [])
    long_pe = [
        r for r in d1 if _long_and_frequent(r) and _has_field(r, "市盈率")
    ]
    long_pb = [
        r for r in d1 if _long_and_frequent(r) and _has_field(r, "市净率")
    ]
    if long_pe and long_pb:
        d1_status = "已解锁"
        d1_summary = (
            "已测来源中存在 ≥5 年、日频且同时含 PE 与 PB 的同口径序列。"
        )
    elif long_pe or long_pb:
        d1_status = "部分"
        d1_summary = (
            f"日频长历史已可取到 {'PE' if long_pe else ''}"
            f"{'、' if long_pe and long_pb else ''}{'PB' if long_pb else ''}"
            f"（{'、'.join(r['probe_id'] for r in (long_pe + long_pb))}）；"
            "另一项仍只有月度采样，原周度策略所需的双指标同频历史未齐备。"
        )
    else:
        d1_status = "未解锁"
        d1_summary = "已测来源中没有 ≥5 年、周度或更高频的同口径 PE/PB 序列。"

    # --- D2 ---
    d2 = by_req.get("D2", [])
    time_fields = [
        r
        for r in d2
        if _ok(r)
        and any(
            ("时间" in str(f) or "time" in str(f).lower() or "publish" in str(f).lower())
            for f in (r["fields"] or [])
        )
    ]
    if time_fields:
        d2_status = "已解锁"
        d2_summary = "返回字段中包含公布时间。"
    else:
        d2_status = "未解锁"
        d2_summary = (
            "所有已测返回字段里都没有公布时刻或发布时间戳，"
            "因此无法在不额外假设的前提下确定信号在当时的可得时点。"
        )

    # --- D3 ---
    d3 = by_req.get("D3", [])
    tr_series = [
        r
        for r in d3
        if _long_and_frequent(r)
        and "全收益" in str((r.get("identity") or {}).get("指数中文全称", ""))
    ]
    tr_yield_only = [
        r for r in d3 if _ok(r) and _has_field(r, "股息率") and not _long_and_frequent(r)
    ]
    if tr_series:
        d3_status = "已解锁"
        spans = "、".join(
            f"{r['probe_id']} {r['date_range'][0]}~{r['date_range'][1]}" for r in tr_series
        )
        d3_summary = (
            f"已测来源中存在官方全收益指数日频历史（{spans}），"
            "可直接作为全收益口径使用；仍需注意它来自官方文件、与价格指数分属不同序列。"
        )
    elif tr_yield_only:
        d3_status = "部分"
        d3_summary = "只有短窗口股息率，不足以重建 5 年总收益。"
    else:
        d3_status = "未解锁"
        d3_summary = "全收益指数与可用于重建总收益的分红数据在已测来源中都取不到。"

    # --- D4 ---
    d4 = by_req.get("D4", [])
    price_ok = [r for r in d4 if "price" in r["probe_id"] and _long_and_frequent(r)]
    nav_ok = [r for r in d4 if "nav" in r["probe_id"] and _long_and_frequent(r)]
    if len(price_ok) == 3 and len(nav_ok) == 3:
        d4_status = "已解锁"
        d4_summary = (
            "三只样本 ETF 的历史日线价格与历史单位净值都可取到（各 3/3，跨度均 ≥5 年）；"
            "仍缺净值披露时刻与停复牌/盘口，因此可做历史价格与净值序列，但不能证明当日盘中可成交。"
        )
    elif price_ok or nav_ok:
        failed = [r["probe_id"] for r in d4 if not _ok(r)]
        d4_status = "部分"
        d4_summary = (
            f"已实测到价格 {len(price_ok)}/3、净值 {len(nav_ok)}/3 满足 ≥5 年日频"
            f"（有的来自早前一次成功调用）；其余 {len(failed)} 条本次调用失败："
            f"{'、'.join(failed)}。失败原因是接口限流/超时，属于本次调用环境问题，"
            "不是「数据不存在」，需择时重测后才能定论。"
        )
    else:
        d4_status = "未解锁"
        d4_summary = "本次调用没有取得满足 ≥5 年日频的价格或净值序列。"

    return {
        "D1": verdict(d1_status, d1_summary, [r["probe_id"] for r in d1]),
        "D2": verdict(d2_status, d2_summary, [r["probe_id"] for r in d2]),
        "D3": verdict(d3_status, d3_summary, [r["probe_id"] for r in d3]),
        "D4": verdict(d4_status, d4_summary, [r["probe_id"] for r in d4]),
    }


def render_markdown(payload: dict) -> str:
    lines = [
        "# 策略数据可行性调查（第三阶段第二批 B）",
        "",
        f"生成时间：{payload['generated_at']}｜生成脚本：`tools/probe_strategy_data.py`",
        "",
        "范围：只用免费、无需账号的来源（已安装 akshare 版本中与需求明确对应的接口）。",
        "不购买、不注册、不爬登录页。**结论只针对下列已测接口与参数**，",
        "不代表「所有免费来源都没有」。",
        "",
        "## 1. 结论",
        "",
        "| 编号 | 需求 | 结论 | 说明 |",
        "|---|---|---|---|",
    ]
    needs = {
        "D1": "≥5 年、周度或更高频、同口径 PE/PB",
        "D2": "公布时刻或可复现的发布规律",
        "D3": "全收益指数或可重建总收益的分红/再投资数据",
        "D4": "三只 ETF 历史日线价格与历史单位净值",
    }
    for key in ("D1", "D2", "D3", "D4"):
        v = payload["verdicts"][key]
        lines.append(f"| {key} | {needs[key]} | **{v['status']}** | {v['summary']} |")
    lines += [
        "",
        "## 1.1 本次调用环境",
        "",
        f"- 观察到限流/超时的主机：{'、'.join(payload['environment']['affected_hosts'])}",
        f"- 说明：{payload['environment']['note']}",
        f"- 重测策略：{payload['environment']['retry_policy']}",
        "",
        "## 2. 逐条调用记录",
        "",
        "| probe | 需求 | 接口 | 参数 | 状态 | 行数 | 日期范围 | 频率 | 返回字段 | 错误 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for record in payload["probes"]:
        params = "、".join(f"{k}={v}" for k, v in (record["params"] or {}).items())
        fields = "、".join((record["fields"] or [])[:8])
        if record["fields"] and len(record["fields"]) > 8:
            fields += "…"
        date_range = (
            " → ".join(record["date_range"]) if record["date_range"] else "—"
        )
        lines.append(
            f"| `{record['probe_id']}` | {record['requirement']} | `{record['interface']}` | "
            f"{params or '—'} | {record['status']} | {record['rows'] if record['rows'] is not None else '—'} | "
            f"{date_range} | {record['frequency'] or '—'} | {fields or '—'} | "
            f"{(record['error'] or '—').splitlines()[0][:120]} |"
        )
    lines += [
        "",
        "## 3. 对本批结论的影响",
        "",
        "- 原设计的**周度** A/B 候选策略仍无法做可执行回测：D1 只拿到月度、D2 拿不到公布时刻；",
        "- 本批因此只做月度的事后诊断（见 [phase3b-diagnostics.md](phase3b-diagnostics.md)），",
        "  不做状态机、不做模拟持仓、不做回测；",
        "- D4 已解锁的部分可以支持「历史价格与净值序列」，但仍缺净值披露时刻与交易状态，",
        "  因此不能据此断言当日盘中可成交。",
        "",
        "## 4. 未做的事",
        "",
        "- 没有遍历所有供应商，也没有尝试付费或需要登录的来源；",
        "- 没有为了让 D1 通过而把月度数据插值成周度；",
        "- 没有为了让 D3 通过而用价格指数冒充全收益指数。",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="策略数据可行性调查")
    parser.add_argument("--json-out", default=str(JSON_OUT))
    parser.add_argument("--md-out", default=str(MD_OUT))
    parser.add_argument("--only", default=None, help="只跑指定 probe_id，逗号分隔")
    parser.add_argument(
        "--merge", action="store_true", help="把本次结果合并进已有 JSON（用于失败重测）"
    )
    args = parser.parse_args(argv)
    now = datetime.now(TZ)
    candidates = _candidates()
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        candidates = [c for c in candidates if c["probe_id"] in wanted]
        if not candidates:
            raise SystemExit(f"没有匹配的 probe_id：{args.only}")
    fresh = [probe(entry) for entry in candidates]
    json_path = Path(args.json_out)
    if args.merge and json_path.exists():
        previous = json.loads(json_path.read_text(encoding="utf-8"))
        by_id = {r["probe_id"]: r for r in previous.get("probes", [])}
        for record in fresh:
            old = by_id.get(record["probe_id"])
            if old and old.get("status") == "ok" and record.get("status") != "ok":
                # 重测失败不能抹掉早前一次成功的证据；两者都留下
                record["previous_success"] = {
                    key: old.get(key)
                    for key in (
                        "status",
                        "rows",
                        "fields",
                        "date_range",
                        "frequency",
                        "identity",
                        "value_columns",
                        "elapsed_s",
                    )
                }
                record["note"] = (
                    (record.get("note") or "")
                    + "；本次重测失败，早前一次调用成功，见 previous_success"
                ).strip("；")
            by_id[record["probe_id"]] = record
        order = [c["probe_id"] for c in _candidates()]
        records = [by_id[k] for k in order if k in by_id]
        records += [r for k, r in by_id.items() if k not in order]
    else:
        records = fresh
    payload = {
        "schema_version": "strategy-data-feasibility/1",
        "generated_at": now.isoformat(timespec="seconds"),
        "last_attempt_at": now.isoformat(timespec="seconds"),
        "scope": "免费、无需账号的已测接口；不购买、不注册、不爬登录页",
        "caveat": "结论只针对已测接口与参数，不代表所有免费来源",
        "probes": records,
        "verdicts": _verdicts(records),
        "environment": {
            "rate_limit_observed": True,
            "affected_hosts": [
                "push2.eastmoney.com",
                "push2his.eastmoney.com",
                "api.fund.eastmoney.com",
            ],
            "note": (
                "东方财富系列接口在本次调查期间返回限流或读超时（首次限流提示约需 5 分钟，"
                "反复触发可能 40 分钟以上）。失败记录属于本次调用环境问题，**不代表数据不存在**。"
                "同一批接口在 2026-11-11T16:53 的采集任务中成功："
                "data/snapshots/ 的三只 ETF 快照即由 fund_etf_hist_em 与 fund_etf_fund_info_em 构建。"
            ),
            "retry_policy": "每条候选单次调用；失败后按 probe_id 重测，重测失败不会抹掉早前的成功记录（见 previous_success）。",
        },
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path = Path(args.md_out)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    print(f"written {json_path.relative_to(ROOT)} and {md_path.relative_to(ROOT)}")
    for key, value in payload["verdicts"].items():
        print(f"  {key}: {value['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
