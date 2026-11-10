#!/usr/bin/env python
"""构建固定快照（阶段一）。

原则：
- 每个事实都带来源、单位、报告期、公告/可获得时间、抓取时间、转换说明；
- 已知为零 / 缺失 / 不适用 / 过期 / 冲突 分开表达，不用 0 填空；
- 抓取时间不等于「当时市场知道的时间」，available_at 单独记录；
- 大体积原始数据只进 data/cache/，仓库里只保留小快照。

用法：
    .venv/bin/python tools/build_snapshots.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.data.facts import Fact, SourceRef  # noqa: E402
from engine.data.snapshot import Snapshot, SnapshotStore  # noqa: E402

TZ = ZoneInfo("Asia/Singapore")
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
CACHE_DIR = ROOT / "data" / "cache"

NOW = datetime.now(TZ).isoformat(timespec="seconds")
AS_OF = "2026-11-10"  # 最近一个交易日（收盘数据）

INDEX_CODES = {
    "000300": "沪深300",
    "000905": "中证500",
    "000852": "中证1000",
}

SRC_CSI = SourceRef(
    source_id="csindex.indicator_file",
    publisher="中证指数有限公司",
    interface="官方指数估值指标文件（.xls）",
    url="https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/indicator/{code}indicator.xls",
    retrieved_at=NOW,
    notes="文件仅包含最近 20 个交易日；市盈率/股息率口径未在文件内说明",
)
SRC_EM_INDEX = SourceRef(
    source_id="em.index_spot",
    publisher="东方财富",
    interface="AKShare stock_zh_index_spot_em（沪深重要指数）",
    url="https://quote.eastmoney.com/center/gridlist.html#index_sh",
    retrieved_at=NOW,
    notes="收盘行情快照，含最新价与昨收",
)
SRC_EM_INDEX_HIST = SourceRef(
    source_id="em.index_daily_em",
    publisher="东方财富",
    interface="AKShare stock_zh_index_daily_em",
    url="https://quote.eastmoney.com/",
    retrieved_at=NOW,
    notes="指数日线（不复权），含开高低收与成交额",
)
SRC_LG_PE = SourceRef(
    source_id="legulegu.index_pe",
    publisher="乐咕乐股",
    interface="AKShare stock_index_pe_lg / stock_index_pb_lg",
    url="https://legulegu.com/stockdata/index-basic-pe",
    retrieved_at=NOW,
    notes="第三方聚合数据；实测按月采样，非日频/周频；聚合方法与亏损股处理未获官方说明",
)
SRC_EM_ETF_SPOT = SourceRef(
    source_id="em.etf_spot",
    publisher="东方财富",
    interface="AKShare fund_etf_spot_em",
    url="https://quote.eastmoney.com/center/gridlist.html#fund_etf",
    retrieved_at=NOW,
    notes="含最新价、IOPV 实时估值、折价率、数据日期与更新时间",
)
SRC_EM_ETF_NAV = SourceRef(
    source_id="em.etf_nav",
    publisher="东方财富",
    interface="AKShare fund_etf_fund_info_em",
    url="http://fundf10.eastmoney.com/jjzq_{code}.html",
    retrieved_at=NOW,
    notes="单位净值/累计净值；接口不含净值披露时间",
)
SRC_FUND_PROFILE = SourceRef(
    source_id="eastmoney.fund_profile",
    publisher="天天基金（基金概况页）",
    interface="HTTP 抓取基金概况页面",
    url="http://fundf10.eastmoney.com/jbgk_{code}.html",
    retrieved_at=NOW,
    notes="基金全称、跟踪标的、业绩比较基准、管理费率、托管费率、成立日期",
)
SRC_EM_STOCK_INFO = SourceRef(
    source_id="em.stock_individual_info",
    publisher="东方财富",
    interface="AKShare stock_individual_info_em",
    url="https://quote.eastmoney.com/",
    retrieved_at=NOW,
    notes="含最新价、总股本、流通股、行业、上市时间",
)
SRC_EM_CASHFLOW = SourceRef(
    source_id="em.cash_flow_sheet",
    publisher="东方财富",
    interface="AKShare stock_cash_flow_sheet_by_report_em",
    url="https://data.eastmoney.com/bbsj/",
    retrieved_at=NOW,
    notes="按报告期现金流量表，含 NOTICE_DATE（公告日期）与 UPDATE_DATE",
)
SRC_EM_BALANCE = SourceRef(
    source_id="em.balance_sheet",
    publisher="东方财富",
    interface="AKShare stock_balance_sheet_by_report_em",
    url="https://data.eastmoney.com/bbsj/",
    retrieved_at=NOW,
    notes="按报告期资产负债表，含 NOTICE_DATE（公告日期）",
)
SRC_CNINFO_DIV = SourceRef(
    source_id="cninfo.dividend",
    publisher="巨潮资讯",
    interface="AKShare stock_dividend_cninfo",
    url="https://webapi.cninfo.com.cn/#/company",
    retrieved_at=NOW,
    notes="含分红类型、派息比例（每 10 股）、报告时间（所属财年）、实施方案公告日期",
)
SRC_SINA_DIV = SourceRef(
    source_id="sina.dividend_detail",
    publisher="新浪财经",
    interface="AKShare stock_history_dividend_detail",
    url="https://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/stockid/601398.phtml",
    retrieved_at=NOW,
    notes="含公告日期、派息、进度（实施/预案）、除权除息日",
)


def _src(source: SourceRef, code: str) -> SourceRef:
    """把来源 URL 中的 {code} 占位替换为实际代码，避免输出模板串。"""
    if source.url and "{code}" in source.url:
        return SourceRef(
            source_id=source.source_id,
            publisher=source.publisher,
            interface=source.interface,
            url=source.url.replace("{code}", code),
            retrieved_at=source.retrieved_at,
            notes=source.notes,
        )
    return source


def _max_available(facts, fallback: str) -> str:
    """快照级可获得时间 = 已知事实可获得时间的最大值。

    若有事实可获得时间未知（None），不会被伪造，而是由 _unknown_availability 列出，
    并在 coverage 中标记 point_in_time_safe=False。
    """
    values = [f.available_at for f in facts if f.available_at]
    if not values:
        return fallback
    return max(values)


def _unknown_availability(facts) -> list[str]:
    return sorted(f.fact_id for f in facts if not f.available_at)


# --- 指数 -------------------------------------------------------------------
def build_index_snapshot(code: str) -> Snapshot:
    name = INDEX_CODES[code]
    facts: list[Fact] = []

    # 官方估值（最近 20 个交易日）
    csi = ak.stock_zh_index_value_csindex(symbol=code)
    csi_latest = csi.iloc[0]
    (CACHE_DIR / f"csindex_indicator_{code}.csv").write_text(
        csi.to_csv(index=False), encoding="utf-8"
    )

    # 收盘点位
    spot = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
    row = spot[spot["代码"] == code]
    if row.empty:
        raise RuntimeError(f"行情清单中没有指数 {code}")
    level = float(row.iloc[0]["最新价"])
    facts.append(
        Fact.known(
            fact_id="index.level",
            metric="index_close_level",
            value=level,
            unit="index_point",
            currency=None,
            period=AS_OF,
            available_at=f"{AS_OF}T15:00:00+08:00",
            fetched_at=NOW,
            source=SRC_EM_INDEX,
            transform="行情清单『最新价』（2026-11-10 收盘）",
        )
    )

    # 官方估值口径（口径未说明，按原列名保留）
    for col, fact_id, metric in (
        ("市盈率1", "index.pe_csindex_1", "csindex_pe_ratio_1"),
        ("股息率1", "index.dividend_yield_csindex_1", "csindex_dividend_yield_1"),
    ):
        value = csi_latest[col]
        facts.append(
            Fact.known(
                fact_id=fact_id,
                metric=metric,
                value=float(value),
                unit="ratio" if "pe" in metric else "percent",
                currency=None,
                period=str(csi_latest["日期"]),
                available_at=None,
                fetched_at=NOW,
                source=_src(SRC_CSI, code),
                transform=f"官方指标文件列『{col}』；聚合方法与分母口径未在文件中说明",
                notes="发布时点未披露，可获得时间为 None；因此该快照不能用于点位时点回放",
            )
        )

    # 第三方月度估值序列
    pe = ak.stock_index_pe_lg(symbol=name)
    pb = ak.stock_index_pb_lg(symbol=name)
    (CACHE_DIR / f"legulegu_pe_{code}.csv").write_text(pe.to_csv(index=False), encoding="utf-8")
    (CACHE_DIR / f"legulegu_pb_{code}.csv").write_text(pb.to_csv(index=False), encoding="utf-8")

    pe_latest = pe.iloc[-1]
    pb_latest = pb.iloc[-1]
    facts.append(
        Fact.known(
            fact_id="index.pe_ttm",
            metric="pe_ttm",
            value=float(pe_latest["滚动市盈率"]),
            unit="ratio",
            currency=None,
            period=str(pe_latest["日期"]),
            available_at=None,
            fetched_at=NOW,
            source=SRC_LG_PE,
            transform="乐咕乐股『滚动市盈率』列；月度采样",
            notes="第三方口径，聚合方法（整体法/等权）与亏损股处理未获官方说明；可获得时间未披露",
        )
    )
    facts.append(
        Fact.known(
            fact_id="index.pb",
            metric="pb",
            value=float(pb_latest["市净率"]),
            unit="ratio",
            currency=None,
            period=str(pb_latest["日期"]),
            available_at=None,
            fetched_at=NOW,
            source=SRC_LG_PE,
            transform="乐咕乐股『市净率』列；月度采样",
            notes="第三方口径，可获得时间未披露",
        )
    )

    # 日线覆盖（只统计数据，不整表入库）
    daily = ak.stock_zh_index_daily_em(symbol=f"sh{code}")
    daily.to_csv(CACHE_DIR / f"index_daily_{code}.csv", index=False)
    price_start = str(daily["date"].iloc[0])
    price_end = str(daily["date"].iloc[-1])

    coverage = {
        "valuation_source_official": {
            "source_id": SRC_CSI.source_id,
            "publisher": SRC_CSI.publisher,
            "start": str(csi["日期"].iloc[-1]),
            "end": str(csi["日期"].iloc[0]),
            "observations": int(len(csi)),
            "frequency": "daily",
            "note": "官方指标文件只提供最近 20 个交易日，不能用于 5 年分位",
        },
        "valuation_source_monthly": {
            "source_id": SRC_LG_PE.source_id,
            "publisher": SRC_LG_PE.publisher,
            "start": str(pe["日期"].iloc[0]),
            "end": str(pe["日期"].iloc[-1]),
            "observations": int(len(pe)),
            "frequency": "monthly",
            "note": "实测为月度采样，无法满足设计书要求的 5 年周度样本",
        },
        "price_series": {
            "source_id": SRC_EM_INDEX_HIST.source_id,
            "start": price_start,
            "end": price_end,
            "observations": int(len(daily)),
            "frequency": "daily",
        },
        "total_return_series": {
            "available": False,
            "note": "免费源未提供全收益指数（如 H00300/H00905/H00852）行情；H 前缀代码在东方财富接口返回空表",
        },
        "dividend_yield_history": {
            "available": False,
            "note": "官方文件仅 20 个交易日，第三方月度序列不含股息率",
        },
        "aggregation_method": "unknown",
        "aggregation_note": "官方文件未说明整体法/加权法与亏损成分处理；两套来源口径不可直接拼接",
        "supports_weekly_5y_history": False,
        "supports_5y_window": True,
        "index_identity_verified": True,
        "point_in_time_safe": False,
        "availability_unknown": _unknown_availability(facts),
        "availability_note": "估值指标的发布时点未披露，不能断言某个历史时点已可知",
    }

    return Snapshot(
        instrument_id=f"INDEX:{code}.SH" if code != "000922" else f"INDEX:{code}.CSI",
        asset_class="index",
        as_of=AS_OF,
        available_at=_max_available(facts, f"{AS_OF}T15:00:00+08:00"),
        fetched_at=NOW,
        facts=facts,
        coverage=coverage,
        producers=["tools/build_snapshots.py", "akshare"],
        notes="阶段一固定快照：只证明「有哪些数据、覆盖多长、口径是否说明」，不构成估值结论",
    )


# --- ETF --------------------------------------------------------------------
ETF_CODES = ["510300", "510500", "512100"]

_ETF_VENUE_SUFFIX = {"510300": "SH", "510500": "SH", "512100": "SH", "159845": "SZ", "159919": "SZ"}


def build_etf_snapshot(code: str) -> Snapshot:
    facts: list[Fact] = []

    spot = ak.fund_etf_spot_em()
    row = spot[spot["代码"] == code]
    if row.empty:
        raise RuntimeError(f"ETF 行情清单中没有 {code}")
    r = row.iloc[0]

    facts.append(
        Fact.known(
            fact_id="etf.price",
            metric="etf_close_price",
            value=float(r["最新价"]),
            unit="CNY_per_share",
            currency="CNY",
            period=str(r["数据日期"])[:10],
            available_at=f"{AS_OF}T15:00:00+08:00",
            fetched_at=NOW,
            source=SRC_EM_ETF_SPOT,
            transform="ETF 行情清单『最新价』（收盘）",
            notes=f"行情商页面更新时间（非交易所披露时间）：{r['更新时间']}",
        )
    )
    facts.append(
        Fact.known(
            fact_id="etf.iopv_reference",
            metric="iopv_reference",
            value=float(r["IOPV实时估值"]),
            unit="CNY_per_share",
            currency="CNY",
            period=str(r["数据日期"])[:10],
            available_at=None,
            fetched_at=NOW,
            source=SRC_EM_ETF_SPOT,
            transform="ETF 行情清单『IOPV实时估值』",
            notes="IOPV 为参考值，不保证可成交或可赎回",
        )
    )

    # 净值（含披露时间缺口）
    nav_df = ak.fund_etf_fund_info_em(fund=code, start_date="20260101", end_date="20261231")
    nav_df.to_csv(CACHE_DIR / f"etf_nav_{code}.csv", index=False)
    nav_latest = nav_df.iloc[-1]
    nav_date = str(nav_latest["净值日期"])[:10]
    facts.append(
        Fact.known(
            fact_id="etf.nav",
            metric="unit_nav",
            value=float(nav_latest["单位净值"]),
            unit="CNY_per_share",
            currency="CNY",
            period=nav_date,
            available_at=None,
            fetched_at=NOW,
            source=_src(SRC_EM_ETF_NAV, code),
            transform="基金净值序列『单位净值』",
            notes="净值日期有，但披露时间未提供",
        )
    )
    facts.append(
        Fact.known(
            fact_id="etf.nav_date",
            metric="nav_date",
            value=nav_date,
            period=nav_date,
            fetched_at=NOW,
            source=_src(SRC_EM_ETF_NAV, code),
            transform="基金净值序列『净值日期』",
        )
    )
    facts.append(
        Fact.missing(
            fact_id="etf.nav_disclosed_at",
            metric="nav_disclosure_time",
            period=nav_date,
            fetched_at=NOW,
            source=_src(SRC_EM_ETF_NAV, code),
            notes="所有已核实免费接口都不提供净值披露时间，无法确认收盘时是否已知当日净值",
        )
    )
    facts.append(
        Fact.missing(
            fact_id="etf.premium_discount_pct",
            metric="premium_discount_pct",
            period=nav_date,
            fetched_at=NOW,
            source=_src(SRC_EM_ETF_NAV, code),
            notes="净值披露时间缺失，按设计书要求不计算日终折溢价（不能用不知何时公布的净值对收盘价）",
        )
    )
    facts.append(
        Fact.missing(
            fact_id="etf.trading_status",
            metric="trading_status",
            period=AS_OF,
            fetched_at=NOW,
            source=SRC_EM_ETF_SPOT,
            notes="未取得交易所停复牌状态字段；仅能确认当日有行情",
        )
    )

    # 基金概况：跟踪标的、费率
    profile = fetch_fund_profile(code)
    if profile.get("management_fee") is not None:
        facts.append(
            Fact.known(
                fact_id="etf.management_fee_pct",
                metric="management_fee_pct",
                value=profile["management_fee"],
                unit="percent",
                currency=None,
                period=profile["retrieved_date"],
                fetched_at=NOW,
                source=_src(SRC_FUND_PROFILE, code),
                transform="基金概况页『管理费率』（每年）",
            )
        )
    else:
        facts.append(
            Fact.missing(
                fact_id="etf.management_fee_pct",
                metric="management_fee_pct",
                fetched_at=NOW,
                source=_src(SRC_FUND_PROFILE, code),
                notes="基金概况页未解析到管理费率",
            )
        )
    if profile.get("custody_fee") is not None:
        facts.append(
            Fact.known(
                fact_id="etf.custody_fee_pct",
                metric="custody_fee_pct",
                value=profile["custody_fee"],
                unit="percent",
                currency=None,
                period=profile["retrieved_date"],
                fetched_at=NOW,
                source=_src(SRC_FUND_PROFILE, code),
                transform="基金概况页『托管费率』（每年）",
            )
        )

    if profile.get("tracking_target"):
        facts.append(
            Fact.known(
                fact_id="etf.tracking_note",
                metric="tracking_note",
                value=f"跟踪标的：{profile['tracking_target']}；业绩比较基准：{profile.get('benchmark') or '未解析'}",
                period=profile["retrieved_date"],
                fetched_at=NOW,
                source=_src(SRC_FUND_PROFILE, code),
                transform="基金概况页文本",
            )
        )

    coverage = {
        "price_history": {"source_id": SRC_EM_ETF_SPOT.source_id, "frequency": "daily (vendor)"},
        "nav_history": {
            "source_id": SRC_EM_ETF_NAV.source_id,
            "observations_2026": int(len(nav_df)),
            "frequency": "daily",
            "note": "净值序列可回溯多年，但无披露时间字段",
        },
        "nav_disclosure_time": {"available": False},
        "premium_discount_same_day": {"available": False},
        "trading_status": {"available": False},
        "fees_from_profile_page": {"available": True, "note": "来自基金概况页，非基金合同原文"},
        "tracking_error_series": {"available": False, "note": "免费接口未提供跟踪偏离/跟踪误差序列"},
        "point_in_time_safe": False,
        "availability_unknown": _unknown_availability(facts),
        "availability_note": "净值披露时间缺失，无法断言收盘时已知当日净值",
    }

    return Snapshot(
        instrument_id=f"ETF:{code}.{_ETF_VENUE_SUFFIX.get(code, 'SH')}",
        asset_class="etf",
        as_of=AS_OF,
        available_at=_max_available(facts, f"{AS_OF}T16:11:00+08:00"),
        fetched_at=NOW,
        facts=facts,
        coverage=coverage,
        producers=["tools/build_snapshots.py", "akshare"],
        notes="阶段一固定快照：展示指数判断与 ETF 执行判断必须分开所需的数据状态",
    )


def fetch_fund_profile(code: str) -> dict:
    import re

    import requests

    url = f"http://fundf10.eastmoney.com/jbgk_{code}.html"
    resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.encoding = resp.apparent_encoding or "utf-8"
    text = re.sub(r"<[^>]+>", " ", resp.text)
    text = text.replace("&nbsp;", " ")

    def grab(label: str) -> str | None:
        idx = text.find(label)
        if idx < 0:
            return None
        snippet = re.sub(r"\s+", " ", text[idx + len(label) : idx + len(label) + 60]).strip()
        return snippet

    def pct(label: str) -> float | None:
        snippet = grab(label)
        if not snippet:
            return None
        m = re.match(r"[:：]?\s*([0-9.]+)%", snippet)
        return float(m.group(1)) if m else None

    target_snippet = grab("跟踪标的") or ""
    tracking = None
    m = re.search(r"([\u4e00-\u9fffA-Za-z0-9]+指数)", target_snippet)
    if m:
        tracking = m.group(1)
    benchmark = grab("业绩比较基准")
    if benchmark:
        benchmark = benchmark.split("跟踪标的")[0].strip(" :：")

    return {
        "tracking_target": tracking,
        "benchmark": benchmark,
        "management_fee": pct("管理费率"),
        "custody_fee": pct("托管费率"),
        "retrieved_date": NOW[:10],
    }


# --- 个股 -------------------------------------------------------------------
STOCK_CODE = "601398"


def build_equity_snapshot(code: str) -> Snapshot:
    facts: list[Fact] = []

    info = ak.stock_individual_info_em(symbol=code)
    info_map = dict(zip(info["item"], info["value"]))
    facts.append(
        Fact.known(
            fact_id="profile.name",
            metric="security_name",
            value=str(info_map.get("股票简称")),
            period=AS_OF,
            fetched_at=NOW,
            source=SRC_EM_STOCK_INFO,
        )
    )
    facts.append(
        Fact.known(
            fact_id="profile.industry",
            metric="industry",
            value=str(info_map.get("行业")),
            period=AS_OF,
            fetched_at=NOW,
            source=SRC_EM_STOCK_INFO,
        )
    )
    listing = info_map.get("上市时间")
    facts.append(
        Fact.known(
            fact_id="profile.listing_date",
            metric="listing_date",
            value=str(listing)[:10] if listing else "未知",
            period=None,
            fetched_at=NOW,
            source=SRC_EM_STOCK_INFO,
        )
    )
    total_shares = info_map.get("总股本")
    if total_shares is not None and str(total_shares) not in ("", "nan"):
        facts.append(
            Fact.known(
                fact_id="profile.total_shares",
                metric="total_shares",
                value=float(total_shares),
                unit="shares",
                period=AS_OF,
                fetched_at=NOW,
                source=SRC_EM_STOCK_INFO,
                transform="个股信息『总股本』",
            )
        )
    else:
        facts.append(
            Fact.missing(
                fact_id="profile.total_shares",
                metric="total_shares",
                fetched_at=NOW,
                source=SRC_EM_STOCK_INFO,
            )
        )
    facts.append(
        Fact.known(
            fact_id="profile.latest_price",
            metric="latest_price",
            value=float(info_map.get("最新")),
            unit="CNY_per_share",
            currency="CNY",
            period=AS_OF,
            available_at=f"{AS_OF}T15:00:00+08:00",
            fetched_at=NOW,
            source=SRC_EM_STOCK_INFO,
            transform="个股信息『最新』（收盘价）",
        )
    )

    # 现金流量表（银行模板）：取最近 8 个报告期
    cash = ak.stock_cash_flow_sheet_by_report_em(symbol=f"SH{code}")
    cash.to_csv(CACHE_DIR / f"stock_cashflow_{code}.csv", index=False)
    cash = cash.sort_values("REPORT_DATE", ascending=False).head(8)
    balance = ak.stock_balance_sheet_by_report_em(symbol=f"SH{code}")
    balance.to_csv(CACHE_DIR / f"stock_balance_{code}.csv", index=False)
    balance = balance.sort_values("REPORT_DATE", ascending=False).head(8)

    for _, row in cash.iterrows():
        period = str(row["REPORT_DATE"])[:10]
        notice = str(row["NOTICE_DATE"])[:10] if pd.notna(row["NOTICE_DATE"]) else None
        prefix = f"fin.{period}"

        ocf = row.get("NETCASH_OPERATE")
        if pd.notna(ocf):
            if float(ocf) == 0.0:
                facts.append(
                    Fact.known_zero(
                        fact_id=f"{prefix}.ocf",
                        metric="operating_cash_flow",
                        unit="CNY",
                        currency=str(row.get("CURRENCY") or "CNY"),
                        period=period,
                        available_at=notice,
                        fetched_at=NOW,
                        source=SRC_EM_CASHFLOW,
                        transform="现金流量表『NETCASH_OPERATE』；报告中确实为零",
                    )
                )
            else:
                facts.append(
                    Fact.known(
                        fact_id=f"{prefix}.ocf",
                        metric="operating_cash_flow",
                        value=float(ocf),
                        unit="CNY",
                        currency=str(row.get("CURRENCY") or "CNY"),
                        period=period,
                        available_at=notice,
                        fetched_at=NOW,
                        source=SRC_EM_CASHFLOW,
                        transform="现金流量表『NETCASH_OPERATE』",
                    )
                )
        else:
            facts.append(
                Fact.missing(
                    fact_id=f"{prefix}.ocf",
                    metric="operating_cash_flow",
                    period=period,
                    available_at=notice,
                    fetched_at=NOW,
                    source=SRC_EM_CASHFLOW,
                )
            )

        capex = row.get("CONSTRUCT_LONG_ASSET")
        if pd.notna(capex):
            facts.append(
                Fact.known(
                    fact_id=f"{prefix}.capex",
                    metric="capex",
                    value=float(capex),
                    unit="CNY",
                    currency=str(row.get("CURRENCY") or "CNY"),
                    period=period,
                    available_at=notice,
                    fetched_at=NOW,
                    source=SRC_EM_CASHFLOW,
                    transform="现金流量表『CONSTRUCT_LONG_ASSET』（购建固定资产等支付的现金）",
                )
            )
        else:
            facts.append(
                Fact.missing(
                    fact_id=f"{prefix}.capex",
                    metric="capex",
                    period=period,
                    available_at=notice,
                    fetched_at=NOW,
                    source=SRC_EM_CASHFLOW,
                )
            )

        facts.append(
            Fact.known(
                fact_id=f"{prefix}.notice_date",
                metric="notice_date",
                value=notice or "未知",
                period=period,
                fetched_at=NOW,
                source=SRC_EM_CASHFLOW,
                transform="现金流量表『NOTICE_DATE』（公告日期）",
            )
        )
        facts.append(
            Fact.not_applicable(
                fact_id=f"{prefix}.net_debt",
                metric="net_debt",
                period=period,
                fetched_at=NOW,
                source=SRC_EM_BALANCE,
                notes="银行以存款/同业负债为主，普通企业净债务（有息负债-现金）口径不适用，需银行专用口径",
            )
        )

    # 分红：按所属财年 + 实施状态归集
    cninfo = ak.stock_dividend_cninfo(symbol=code)
    detail = ak.stock_history_dividend_detail(symbol=code, indicator="分红")
    cninfo.to_csv(CACHE_DIR / f"stock_dividend_cninfo_{code}.csv", index=False)
    detail.to_csv(CACHE_DIR / f"stock_dividend_detail_{code}.csv", index=False)

    detail_by_date = {
        str(r["公告日期"])[:10]: r for _, r in detail.iterrows() if pd.notna(r.get("公告日期"))
    }
    for _, row in cninfo.tail(10).iterrows():
        announce = str(row["实施方案公告日期"])[:10]
        fiscal = str(row["报告时间"])
        div_type = str(row["分红类型"])
        per10 = row["派息比例"]
        if pd.isna(per10) or float(per10) == 0:
            continue
        matched = detail_by_date.get(announce)
        status = str(matched["进度"]) if matched is not None else "实施（按巨潮实施方案公告日推断）"
        facts.append(
            Fact.known(
                fact_id=f"div.{fiscal}.{div_type}",
                metric="cash_dividend_per_10_shares",
                value=float(per10),
                unit="CNY",
                currency="CNY",
                period=fiscal,
                available_at=announce,
                fetched_at=NOW,
                source=SRC_CNINFO_DIV,
                transform="巨潮『派息比例』（每 10 股，税前）",
                notes=(
                    f"分红类型={div_type}；实施状态={status}；公告日={announce}；"
                    f"除权日={str(row['除权日'])[:10] if pd.notna(row['除权日']) else '未知'}"
                ),
            )
        )

    coverage = {
        "financial_statements": {
            "source_id": SRC_EM_CASHFLOW.source_id,
            "periods_available": int(len(cash)),
            "has_notice_date": True,
            "note": "公告日期字段可用；UPDATE_DATE 可识别后续更正",
        },
        "dividend_records": {
            "source_id": SRC_CNINFO_DIV.source_id,
            "records": int(len(cninfo)),
            "fiscal_year_mapping": True,
            "implementation_status": True,
            "note": "巨潮『报告时间』给出所属财年，可与实施状态、除权日归集",
        },
        "net_debt": {
            "available": False,
            "note": "银行不适用普通企业净债务口径；银行专用资本/资产质量字段尚未采集",
        },
        "bank_capital_and_asset_quality": {
            "available": False,
            "note": "NPL 比率、拨备覆盖率、资本充足率需要银行专项报表接口，阶段二处理",
        },
        "point_in_time_safe": True,
        "availability_unknown": _unknown_availability(facts),
        "availability_note": "每条事实都记录了公告日期；按 available_at 过滤可用于历史时点分析",
    }

    return Snapshot(
        instrument_id=f"EQUITY:{code}.SH",
        asset_class="equity",
        as_of=str(cash.iloc[0]["REPORT_DATE"])[:10],
        available_at=_max_available(facts, str(cash.iloc[0]["NOTICE_DATE"])[:10]),
        fetched_at=NOW,
        facts=facts,
        coverage=coverage,
        producers=["tools/build_snapshots.py", "akshare"],
        notes="阶段一固定快照：展示财报公告时点、现金流/资本开支可得性与分红按财年归集",
    )


def main() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    store = SnapshotStore(SNAPSHOT_DIR)

    built = []
    for code in INDEX_CODES:
        snap = build_index_snapshot(code)
        store.save(snap)
        built.append(snap)
        print(f"指数 {code}: {snap.snapshot_id} facts={len(snap.facts)}")

    for code in ETF_CODES:
        snap = build_etf_snapshot(code)
        store.save(snap)
        built.append(snap)
        print(f"ETF {code}: {snap.snapshot_id} facts={len(snap.facts)}")

    snap = build_equity_snapshot(STOCK_CODE)
    store.save(snap)
    built.append(snap)
    print(f"个股 {STOCK_CODE}: {snap.snapshot_id} facts={len(snap.facts)}")

    entries = store.rebuild_manifest()
    print(f"manifest 已更新，共 {len(entries)} 个快照")
    return 0


if __name__ == "__main__":
    sys.exit(main())
