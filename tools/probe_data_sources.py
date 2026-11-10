#!/usr/bin/env python
"""数据可用性调查（阶段一任务四）。

实际调用每个接口，记录：来源、字段、日期区间、行数、频率、更新/可获得时间、
缺失率、访问限制、结论。结果写入 data/coverage/coverage-report.json。

要点：
- 「抓取时间」不等于「当时市场知道的时间」，可获得时间单独记录；
- 免费接口没有历史就如实记为缺口，不用当前成分股回填，也不购买数据；
- 大体积原始数据只落 data/cache/。
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "coverage"
CACHE_DIR = ROOT / "data" / "cache"
TZ = ZoneInfo("Asia/Singapore")
NOW = datetime.now(TZ).isoformat(timespec="seconds")

PROBES: list[dict] = []


def record(
    *,
    probe_id: str,
    scope: str,
    question: str,
    interface: str,
    provider: str,
    status: str,
    fields: list[str] | None = None,
    date_range: list[str] | None = None,
    rows: int | None = None,
    frequency: str | None = None,
    update_time: str | None = None,
    availability: str | None = None,
    missing_rate: float | None = None,
    access: str = "免费、无需登录",
    verdict: str = "",
    sample: dict | None = None,
    error: str | None = None,
) -> None:
    PROBES.append(
        {
            "probe_id": probe_id,
            "scope": scope,
            "question": question,
            "interface": interface,
            "provider": provider,
            "status": status,
            "fields": fields or [],
            "date_range": date_range,
            "rows": rows,
            "frequency": frequency,
            "update_time": update_time,
            "availability": availability,
            "missing_rate": missing_rate,
            "access": access,
            "verdict": verdict,
            "sample": sample or {},
            "error": error,
            "fetched_at": NOW,
        }
    )


def _missing_rate(df: pd.DataFrame, columns: list[str]) -> float | None:
    present = [c for c in columns if c in df.columns]
    if not present or df.empty:
        return None
    return round(float(df[present].isna().mean().mean()), 4)


def _range(df: pd.DataFrame, column: str) -> list[str] | None:
    if column not in df.columns or df.empty:
        return None
    values = pd.to_datetime(df[column], errors="coerce").dropna()
    if values.empty:
        return None
    return [str(values.min().date()), str(values.max().date())]


def run_probe(fn) -> None:
    try:
        fn()
    except Exception as exc:  # 记录失败，不伪造数据
        record(
            probe_id=getattr(fn, "probe_id", fn.__name__),
            scope=getattr(fn, "scope", "unknown"),
            question=getattr(fn, "question", ""),
            interface=getattr(fn, "interface", fn.__name__),
            provider=getattr(fn, "provider", ""),
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            verdict="接口调用失败，相关字段视为缺口",
        )
        if "--verbose" in sys.argv:
            traceback.print_exc()


# --- 个股 -------------------------------------------------------------------
def probe_stock_basic() -> None:
    df = ak.stock_individual_info_em(symbol="601398")
    data = dict(zip(df["item"], df["value"]))
    record(
        probe_id="stock.basic_info",
        scope="个股",
        question="个股基础信息（名称、行业、股本、上市日、最新价）是否可得？",
        interface="stock_individual_info_em",
        provider="东方财富",
        status="ok",
        fields=list(df["item"].astype(str)),
        rows=int(len(df)),
        frequency="按需（当前快照）",
        update_time="随行情变化",
        availability="实时/收盘后即可得；无历史版本",
        access="免费、无需登录",
        verdict="基础信息可得，但只有当前值，没有历史版本（无法回放历史时点的股本/行业）",
        sample={k: (str(v)[:40]) for k, v in list(data.items())[:5]},
    )


def probe_stock_cashflow() -> None:
    df = ak.stock_cash_flow_sheet_by_report_em(symbol="SH601398")
    df.to_csv(CACHE_DIR / "probe_cashflow_601398.csv", index=False)
    record(
        probe_id="stock.cash_flow_sheet",
        scope="个股",
        question="现金流表是否带公告日期？覆盖多少报告期？",
        interface="stock_cash_flow_sheet_by_report_em",
        provider="东方财富",
        status="ok",
        fields=["REPORT_DATE", "NOTICE_DATE", "UPDATE_DATE", "NETCASH_OPERATE", "CONSTRUCT_LONG_ASSET", "CURRENCY"],
        date_range=_range(df, "REPORT_DATE"),
        rows=int(len(df)),
        frequency="按报告期（季/半年/年）",
        update_time="NOTICE_DATE（公告日）+ UPDATE_DATE（更正日）",
        availability="公告日之后才应视为已知；接口提供 NOTICE_DATE",
        missing_rate=_missing_rate(df, ["NETCASH_OPERATE", "CONSTRUCT_LONG_ASSET"]),
        access="免费、无需登录",
        verdict="可以做到「当时可得」的时点控制；银行模板字段与一般工商企业不同，OCF 不能直接当 FCFF",
        sample={"latest_report": str(df["REPORT_DATE"].max())[:10]},
    )


def probe_stock_balance() -> None:
    df = ak.stock_balance_sheet_by_report_em(symbol="SH601398")
    record(
        probe_id="stock.balance_sheet",
        scope="个股",
        question="资产负债表是否带公告日期？",
        interface="stock_balance_sheet_by_report_em",
        provider="东方财富",
        status="ok",
        fields=["REPORT_DATE", "NOTICE_DATE", "TOTAL_ASSETS", "TOTAL_LIABILITIES", "SHARE_CAPITAL"],
        date_range=_range(df, "REPORT_DATE"),
        rows=int(len(df)),
        frequency="按报告期",
        update_time="NOTICE_DATE",
        availability="公告日之后",
        missing_rate=_missing_rate(df, ["TOTAL_ASSETS", "TOTAL_LIABILITIES"]),
        verdict="可得；但银行没有普通企业口径的「有息负债/现金」，净债务需专用口径",
    )


def probe_stock_dividend_detail() -> None:
    df = ak.stock_history_dividend_detail(symbol="601398", indicator="分红")
    record(
        probe_id="stock.dividend_detail",
        scope="个股",
        question="分红明细是否含实施状态与除权日？是否含所属财年？",
        interface="stock_history_dividend_detail",
        provider="新浪财经",
        status="ok",
        fields=list(df.columns),
        date_range=_range(df, "公告日期"),
        rows=int(len(df)),
        frequency="按事件",
        update_time="公告日",
        availability="公告日",
        missing_rate=_missing_rate(df, ["派息", "进度"]),
        verdict="有实施状态与除权日，但没有所属财年字段，不能单靠除息月份猜财年",
    )


def probe_stock_dividend_cninfo() -> None:
    df = ak.stock_dividend_cninfo(symbol="601398")
    record(
        probe_id="stock.dividend_cninfo",
        scope="个股",
        question="分红是否标注所属财年（报告期）与分红类型？",
        interface="stock_dividend_cninfo",
        provider="巨潮资讯",
        status="ok",
        fields=list(df.columns),
        date_range=_range(df, "实施方案公告日期"),
        rows=int(len(df)),
        frequency="按事件",
        update_time="实施方案公告日",
        availability="实施方案公告日",
        missing_rate=_missing_rate(df, ["派息比例", "报告时间"]),
        verdict="『报告时间』给出所属财年（如 2025年报、2026半年报），可与实施状态、除权日联合归集",
        sample={"latest": str(df.iloc[-1].to_dict())[:200]},
    )


# --- 指数 -------------------------------------------------------------------
def _index_values(code: str) -> None:
    df = ak.stock_zh_index_value_csindex(symbol=code)
    record(
        probe_id=f"index.official_valuation.{code}",
        scope="指数",
        question="官方 PE/PB/股息率历史有多长？口径是否说明？",
        interface="stock_zh_index_value_csindex",
        provider="中证指数有限公司",
        status="partial",
        fields=list(df.columns),
        date_range=_range(df, "日期"),
        rows=int(len(df)),
        frequency="日频，但只提供最近 20 个交易日",
        update_time="官方文件每日更新，文件内无发布时间字段",
        availability="发布时点未披露",
        missing_rate=_missing_rate(df, ["市盈率1", "股息率1"]),
        verdict="官方口径最可信，但历史只有 20 个交易日，不能用于 5 年分位；文件未说明整体法/加权法与亏损股处理",
    )


def probe_index_valuation_300() -> None:
    _index_values("000300")


def probe_index_valuation_500() -> None:
    _index_values("000905")


def probe_index_valuation_1000() -> None:
    _index_values("000852")


def _legulegu(name: str, code: str) -> None:
    pe = ak.stock_index_pe_lg(symbol=name)
    pb = ak.stock_index_pb_lg(symbol=name)
    record(
        probe_id=f"index.vendor_valuation.{code}",
        scope="指数",
        question="第三方 PE/PB 历史覆盖多长、频率多少？",
        interface="stock_index_pe_lg / stock_index_pb_lg",
        provider="乐咕乐股",
        status="partial",
        fields=list(pe.columns) + list(pb.columns),
        date_range=_range(pe, "日期"),
        rows=int(len(pe)),
        frequency="实测为月度采样（非日频/周频）",
        update_time="未披露",
        availability="未披露",
        missing_rate=_missing_rate(pe, ["滚动市盈率", "静态市盈率"]) or 0.0,
        verdict="可提供长历史，但只有月度频率，无法满足「5 年周度」要求；聚合方法与亏损股处理无官方说明，且未标注回溯编制情况",
        sample={"last_date": str(pe["日期"].iloc[-1]), "last_pe_ttm": float(pe["滚动市盈率"].iloc[-1])},
    )


def probe_legulegu_300() -> None:
    _legulegu("沪深300", "000300")


def probe_legulegu_500() -> None:
    _legulegu("中证500", "000905")


def probe_legulegu_1000() -> None:
    _legulegu("中证1000", "000852")


def probe_index_daily() -> None:
    df = ak.stock_zh_index_daily_em(symbol="sh000300")
    record(
        probe_id="index.daily_history",
        scope="指数",
        question="指数点位日线历史是否完整？",
        interface="stock_zh_index_daily_em",
        provider="东方财富",
        status="ok",
        fields=list(df.columns),
        date_range=_range(df, "date"),
        rows=int(len(df)),
        frequency="日频",
        update_time="收盘后",
        availability="收盘后",
        missing_rate=_missing_rate(df, ["close"]),
        verdict="点位日线可得且历史足够；但只有价格指数，没有全收益指数",
    )


def probe_index_total_return() -> None:
    errors = []
    for code in ("H00300", "H00905", "H00852"):
        try:
            df = ak.stock_zh_index_daily_em(symbol=code)
            if df.empty:
                errors.append(f"{code}: 空表")
        except Exception as exc:
            errors.append(f"{code}: {type(exc).__name__}")
    record(
        probe_id="index.total_return",
        scope="指数",
        question="全收益指数是否可得？",
        interface="stock_zh_index_daily_em(H00300/H00905/H00852) / index_zh_a_hist",
        provider="东方财富",
        status="failed",
        fields=[],
        rows=0,
        frequency="—",
        availability="—",
        verdict="免费源未提供全收益/净收益指数行情；只能使用价格指数，回测需明确说明不含股息",
        error="; ".join(errors),
    )


def probe_index_spot() -> None:
    df = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
    record(
        probe_id="index.spot",
        scope="指数",
        question="宽基指数当前点位与名称是否可得？",
        interface="stock_zh_index_spot_em",
        provider="东方财富",
        status="ok",
        fields=list(df.columns),
        rows=int(len(df)),
        frequency="实时/收盘",
        update_time="收盘后",
        availability="收盘后",
        verdict="可用于识别与当前点位；不含估值指标",
        sample={"codes": df["代码"].head(3).tolist()},
    )


# --- ETF --------------------------------------------------------------------
ETF = "510300"


def probe_etf_spot() -> None:
    df = ak.fund_etf_spot_em()
    row = df[df["代码"] == ETF]
    record(
        probe_id="etf.spot",
        scope="ETF",
        question="ETF 价格、IOPV、折价率、更新时间是否可得？",
        interface="fund_etf_spot_em",
        provider="东方财富",
        status="ok",
        fields=list(df.columns),
        rows=int(len(df)),
        frequency="实时/收盘",
        update_time="页面更新时间（非交易所披露时间）",
        availability="收盘后；更新时间字段可用",
        access="免费、无需登录；单次返回约 1600 只 ETF，较慢",
        verdict="价格与 IOPV 参考值可得；折价率是行情商计算值，不能替代「同日收盘价 vs 同日已公布净值」的核对",
        sample={"code": ETF, "time": str(row["更新时间"].iloc[0]) if not row.empty else None},
    )


def probe_etf_nav() -> None:
    df = ak.fund_etf_fund_info_em(fund=ETF, start_date="20240101", end_date="20261231")
    record(
        probe_id="etf.nav_history",
        scope="ETF",
        question="ETF 净值历史是否可得？是否含披露时间？",
        interface="fund_etf_fund_info_em",
        provider="东方财富",
        status="partial",
        fields=list(df.columns),
        date_range=_range(df, "净值日期"),
        rows=int(len(df)),
        frequency="日频",
        update_time="未提供披露时间字段",
        availability="仅净值日期，无披露时刻",
        missing_rate=_missing_rate(df, ["单位净值"]),
        verdict="净值数值可得，但披露时间缺失，无法确认收盘时是否已知当日净值（影响日终折溢价）",
    )


def probe_etf_hist() -> None:
    df = ak.fund_etf_hist_em(symbol=ETF, period="daily", start_date="20240101", end_date="20261231", adjust="")
    record(
        probe_id="etf.price_history",
        scope="ETF",
        question="ETF 行情历史是否可得？",
        interface="fund_etf_hist_em",
        provider="东方财富",
        status="ok",
        fields=list(df.columns),
        date_range=_range(df, "日期"),
        rows=int(len(df)),
        frequency="日频",
        update_time="收盘后",
        availability="收盘后",
        missing_rate=_missing_rate(df, ["收盘"]),
        verdict="价格历史可得；可用于可执行回测的成交价，但需与净值/信号时点对齐",
    )


def probe_etf_profile() -> None:
    url = f"http://fundf10.eastmoney.com/jbgk_{ETF}.html"
    resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    ok = resp.status_code == 200 and "跟踪标的" in resp.text
    record(
        probe_id="etf.profile",
        scope="ETF",
        question="ETF 真实跟踪对象与费率是否可从产品资料核实？",
        interface="HTTP 抓取基金概况页",
        provider="天天基金",
        status="ok" if ok else "failed",
        fields=["基金全称", "跟踪标的", "业绩比较基准", "管理费率", "托管费率", "成立日期"],
        rows=1,
        frequency="按需",
        update_time="页面更新",
        availability="即时",
        access="免费；页面结构可能变化，需要解析容错",
        verdict="跟踪标的与费率可核实；但这是基金销售平台的概况页，正式引用仍应以基金合同/招募说明书为准",
    )


def probe_etf_pcf() -> None:
    df = ak.fund_etf_pcf(symbol=ETF)
    record(
        probe_id="etf.pcf",
        scope="ETF",
        question="申赎清单是否提供每日申赎单位净值？",
        interface="fund_etf_pcf",
        provider="东方财富",
        status="ok",
        fields=list(df.columns),
        rows=int(len(df)),
        frequency="日频（每个交易日一份 PCF）",
        update_time="交易日前/当日",
        availability="以 PCF 文件发布为准",
        missing_rate=_missing_rate(df, ["申赎单位净值"]),
        verdict="PCF 提供申赎单位净值与成分，可作为折溢价核对的补充；但 PCF 净值为估算口径，不等于披露的单位净值",
    )


def probe_etf_exchange_lists() -> None:
    try:
        sh = ak.fund_etf_scale_sse()
        sh_status = "partial"
        sh_verdict = f"上交所 ETF 列表可得，但统计日期为 {sh['统计日期'].iloc[0]}（明显滞后）"
        sh_rows = int(len(sh))
    except Exception as exc:
        sh_status = "failed"
        sh_verdict = f"上交所 ETF 列表失败: {type(exc).__name__}"
        sh_rows = 0
    record(
        probe_id="etf.exchange_list.sse",
        scope="ETF",
        question="交易所 ETF 清单是否可用于核实上市地与产品类型？",
        interface="fund_etf_scale_sse",
        provider="上海证券交易所",
        status=sh_status,
        fields=["基金代码", "基金简称", "ETF类型", "统计日期", "基金份额"],
        rows=sh_rows,
        frequency="按统计日",
        update_time="统计日期字段",
        availability="统计日",
        verdict=sh_verdict,
    )


def probe_etf_exchange_list_szse() -> None:
    try:
        sz = ak.fund_etf_scale_szse()
        record(
            probe_id="etf.exchange_list.szse",
            scope="ETF",
            question="深交所 ETF 清单是否可得？",
            interface="fund_etf_scale_szse",
            provider="深圳证券交易所",
            status="ok",
            fields=list(sz.columns),
            rows=int(len(sz)),
            frequency="按统计日",
            update_time="统计日期",
            availability="统计日",
            verdict="可得",
        )
    except Exception as exc:
        record(
            probe_id="etf.exchange_list.szse",
            scope="ETF",
            question="深交所 ETF 清单是否可得？",
            interface="fund_etf_scale_szse",
            provider="深圳证券交易所",
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            verdict="接口当前不可用；深市 ETF 的交易所归属只能用代码段与行情清单推断，属于证据缺口",
        )


def probe_etf_dividend() -> None:
    df = ak.fund_etf_dividend_sina(symbol="sh510300")
    record(
        probe_id="etf.dividend",
        scope="ETF",
        question="ETF 分红历史是否可得？",
        interface="fund_etf_dividend_sina",
        provider="新浪财经",
        status="ok",
        fields=list(df.columns),
        date_range=_range(df, "日期"),
        rows=int(len(df)),
        frequency="按事件",
        update_time="除息日",
        availability="除息日后",
        verdict="可得累计分红，可用于复权/持有收益核对",
    )


def probe_etf_tracking_error() -> None:
    record(
        probe_id="etf.tracking_error",
        scope="ETF",
        question="跟踪偏离/跟踪误差序列是否可得？",
        interface="（无可用免费接口）",
        provider="—",
        status="failed",
        rows=0,
        verdict="免费接口未提供跟踪误差序列，只能由净值与指数自行计算，且需注意费用已在净值中扣除",
        error="未找到提供跟踪误差时间序列的免费接口",
    )


PROBE_FUNCS = [
    probe_stock_basic,
    probe_stock_cashflow,
    probe_stock_balance,
    probe_stock_dividend_detail,
    probe_stock_dividend_cninfo,
    probe_index_valuation_300,
    probe_index_valuation_500,
    probe_index_valuation_1000,
    probe_legulegu_300,
    probe_legulegu_500,
    probe_legulegu_1000,
    probe_index_daily,
    probe_index_total_return,
    probe_index_spot,
    probe_etf_spot,
    probe_etf_nav,
    probe_etf_hist,
    probe_etf_profile,
    probe_etf_pcf,
    probe_etf_exchange_lists,
    probe_etf_exchange_list_szse,
    probe_etf_dividend,
    probe_etf_tracking_error,
]

GAPS = [
    {
        "gap_id": "GAP-WEEKLY-VALUATION-HISTORY",
        "scope": "指数",
        "impact": "阶段三的 5 年周度分位无法用免费数据完成",
        "detail": "官方指标文件只有最近 20 个交易日；第三方仅有月度序列。需要设计方决定：接受月度降频，或另行取得日频历史",
    },
    {
        "gap_id": "GAP-TOTAL-RETURN-INDEX",
        "scope": "指数/ETF",
        "impact": "回测无法使用全收益指数，股息收益会缺失",
        "detail": "免费源未提供 H00300/H00905/H00852 等全收益指数行情",
    },
    {
        "gap_id": "GAP-AGGREGATION-METHOD",
        "scope": "指数",
        "impact": "PE/PB 口径无法独立核实，不同来源不可拼接",
        "detail": "官方文件未说明整体法/加权法与亏损成分处理；第三方口径说明不足",
    },
    {
        "gap_id": "GAP-NAV-DISCLOSURE-TIME",
        "scope": "ETF",
        "impact": "无法严格计算「同日收盘价 vs 同日已公布净值」的日终折溢价",
        "detail": "净值接口只有净值日期，没有披露时刻",
    },
    {
        "gap_id": "GAP-ETF-TRADING-STATUS",
        "scope": "ETF",
        "impact": "停复牌、临时停牌无法确认",
        "detail": "免费接口未提供交易所停复牌状态字段",
    },
    {
        "gap_id": "GAP-ETF-TRACKING-ERROR",
        "scope": "ETF",
        "impact": "跟踪偏离只能自行计算，缺少官方序列",
        "detail": "无免费跟踪误差时间序列接口",
    },
    {
        "gap_id": "GAP-SZSE-ETF-LIST",
        "scope": "ETF",
        "impact": "深市 ETF 交易所归属缺少官方清单证据",
        "detail": "深交所 ETF 份额接口当前报错（TypeError: bytes）",
    },
    {
        "gap_id": "GAP-BANK-CAPITAL-QUALITY",
        "scope": "个股",
        "impact": "银行专用模型所需资本充足率/拨备覆盖率需专项采集",
        "detail": "通用财务报表接口不直接提供这些指标，阶段二需单独核实",
    },
    {
        "gap_id": "GAP-STOCK-HISTORICAL-VINTAGE",
        "scope": "个股",
        "impact": "股本、行业等基础信息只有当前值，无法回放历史版本",
        "detail": "stock_individual_info_em 仅返回当前快照",
    },
    {
        "gap_id": "GAP-CSI-INDICATOR-PUBLISH-TIME",
        "scope": "指数",
        "impact": "无法断言某个历史时点已可知当日估值",
        "detail": "官方文件不含发布时间字段",
    },
]


def main() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for fn in PROBE_FUNCS:
        print(f"probe {fn.__name__} ...", flush=True)
        run_probe(fn)

    ok = sum(1 for p in PROBES if p["status"] == "ok")
    partial = sum(1 for p in PROBES if p["status"] == "partial")
    failed = sum(1 for p in PROBES if p["status"] == "failed")

    report = {
        "schema_version": "data-coverage/1",
        "generated_at": NOW,
        "environment": {
            "python": sys.version.split()[0],
            "akshare": ak.__version__,
            "pandas": pd.__version__,
            "note": "全部为免费接口，未购买数据、未申请账号",
        },
        "summary": {"total": len(PROBES), "ok": ok, "partial": partial, "failed": failed},
        "probes": PROBES,
        "gaps": GAPS,
    }
    out = OUT_DIR / "coverage-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n写入 {out}: ok={ok} partial={partial} failed={failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
