#!/usr/bin/env python
"""构建固定快照（阶段一，返工后版本）。

本版修复的核心问题：**不再用「写死的旧日期 + 实时最新价」拼快照**。

构建模式：
1. 先从行情序列里取「实际市场日期」，而不是先假定一个日期；
2. 用该日期的收盘行，而不是实时最新价；
3. 校验：行情日期必须等于预期日期（可显式传 --as-of）；不符就失败，不倒填；
4. 若最新一根是当日盘中未收盘的数据，标记 bar_status=intraday，且不声称收盘可得；
5. 每条事实都记录可获得时间与精度（second / date / unknown）。

大体积原始数据只进 data/cache/，仓库里只保留小快照。

用法：
    .venv/bin/python tools/build_snapshots.py                 # 取最近已收盘交易日
    .venv/bin/python tools/build_snapshots.py --as-of 2026-11-10
    .venv/bin/python tools/build_snapshots.py --only index
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
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
MARKET_CLOSE = (15, 0)  # 收盘时间，按交易所收盘口径

INDEX_CODES = {"000300": "沪深300", "000905": "中证500", "000852": "中证1000"}
ETF_CODES = ["510300", "510500", "512100"]
_ETF_VENUE_SUFFIX = {
    "510300": "SH",
    "510500": "SH",
    "512100": "SH",
    "159845": "SZ",
    "159919": "SZ",
    "159915": "SZ",
}
STOCK_CODE = "601398"


class BuildError(RuntimeError):
    """采集结果与预期时点不一致，或数据不足以构建可审计快照。"""


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _src(source: SourceRef, code: str) -> SourceRef:
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


def _make_sources(code: str) -> dict[str, SourceRef]:
    now = _now()
    return {
        "csi": SourceRef(
            source_id="csindex.indicator_file",
            publisher="中证指数有限公司",
            interface="官方指数估值指标文件（.xls）",
            url=(
                "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/"
                f"autofile/indicator/{code}indicator.xls"
            ),
            retrieved_at=now,
            notes="文件仅包含最近 20 个交易日；市盈率/股息率口径未在文件内说明",
        ),
        "legulegu": SourceRef(
            source_id="legulegu.index_pe",
            publisher="乐咕乐股",
            interface="AKShare stock_index_pe_lg / stock_index_pb_lg",
            url="https://legulegu.com/stockdata/index-basic-pe",
            retrieved_at=now,
            notes="第三方聚合数据；实测按月采样；聚合方法与亏损股处理未获官方说明",
        ),
        "index_daily": SourceRef(
            source_id="em.index_daily_em",
            publisher="东方财富",
            interface="AKShare stock_zh_index_daily_em",
            url="https://quote.eastmoney.com/",
            retrieved_at=now,
            notes="指数日线（不复权），含开高低收与成交额",
        ),
        "etf_hist": SourceRef(
            source_id="em.etf_hist",
            publisher="东方财富",
            interface="AKShare fund_etf_hist_em",
            url="https://quote.eastmoney.com/",
            retrieved_at=now,
            notes="ETF 日线行情，用于取已收盘的收盘价",
        ),
        "etf_spot": SourceRef(
            source_id="em.etf_spot",
            publisher="东方财富",
            interface="AKShare fund_etf_spot_em",
            url="https://quote.eastmoney.com/center/gridlist.html#fund_etf",
            retrieved_at=now,
            notes="含最新价、IOPV 实时估值、数据日期与更新时间",
        ),
        "etf_nav": SourceRef(
            source_id="em.etf_nav",
            publisher="东方财富",
            interface="AKShare fund_etf_fund_info_em",
            url=f"http://fundf10.eastmoney.com/jjzq_{code}.html",
            retrieved_at=now,
            notes="单位净值/累计净值；接口不含净值披露时间",
        ),
        "fund_profile": SourceRef(
            source_id="eastmoney.fund_profile",
            publisher="天天基金（基金概况页）",
            interface="HTTP 抓取基金概况页面",
            url=f"http://fundf10.eastmoney.com/jbgk_{code}.html",
            retrieved_at=now,
            notes="基金全称、跟踪标的、业绩比较基准、管理费率、托管费率、成立日期",
        ),
        "stock_info": SourceRef(
            source_id="em.stock_individual_info",
            publisher="东方财富",
            interface="AKShare stock_individual_info_em",
            url="https://quote.eastmoney.com/",
            retrieved_at=now,
            notes="含总股本、流通股、行业、上市时间",
        ),
        "cash": SourceRef(
            source_id="em.cash_flow_sheet",
            publisher="东方财富",
            interface="AKShare stock_cash_flow_sheet_by_report_em",
            url="https://data.eastmoney.com/bbsj/",
            retrieved_at=now,
            notes="按报告期现金流量表，含 NOTICE_DATE（公告日期）与 UPDATE_DATE",
        ),
        "balance": SourceRef(
            source_id="em.balance_sheet",
            publisher="东方财富",
            interface="AKShare stock_balance_sheet_by_report_em",
            url="https://data.eastmoney.com/bbsj/",
            retrieved_at=now,
            notes="按报告期资产负债表，含 NOTICE_DATE（公告日期）",
        ),
        "dividend": SourceRef(
            source_id="cninfo.dividend",
            publisher="巨潮资讯",
            interface="AKShare stock_dividend_cninfo",
            url="https://webapi.cninfo.com.cn/#/company",
            retrieved_at=now,
            notes="含分红类型、派息比例（每 10 股）、报告时间（所属财年）、实施方案公告日期",
        ),
        "dividend_detail": SourceRef(
            source_id="sina.dividend_detail",
            publisher="新浪财经",
            interface="AKShare stock_history_dividend_detail",
            url=(
                "https://vip.stock.finance.sina.com.cn/corp/go.php/vISSUE_ShareBonus/"
                f"stockid/{code}.phtml"
            ),
            retrieved_at=now,
            notes="含公告日期、派息、进度（实施/预案）、除权除息日",
        ),
    }


# --- 时间与行情校验 ---------------------------------------------------------
def _as_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def _bar_status(market_date: date, now: datetime) -> str:
    """判断最后一根行情是否已收盘。"""
    today = now.date()
    if market_date > today:
        raise BuildError(
            f"行情日期 {market_date} 晚于当前日期 {today}：数据异常，拒绝构建快照"
        )
    if market_date < today:
        return "closed"
    close_hour, close_minute = MARKET_CLOSE
    if (now.hour, now.minute) >= (close_hour + 1, close_minute):
        return "closed"
    return "intraday"


def _assert_expected(market_date: str, expected: str | None, label: str) -> None:
    if expected is not None and market_date != expected:
        raise BuildError(
            f"{label} 实际市场日期为 {market_date}，与预期 {expected} 不一致："
            "不做倒填，请确认 --as-of 或数据源"
        )


def _select_bar(df: pd.DataFrame, date_column: str, expected: str | None, label: str):
    """选出要使用的那一根行情。

    - 给了 --as-of：**按日期选行**（历史取数必须落到具体那一行），找不到即失败；
    - 没给：取最后一行，但会记录它是不是当日未收盘的 bar。
    返回 (截断后的 DataFrame, 市场日期, 被丢弃的后续行数)。
    """
    dates = [_as_date(v) for v in df[date_column]]
    if expected is not None:
        targets = [i for i, day in enumerate(dates) if day.isoformat() == expected]
        if not targets:
            raise BuildError(
                f"{label} 中没有 {expected} 这一行：不做倒填，请确认 --as-of 或数据源"
            )
        index = targets[0]
    else:
        index = len(dates) - 1
    dropped = len(dates) - index - 1
    selected = df.iloc[: index + 1].reset_index(drop=True)
    return selected, dates[index], dropped


def _normalize_date_text(value) -> str:
    """把 20061027 / 2006-10-27 统一成 YYYY-MM-DD；无法识别时原样返回。"""
    text = str(value)[:10] if value is not None else "未知"
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return text


def _availability_counts(facts: list[Fact]) -> dict[str, int]:
    counts = {"second": 0, "date": 0, "unknown": 0}
    for fact in facts:
        precision = fact.availability_precision
        counts[precision or "unknown"] += 1
    return counts


def _unknown_availability(facts: list[Fact]) -> list[str]:
    return sorted(f.fact_id for f in facts if not f.available_at)


def _point_in_time_block(facts: list[Fact]) -> dict:
    counts = _availability_counts(facts)
    if counts["unknown"] and (counts["second"] or counts["date"]):
        granularity = "mixed"
    elif counts["unknown"]:
        granularity = "unknown"
    elif counts["date"] and counts["second"]:
        granularity = "mixed"
    elif counts["date"]:
        granularity = "day"
    else:
        granularity = "second"
    return {
        "point_in_time_safe": counts["unknown"] == 0 and counts["date"] == 0,
        "point_in_time_granularity": granularity,
        "availability_precision_counts": counts,
        "availability_unknown": _unknown_availability(facts),
        "availability_note": (
            "只有全部事实都带秒级可获得时间，才标记 point_in_time_safe=true；"
            "仅日期精度按「当日结束时才可知」处理，不用于盘中历史判断。"
        ),
    }


# --- 指数 -------------------------------------------------------------------
def build_index_snapshot(code: str, *, expected_as_of: str | None = None, now: datetime | None = None) -> Snapshot:
    now = now or datetime.now(TZ)
    src = _make_sources(code)
    name = INDEX_CODES[code]
    facts: list[Fact] = []

    # 1) 市场日期以日线最后一根为准，而不是假定日期
    daily = ak.stock_zh_index_daily_em(symbol=f"sh{code}")
    if daily.empty:
        raise BuildError(f"{code} 日线为空，无法确定市场日期")
    daily.to_csv(CACHE_DIR / f"index_daily_{code}.csv", index=False)
    daily, market_date, dropped = _select_bar(daily, "date", expected_as_of, f"{code} 日线")
    market_date_str = market_date.isoformat()
    status = _bar_status(market_date, now)

    close = float(daily["close"].iloc[-1])
    facts.append(
        Fact.known(
            fact_id="index.level",
            metric="index_close_level",
            value=close,
            unit="index_point",
            currency=None,
            period=market_date_str,
            available_at=(
                f"{market_date_str}T15:00:00+08:00" if status == "closed" else None
            ),
            fetched_at=now.isoformat(timespec="seconds"),
            source=src["index_daily"],
            transform=f"日线最后一行收盘价（bar_status={status}）",
            notes=(
                "已收盘交易日的收盘点位"
                if status == "closed"
                else "最新一根为当日未收盘行情，不能标为收盘价，可获得时间记为未知"
            ),
        )
    )

    # 2) 官方估值：必须覆盖同一市场日期
    csi = ak.stock_zh_index_value_csindex(symbol=code)
    csi.to_csv(CACHE_DIR / f"csindex_indicator_{code}.csv", index=False)
    csi_dates = [_as_date(v) for v in csi["日期"]]
    matching = [i for i, day in enumerate(csi_dates) if day == market_date]
    if not matching:
        raise BuildError(
            f"{code} 官方估值文件中没有 {market_date} 这一行：不做跨日拼接"
        )
    csi_latest = csi.iloc[matching[0]]
    for column, fact_id, metric, unit in (
        ("市盈率1", "index.pe_csindex_1", "csindex_pe_ratio_1", "ratio"),
        ("股息率1", "index.dividend_yield_csindex_1", "csindex_dividend_yield_1", "percent"),
    ):
        facts.append(
            Fact.known(
                fact_id=fact_id,
                metric=metric,
                value=float(csi_latest[column]),
                unit=unit,
                currency=None,
                period=market_date_str,
                available_at=None,
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["csi"],
                transform=f"官方指标文件列『{column}』；聚合方法与分母口径未在文件中说明",
                notes="发布时点未披露，可获得时间记为未知；该事实不参与盘中历史判断",
            )
        )

    # 3) 第三方月度序列：只取与市场日期同名的那一行，缺少就记缺失
    pe = ak.stock_index_pe_lg(symbol=name)
    pb = ak.stock_index_pb_lg(symbol=name)
    pe.to_csv(CACHE_DIR / f"legulegu_pe_{code}.csv", index=False)
    pb.to_csv(CACHE_DIR / f"legulegu_pb_{code}.csv", index=False)

    def _pick(series: pd.DataFrame, column: str) -> float | None:
        dates = [_as_date(v) for v in series["日期"]]
        for idx, day in enumerate(dates):
            if day == market_date:
                return float(series[column].iloc[idx])
        return None

    pe_value = _pick(pe, "滚动市盈率")
    pb_value = _pick(pb, "市净率")
    if pe_value is None:
        facts.append(
            Fact.missing(
                fact_id="index.pe_ttm",
                metric="pe_ttm",
                period=market_date_str,
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["legulegu"],
                notes=f"第三方月度序列中没有 {market_date_str} 的观测，不用相邻日期回填",
            )
        )
    else:
        facts.append(
            Fact.known(
                fact_id="index.pe_ttm",
                metric="pe_ttm",
                value=pe_value,
                unit="ratio",
                currency=None,
                period=market_date_str,
                available_at=None,
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["legulegu"],
                transform="乐咕乐股『滚动市盈率』列",
                notes="第三方口径，聚合方法与亏损股处理未获说明；可获得时间未披露",
            )
        )
    if pb_value is not None:
        facts.append(
            Fact.known(
                fact_id="index.pb",
                metric="pb",
                value=pb_value,
                unit="ratio",
                currency=None,
                period=market_date_str,
                available_at=None,
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["legulegu"],
                transform="乐咕乐股『市净率』列",
                notes="第三方口径，可获得时间未披露",
            )
        )

    coverage = {
        "build": {
            "mode": "historical_close_bar",
            "market_date": market_date_str,
            "bar_status": status,
            "built_at": now.isoformat(timespec="seconds"),
            "date_source": "按日期选行" if expected_as_of else "日线最后一行",
            "rows_after_market_date_dropped": dropped,
        },
        "valuation_source_official": {
            "source_id": src["csi"].source_id,
            "publisher": src["csi"].publisher,
            "start": csi_dates[-1].isoformat(),
            "end": csi_dates[0].isoformat(),
            "observations": int(len(csi)),
            "frequency": "daily",
            "note": "官方指标文件只提供最近 20 个交易日，不能用于 5 年分位",
        },
        "valuation_source_monthly": {
            "source_id": src["legulegu"].source_id,
            "publisher": src["legulegu"].publisher,
            "start": _as_date(pe["日期"].iloc[0]).isoformat(),
            "end": _as_date(pe["日期"].iloc[-1]).isoformat(),
            "observations": int(len(pe)),
            "frequency": "monthly",
            "note": "该缓存实测为月度采样，不足以构成连续周度样本；不能据此断言所有来源均无周度数据",
        },
        "price_series": {
            "source_id": src["index_daily"].source_id,
            "start": _as_date(daily["date"].iloc[0]).isoformat(),
            "end": market_date_str,
            "observations": int(len(daily)),
            "frequency": "daily",
        },
        "total_return_series": {
            "available": False,
            "note": "本次未取得全收益指数行情；该结论只针对已测来源与参数，不代表所有免费来源",
        },
        "dividend_yield_history": {
            "available": False,
            "note": "官方文件仅覆盖 20 个交易日，第三方月度序列不含股息率",
        },
        "aggregation_method": "unknown",
        "aggregation_note": "官方文件未说明整体法/加权法与亏损成分处理；两套来源口径不可直接拼接",
        "supports_weekly_5y_history": False,
        "supports_5y_window": True,
        "index_identity_verified": True,
        **_point_in_time_block(facts),
    }

    return Snapshot(
        instrument_id=f"INDEX:{code}.SH",
        asset_class="index",
        as_of=market_date_str,
        available_at=(
            f"{market_date_str}T15:00:00+08:00"
            if status == "closed"
            else now.isoformat(timespec="seconds")
        ),
        fetched_at=now.isoformat(timespec="seconds"),
        facts=facts,
        coverage=coverage,
        producers=["tools/build_snapshots.py", "akshare"],
        notes=(
            "阶段一固定快照：市场日期取自日线最后一根，"
            f"bar_status={status}；不构成估值结论"
        ),
    )


# --- ETF --------------------------------------------------------------------
def fetch_fund_profile(code: str, *, now: datetime) -> dict:
    """抓取基金概况页。

    失败时返回带 error 的空结果，由调用方记为 missing，
    而不是让整批快照构建失败或伪造费率。
    """
    import re
    import time

    import requests

    url = f"http://fundf10.eastmoney.com/jbgk_{code}.html"
    last_error: Exception | None = None
    resp = None
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                break
            last_error = RuntimeError(f"HTTP {resp.status_code}")
        except Exception as exc:  # 网络抖动/超时
            last_error = exc
            resp = None
            time.sleep(1.5 * (attempt + 1))
    if resp is None or resp.status_code != 200:
        return {
            "tracking_target": None,
            "benchmark": None,
            "management_fee": None,
            "custody_fee": None,
            "retrieved_at": now.isoformat(timespec="seconds"),
            "error": f"{type(last_error).__name__}: {last_error}",
        }

    resp.encoding = resp.apparent_encoding or "utf-8"
    text = re.sub(r"<[^>]+>", " ", resp.text)
    text = text.replace("&nbsp;", " ")

    def grab(label: str) -> str | None:
        idx = text.find(label)
        if idx < 0:
            return None
        return re.sub(r"\s+", " ", text[idx + len(label) : idx + len(label) + 60]).strip()

    def pct(label: str) -> float | None:
        snippet = grab(label)
        if not snippet:
            return None
        match = re.match(r"[:：]?\s*([0-9.]+)%", snippet)
        return float(match.group(1)) if match else None

    target_snippet = grab("跟踪标的") or ""
    match = re.search(r"([\u4e00-\u9fffA-Za-z0-9]+指数)", target_snippet)
    benchmark = grab("业绩比较基准")
    if benchmark:
        benchmark = benchmark.split("跟踪标的")[0].strip(" :：")

    return {
        "tracking_target": match.group(1) if match else None,
        "benchmark": benchmark,
        "management_fee": pct("管理费率"),
        "custody_fee": pct("托管费率"),
        "retrieved_at": now.isoformat(timespec="seconds"),
        "error": None,
    }


def build_etf_snapshot(
    code: str,
    *,
    expected_as_of: str | None = None,
    now: datetime | None = None,
    hist_frame: pd.DataFrame | None = None,
    price_source: SourceRef | None = None,
) -> Snapshot:
    """构建 ETF 快照。

    `hist_frame`/`price_source` 只用于行情接口不可用时的等价替代源：
    调用方必须显式传入源信息，快照里会如实写成该源，不能伪装成东财数据。
    """
    now = now or datetime.now(TZ)
    src = _make_sources(code)
    price_src = price_source or src["etf_hist"]
    facts: list[Fact] = []

    if hist_frame is None:
        hist = ak.fund_etf_hist_em(
            symbol=code, period="daily", start_date="20240101", end_date="20301231", adjust=""
        )
    else:
        hist = hist_frame.copy()
    if hist.empty:
        raise BuildError(f"{code} 行情为空，无法确定市场日期")
    hist.to_csv(CACHE_DIR / f"etf_hist_{code}.csv", index=False)
    hist, market_date, dropped = _select_bar(hist, "日期", expected_as_of, f"{code} 日线")
    market_date_str = market_date.isoformat()
    status = _bar_status(market_date, now)
    close = float(hist["收盘"].iloc[-1])

    facts.append(
        Fact.known(
            fact_id="etf.price",
            metric="etf_close_price",
            value=close,
            unit="CNY_per_share",
            currency="CNY",
            period=market_date_str,
            available_at=(
                f"{market_date_str}T15:00:00+08:00" if status == "closed" else None
            ),
            fetched_at=now.isoformat(timespec="seconds"),
            source=price_src,
            transform=f"ETF 日线最后一行收盘价（bar_status={status}）",
            notes=(
                "已收盘交易日的收盘价"
                if status == "closed"
                else "当日未收盘行情，不标为收盘价"
            ),
        )
    )

    # 2) 成交额与成交量（第三阶段：产品资料完整性检查需要）
    stamp = now.isoformat(timespec="seconds")
    for column, fact_id, metric, unit in (
        ("成交额", "etf.turnover_cny", "turnover_amount", "CNY"),
        ("成交量", "etf.volume_shares", "turnover_volume", "shares"),
    ):
        raw = hist[column].iloc[-1] if column in hist.columns else None
        if raw is None or pd.isna(raw):
            facts.append(
                Fact.missing(
                    fact_id=fact_id,
                    metric=metric,
                    period=market_date_str,
                    fetched_at=stamp,
                    source=price_src,
                    notes=f"行情接口未返回『{column}』",
                )
            )
        else:
            facts.append(
                Fact.known(
                    fact_id=fact_id,
                    metric=metric,
                    value=float(raw),
                    unit=unit,
                    currency="CNY" if unit == "CNY" else None,
                    period=market_date_str,
                    available_at=(
                        f"{market_date_str}T15:00:00+08:00" if status == "closed" else None
                    ),
                    fetched_at=stamp,
                    source=price_src,
                    transform=f"不复权日线『{column}』（bar_status={status}）",
                    notes="已收盘交易日的日成交数据" if status == "closed" else "当日未收盘，成交数据不完整",
                )
            )

    # 近 20 个交易日平均成交额：必须按实际取得的有效天数报告，不足 20 天不标成 20 日均值
    if "成交额" in hist.columns:
        window = hist["成交额"].dropna().tail(20)
        days_used = int(len(window))
        if days_used == 0:
            facts.append(
                Fact.missing(
                    fact_id="etf.turnover_avg_20d_cny",
                    metric="turnover_avg_20d",
                    period=market_date_str,
                    fetched_at=stamp,
                    source=price_src,
                    notes="没有可用的成交额观测",
                )
            )
        else:
            facts.append(
                Fact.known(
                    fact_id="etf.turnover_avg_20d_cny",
                    metric="turnover_avg_20d",
                    value=float(window.mean()),
                    unit="CNY",
                    currency="CNY",
                    period=market_date_str,
                    available_at=(
                        f"{market_date_str}T15:00:00+08:00" if status == "closed" else None
                    ),
                    fetched_at=stamp,
                    source=price_src,
                    transform=f"末尾 {days_used} 个交易日成交额算术平均（最多取 20 个）",
                    notes=f"实际使用 {days_used} 个交易日；不足 20 天时不称『20 日均值』",
                )
            )
            facts.append(
                Fact.known(
                    fact_id="etf.turnover_avg_days_used",
                    metric="turnover_avg_days_used",
                    value=days_used,
                    unit="count",
                    period=market_date_str,
                    fetched_at=stamp,
                    source=price_src,
                    transform="参与平均的实际交易日数",
                )
            )

    facts.append(
        Fact.missing(
            fact_id="etf.bid_ask_spread_cny",
            metric="bid_ask_spread",
            period=market_date_str,
            fetched_at=stamp,
            source=price_src,
            notes="日线行情不含买卖盘口，未取得价差；不因有收盘价就断言可交易",
        )
    )

    nav_df = ak.fund_etf_fund_info_em(fund=code, start_date="20260101", end_date="20261231")
    nav_df.to_csv(CACHE_DIR / f"etf_nav_{code}.csv", index=False)
    if not nav_df.empty:
        # 快照 as_of 可能早于最新净值日（例如按前一交易日重建）：只用不晚于
        # 快照日期的净值，避免把未来净值塞进「截至某日」的快照。
        nav_df = nav_df.copy()
        nav_df["_nav_date"] = [_as_date(value) for value in nav_df["净值日期"]]
        visible = nav_df[nav_df["_nav_date"] <= market_date]
        if not visible.empty:
            nav_df = visible
        nav_df = nav_df.sort_values("_nav_date").drop(columns=["_nav_date"])
        nav_latest = nav_df.iloc[-1]
        nav_date = _as_date(nav_latest["净值日期"])
        facts.append(
            Fact.known(
                fact_id="etf.nav",
                metric="unit_nav",
                value=float(nav_latest["单位净值"]),
                unit="CNY_per_share",
                currency="CNY",
                period=nav_date.isoformat(),
                available_at=None,
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["etf_nav"],
                transform="基金净值序列『单位净值』",
                notes="净值日期存在，但披露时间未提供，可获得时间记为未知",
            )
        )
        facts.append(
            Fact.known(
                fact_id="etf.nav_date",
                metric="nav_date",
                value=nav_date.isoformat(),
                period=nav_date.isoformat(),
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["etf_nav"],
                transform="基金净值序列『净值日期』",
            )
        )
        facts.append(
            Fact.missing(
                fact_id="etf.nav_disclosed_at",
                metric="nav_disclosure_time",
                period=nav_date.isoformat(),
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["etf_nav"],
                notes="所有已核实免费接口都不提供净值披露时间，无法确认收盘时是否已知当日净值",
            )
        )

        # 事后同日折溢价：价格与净值对应同一估值日期且都已实际取得时才计算
        nav_value = float(nav_latest["单位净值"])
        if nav_date.isoformat() == market_date_str and nav_value > 0 and status == "closed":
            facts.append(
                Fact.known(
                    fact_id="etf.premium_discount_same_day_pct",
                    metric="same_day_premium_discount_pct",
                    value=(close / nav_value - 1.0) * 100.0,
                    unit="percent",
                    currency=None,
                    period=market_date_str,
                    available_at=None,
                    fetched_at=now.isoformat(timespec="seconds"),
                    source=src["etf_nav"],
                    transform="事后同日折溢价 = 市场收盘价 / 同日单位净值 − 1",
                    notes=(
                        "价格与净值对应同一估值日期；净值披露时刻未知，"
                        "不能据此证明当日收盘前已可交易，也不用于回测的当日成交"
                    ),
                )
            )
        else:
            reason_parts = []
            if nav_date.isoformat() != market_date_str:
                reason_parts.append(f"净值日期 {nav_date.isoformat()} 与价格日期 {market_date_str} 不一致")
            if nav_value <= 0:
                reason_parts.append(f"单位净值 {nav_value} 非正")
            if status != "closed":
                reason_parts.append("价格不是已收盘价")
            facts.append(
                Fact.missing(
                    fact_id="etf.premium_discount_same_day_pct",
                    metric="same_day_premium_discount_pct",
                    period=market_date_str,
                    fetched_at=now.isoformat(timespec="seconds"),
                    source=src["etf_nav"],
                    notes="不计算同日折溢价：" + "；".join(reason_parts),
                )
            )

        for column, fact_id, metric in (
            ("申购状态", "etf.subscription_status", "subscription_status"),
            ("赎回状态", "etf.redemption_status", "redemption_status"),
        ):
            if column in nav_df.columns:
                text = str(nav_latest[column])
                if text and text not in ("nan", "None", "--"):
                    facts.append(
                        Fact.known(
                            fact_id=fact_id,
                            metric=metric,
                            value=text,
                            period=nav_date.isoformat(),
                            fetched_at=now.isoformat(timespec="seconds"),
                            source=src["etf_nav"],
                            transform=f"基金净值表『{column}』",
                            notes="申购/赎回状态，不等于二级市场交易状态（停复牌另行核查）",
                        )
                    )
    else:
        facts.append(
            Fact.missing(
                fact_id="etf.nav",
                metric="unit_nav",
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["etf_nav"],
                notes="净值序列为空",
            )
        )

    facts.append(
        Fact.missing(
            fact_id="etf.premium_discount_pct",
            metric="premium_discount_pct",
            period=market_date_str,
            fetched_at=now.isoformat(timespec="seconds"),
            source=src["etf_nav"],
            notes="净值披露时间缺失，按设计书要求不计算日终折溢价",
        )
    )
    facts.append(
        Fact.missing(
            fact_id="etf.trading_status",
            metric="trading_status",
            period=market_date_str,
            fetched_at=now.isoformat(timespec="seconds"),
            source=price_src,
            notes="未取得交易所停复牌状态字段；仅能确认当日有行情",
        )
    )

    profile = fetch_fund_profile(code, now=now)
    if profile.get("management_fee") is not None:
        facts.append(
            Fact.known(
                fact_id="etf.management_fee_pct",
                metric="management_fee_pct",
                value=profile["management_fee"],
                unit="percent",
                currency=None,
                period=profile["retrieved_at"][:10],
                available_at=None,
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["fund_profile"],
                transform="基金概况页『管理费率』（每年）",
                notes="费率原文未标注生效日，可获得时间记为未知",
            )
        )
    else:
        facts.append(
            Fact.missing(
                fact_id="etf.management_fee_pct",
                metric="management_fee_pct",
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["fund_profile"],
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
                period=profile["retrieved_at"][:10],
                available_at=None,
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["fund_profile"],
                transform="基金概况页『托管费率』（每年）",
            )
        )
    if profile.get("tracking_target"):
        facts.append(
            Fact.known(
                fact_id="etf.tracking_note",
                metric="tracking_note",
                value=(
                    f"跟踪标的：{profile['tracking_target']}；"
                    f"业绩比较基准：{profile.get('benchmark') or '未解析'}"
                ),
                period=profile["retrieved_at"][:10],
                available_at=None,
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["fund_profile"],
                transform="基金概况页文本",
                notes="来自基金销售平台概况页，正式引用应以基金合同为准",
            )
        )

    coverage = {
        "build": {
            "mode": "historical_close_bar",
            "market_date": market_date_str,
            "bar_status": status,
            "built_at": now.isoformat(timespec="seconds"),
            "date_source": "按日期选行" if expected_as_of else "ETF 日线最后一行",
            "rows_after_market_date_dropped": dropped,
        },
        "price_history": {"source_id": price_src.source_id, "frequency": "daily"},
        "nav_history": {
            "source_id": src["etf_nav"].source_id,
            "observations_2026": int(len(nav_df)),
            "frequency": "daily",
            "note": "净值序列可回溯多年，但无披露时间字段",
        },
        "nav_disclosure_time": {"available": False},
        "premium_discount_same_day": {"available": False},
        "trading_status": {"available": False},
        "fees_from_profile_page": {
            "available": profile.get("error") is None,
            "note": (
                "来自基金概况页，非基金合同原文"
                if profile.get("error") is None
                else f"概况页抓取失败：{profile['error']}"
            ),
        },
        "tracking_error_series": {"available": False, "note": "本次未取得跟踪偏离/误差序列"},
        **_point_in_time_block(facts),
    }

    return Snapshot(
        instrument_id=f"ETF:{code}.{_ETF_VENUE_SUFFIX.get(code, 'SH')}",
        asset_class="etf",
        as_of=market_date_str,
        available_at=(
            f"{market_date_str}T15:00:00+08:00"
            if status == "closed"
            else now.isoformat(timespec="seconds")
        ),
        fetched_at=now.isoformat(timespec="seconds"),
        facts=facts,
        coverage=coverage,
        producers=["tools/build_snapshots.py", "akshare"],
        notes=(
            "阶段一固定快照：价格取自已收盘日线，净值与披露时间缺口如实记录；"
            f"bar_status={status}"
        ),
    )


# --- 个股 -------------------------------------------------------------------
def build_equity_snapshot(code: str, *, now: datetime | None = None) -> Snapshot:
    now = now or datetime.now(TZ)
    src = _make_sources(code)
    facts: list[Fact] = []

    info = ak.stock_individual_info_em(symbol=code)
    info_map = dict(zip(info["item"], info["value"]))
    for fact_id, metric, key in (
        ("profile.name", "security_name", "股票简称"),
        ("profile.industry", "industry", "行业"),
    ):
        facts.append(
            Fact.known(
                fact_id=fact_id,
                metric=metric,
                value=str(info_map.get(key)),
                period=None,
                available_at=None,
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["stock_info"],
                transform="个股信息当前值",
                notes="当前快照值，无历史版本、无报告期；可获得时间未知",
            )
        )
    listing = info_map.get("上市时间")
    facts.append(
        Fact.known(
            fact_id="profile.listing_date",
            metric="listing_date",
            value=_normalize_date_text(listing),
            period=None,
            available_at=None,
            fetched_at=now.isoformat(timespec="seconds"),
            source=src["stock_info"],
            transform="个股信息『上市时间』，统一为 YYYY-MM-DD",
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
                period=None,
                available_at=None,
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["stock_info"],
                transform="个股信息『总股本』",
                notes="当前股本，未与财报期末股本核对；可获得时间未知",
            )
        )
    else:
        facts.append(
            Fact.missing(
                fact_id="profile.total_shares",
                metric="total_shares",
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["stock_info"],
            )
        )

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

        for column, fact_id, metric, transform in (
            ("NETCASH_OPERATE", f"{prefix}.ocf", "operating_cash_flow", "现金流量表『NETCASH_OPERATE』"),
            ("CONSTRUCT_LONG_ASSET", f"{prefix}.capex", "capex", "现金流量表『CONSTRUCT_LONG_ASSET』（购建固定资产等支付的现金）"),
        ):
            raw = row.get(column)
            if pd.isna(raw):
                facts.append(
                    Fact.missing(
                        fact_id=fact_id,
                        metric=metric,
                        period=period,
                        available_at=notice,
                        fetched_at=now.isoformat(timespec="seconds"),
                        source=src["cash"],
                    )
                )
            elif float(raw) == 0.0:
                facts.append(
                    Fact.known_zero(
                        fact_id=fact_id,
                        metric=metric,
                        unit="CNY",
                        currency=str(row.get("CURRENCY") or "CNY"),
                        period=period,
                        available_at=notice,
                        fetched_at=now.isoformat(timespec="seconds"),
                        source=src["cash"],
                        transform=transform + "；报告中确实为零",
                    )
                )
            else:
                facts.append(
                    Fact.known(
                        fact_id=fact_id,
                        metric=metric,
                        value=float(raw),
                        unit="CNY",
                        currency=str(row.get("CURRENCY") or "CNY"),
                        period=period,
                        available_at=notice,
                        fetched_at=now.isoformat(timespec="seconds"),
                        source=src["cash"],
                        transform=transform,
                        notes="公告日期只有日期精度，按当日结束才视为已知",
                    )
                )

        facts.append(
            Fact.known(
                fact_id=f"{prefix}.notice_date",
                metric="notice_date",
                value=notice or "未知",
                period=period,
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["cash"],
                transform="现金流量表『NOTICE_DATE』（公告日期，日精度）",
            )
        )
        facts.append(
            Fact.not_applicable(
                fact_id=f"{prefix}.net_debt",
                metric="net_debt",
                period=period,
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["balance"],
                notes="银行以存款/同业负债为主，普通企业净债务口径不适用，需银行专用口径",
            )
        )

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
                fetched_at=now.isoformat(timespec="seconds"),
                source=src["dividend"],
                transform="巨潮『派息比例』（每 10 股，税前）",
                notes=(
                    f"分红类型={div_type}；实施状态={status}；公告日={announce}；"
                    f"除权日={str(row['除权日'])[:10] if pd.notna(row['除权日']) else '未知'}"
                ),
            )
        )

    coverage = {
        "build": {
            "mode": "latest_report_period",
            "report_period": str(cash.iloc[0]["REPORT_DATE"])[:10],
            "built_at": now.isoformat(timespec="seconds"),
        },
        "financial_statements": {
            "source_id": src["cash"].source_id,
            "periods_available": int(len(cash)),
            "has_notice_date": True,
            "has_update_date": True,
            "note": "公告日期只有日精度；UPDATE_DATE 可用于识别后续更正，但未逐条比对重述内容",
        },
        "dividend_records": {
            "source_id": src["dividend"].source_id,
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
        "price": {
            "available": False,
            "note": "阶段一未采集个股价格：实时接口无可靠收盘时刻标记，留待阶段二按已核实收盘源处理",
        },
        **_point_in_time_block(facts),
    }

    as_of = str(cash.iloc[0]["REPORT_DATE"])[:10]
    return Snapshot(
        instrument_id=f"EQUITY:{code}.SH",
        asset_class="equity",
        as_of=as_of,
        available_at=max((f.available_at for f in facts if f.available_at), default=as_of),
        fetched_at=now.isoformat(timespec="seconds"),
        facts=facts,
        coverage=coverage,
        producers=["tools/build_snapshots.py", "akshare"],
        notes="阶段一固定快照：报告期与公告日均可追溯；公告只有日精度",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建固定快照（阶段一）")
    parser.add_argument("--as-of", dest="as_of", default=None, help="预期市场日期 YYYY-MM-DD")
    parser.add_argument(
        "--only", choices=["index", "etf", "equity", "all"], default="all", help="只构建某一类"
    )
    args = parser.parse_args(argv)

    now = datetime.now(TZ)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    store = SnapshotStore(SNAPSHOT_DIR)

    built: list[Snapshot] = []
    if args.only in ("index", "all"):
        for code in INDEX_CODES:
            snap = build_index_snapshot(code, expected_as_of=args.as_of, now=now)
            store.save(snap)
            built.append(snap)
            print(f"指数 {code}: {snap.snapshot_id} as_of={snap.as_of} facts={len(snap.facts)}")

    if args.only in ("etf", "all"):
        for code in ETF_CODES:
            snap = build_etf_snapshot(code, expected_as_of=args.as_of, now=now)
            store.save(snap)
            built.append(snap)
            print(f"ETF {code}: {snap.snapshot_id} as_of={snap.as_of} facts={len(snap.facts)}")

    if args.only in ("equity", "all"):
        snap = build_equity_snapshot(STOCK_CODE, now=now)
        store.save(snap)
        built.append(snap)
        print(f"个股 {STOCK_CODE}: {snap.snapshot_id} as_of={snap.as_of} facts={len(snap.facts)}")

    entries = store.rebuild_manifest()
    summary = {
        "built_at": now.isoformat(timespec="seconds"),
        "expected_as_of": args.as_of,
        "snapshots": [
            {"snapshot_id": s.snapshot_id, "instrument_id": s.instrument_id, "as_of": s.as_of}
            for s in built
        ],
        "manifest_entries": len(entries),
    }
    (ROOT / "data" / "coverage" / "last_build.json").parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "coverage" / "last_build.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"manifest 已更新，共 {len(entries)} 个快照")
    return 0


if __name__ == "__main__":
    sys.exit(main())
