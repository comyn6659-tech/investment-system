#!/usr/bin/env python
"""构建阶段二个股研究快照（真实财务事实 + 分红 + 已收盘价格）。

与阶段一 build_snapshots.py 的关系：阶段一那份继续负责指数/ETF，本脚本只负责
个股，并把个股需要的口径补全。两者共用 tools/market_time.py 的时点判定。

口径原则（对应设计书 §5.2 与阶段二施工指令 §3.1）：
- 每个事实记录原科目、来源接口、报告期、公告时间、抓取时间、单位与币种；
- 合并报表口径（现金流量表/利润表/资产负债表均为合并数），并显式标注；
- 归母与少数股东分开：net_profit 为含少数股东的净利润，net_profit_parent 为归母；
- 银行为金融口径：普通企业「净债务」不适用，记为 not_applicable；
- 银行专项风险字段（不良率、拨备覆盖率、核心一级资本充足率）若免费接口确实不提供，
  记 missing 并写明已尝试的接口，绝不填造；
- 价格只取已收盘日线的收盘价；盘中行情不标为收盘价。

大体积原始数据只进 data/cache/，仓库里只保留小快照。

用法：
    .venv/bin/python tools/build_equity_snapshots.py
    .venv/bin/python tools/build_equity_snapshots.py --as-of 2026-11-10
    .venv/bin/python tools/build_equity_snapshots.py --only 600519
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import akshare as ak
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from engine.data.facts import Fact, SourceRef  # noqa: E402
from engine.data.snapshot import Snapshot, SnapshotStore  # noqa: E402
from engine.data.timeutil import parse_instant  # noqa: E402
from market_time import (  # noqa: E402
    BuildError,
    as_date,
    bar_status,
    normalize_date_text,
    select_bar,
)

TZ = ZoneInfo("Asia/Singapore")
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
CACHE_DIR = ROOT / "data" / "cache"

# 本轮真实样本：两只非金融（现金流型 + 成熟高分配型）+ 一只银行
DEFAULT_CODES = ("600519", "600900", "601398")
BANK_CODES = frozenset({"601398"})

_PERIODS = 12  # 保留最近 12 个报告期（覆盖 3 个完整年度）


# --- 来源 -------------------------------------------------------------------
def _sources(code: str, now: str) -> dict[str, SourceRef]:
    return {
        "stock_info": SourceRef(
            source_id="em.stock_individual_info",
            publisher="东方财富",
            interface="AKShare stock_individual_info_em",
            url="https://quote.eastmoney.com/",
            retrieved_at=now,
            notes="含总股本、流通股、行业、上市时间（当前快照值，无历史版本）",
        ),
        "profit": SourceRef(
            source_id="em.profit_sheet",
            publisher="东方财富",
            interface="AKShare stock_profit_sheet_by_report_em",
            url="https://data.eastmoney.com/bbsj/",
            retrieved_at=now,
            notes="合并利润表，按报告期；含 NOTICE_DATE 与 UPDATE_DATE；金额单位：元",
        ),
        "balance": SourceRef(
            source_id="em.balance_sheet",
            publisher="东方财富",
            interface="AKShare stock_balance_sheet_by_report_em",
            url="https://data.eastmoney.com/bbsj/",
            retrieved_at=now,
            notes="合并资产负债表，按报告期；含 NOTICE_DATE；金额单位：元",
        ),
        "cash": SourceRef(
            source_id="em.cash_flow_sheet",
            publisher="东方财富",
            interface="AKShare stock_cash_flow_sheet_by_report_em",
            url="https://data.eastmoney.com/bbsj/",
            retrieved_at=now,
            notes="合并现金流量表，按报告期；含折旧摊销补充资料列；金额单位：元",
        ),
        "dividend": SourceRef(
            source_id="cninfo.dividend",
            publisher="巨潮资讯",
            interface="AKShare stock_dividend_cninfo",
            url="https://webapi.cninfo.com.cn/#/company",
            retrieved_at=now,
            notes="分红类型（年度/中期/季度/特别）、派息比例（每 10 股，税前）、报告时间（所属财年）、实施公告日、除权日",
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
            notes="公告日期、派息、进度（实施/预案）、除权除息日；用于交叉核对巨潮记录",
        ),
        "price": SourceRef(
            source_id="em.stock_zh_a_hist",
            publisher="东方财富",
            interface="AKShare stock_zh_a_hist（不复权日线）",
            url="https://quote.eastmoney.com/",
            retrieved_at=now,
            notes="不复权日线；用于取已收盘交易日的收盘价，不做复权处理",
        ),
        "bank_probe": SourceRef(
            source_id="bank.metric_probe",
            publisher="东方财富 / 同花顺 / 新浪（经 AKShare 财务指标接口）",
            interface=(
                "stock_financial_abstract / stock_financial_abstract_new_ths / "
                "stock_financial_analysis_indicator / stock_financial_analysis_indicator_em"
            ),
            url="https://data.eastmoney.com/bbsj/",
            retrieved_at=now,
            notes="银行不良率、拨备覆盖率、资本充足率探测；四类接口均未返回该字段",
        ),
    }


# --- 小工具 -----------------------------------------------------------------
def _with_retry(label: str, fn, *, attempts: int = 3, sleep: float = 2.0):
    """网络抖动时重试；仍然失败就抛出，绝不返回一份残缺数据。"""
    import time

    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - 需要区分重试与最终失败
            last = exc
            if attempt + 1 < attempts:
                time.sleep(sleep * (attempt + 1))
    raise BuildError(f"{label} 连续 {attempts} 次失败：{type(last).__name__}: {last}") from last

def _f(value) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _text(value) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return None if text in ("", "nan", "None", "--") else text


class _Collector:
    def __init__(self, code: str, now: datetime) -> None:
        self.code = code
        self.now = now
        self.stamp = now.isoformat(timespec="seconds")
        self.facts: list[Fact] = []
        self.src = _sources(code, self.stamp)

    def add(
        self,
        fact_id: str,
        metric: str,
        value,
        *,
        source: str,
        period: str | None = None,
        available_at: str | None = None,
        unit: str | None = None,
        currency: str | None = None,
        transform: str = "",
        notes: str = "",
    ) -> None:
        self.facts.append(
            Fact.known(
                fact_id=fact_id,
                metric=metric,
                value=value,
                unit=unit,
                currency=currency,
                period=period,
                available_at=available_at,
                fetched_at=self.stamp,
                source=self.src[source],
                transform=transform,
                notes=notes,
            )
        )

    def add_amount(
        self,
        fact_id: str,
        metric: str,
        raw,
        *,
        source: str,
        period: str,
        available_at: str | None,
        transform: str,
        notes: str = "",
    ) -> None:
        """金额事实：NaN → missing；真实 0 → known_zero；其余 → known。"""
        value = _f(raw)
        if value is None:
            self.facts.append(
                Fact.missing(
                    fact_id=fact_id,
                    metric=metric,
                    period=period,
                    available_at=available_at,
                    fetched_at=self.stamp,
                    source=self.src[source],
                    transform=transform,
                    notes="来源返回空值，不用 0 代替",
                )
            )
        elif value == 0.0:
            self.facts.append(
                Fact.known_zero(
                    fact_id=fact_id,
                    metric=metric,
                    unit="CNY",
                    currency="CNY",
                    period=period,
                    available_at=available_at,
                    fetched_at=self.stamp,
                    source=self.src[source],
                    transform=transform + "；报表中确实为 0",
                    notes=notes,
                )
            )
        else:
            self.facts.append(
                Fact.known(
                    fact_id=fact_id,
                    metric=metric,
                    value=value,
                    unit="CNY",
                    currency="CNY",
                    period=period,
                    available_at=available_at,
                    fetched_at=self.stamp,
                    source=self.src[source],
                    transform=transform,
                    notes=notes,
                )
            )

    def add_missing(
        self, fact_id: str, metric: str, *, source: str, period: str | None = None, notes: str
    ) -> None:
        self.facts.append(
            Fact.missing(
                fact_id=fact_id,
                metric=metric,
                period=period,
                fetched_at=self.stamp,
                source=self.src[source],
                notes=notes,
            )
        )


# --- 报表字段映射 -----------------------------------------------------------
# (列名, 后缀, metric, 口径说明)
_PROFIT_FIELDS = (
    ("TOTAL_OPERATE_INCOME", "revenue", "total_operating_revenue", "营业总收入（合并）"),
    ("OPERATE_INCOME", "operating_revenue", "operating_revenue", "营业收入（合并）"),
    ("OPERATE_PROFIT", "operating_profit", "operating_profit", "营业利润（合并）"),
    ("TOTAL_PROFIT", "pretax_profit", "pretax_profit", "利润总额（合并）"),
    ("INCOME_TAX", "income_tax", "income_tax", "所得税费用（合并）"),
    ("NETPROFIT", "net_profit", "net_profit", "净利润（合并，含少数股东损益）"),
    ("PARENT_NETPROFIT", "net_profit_parent", "net_profit_parent", "归属于母公司股东的净利润"),
    ("FE_INTEREST_EXPENSE", "interest_expense", "interest_expense", "利息费用（财务费用明细）"),
    ("FINANCE_EXPENSE", "finance_expense", "finance_expense", "财务费用（净额，可为负=净利息收入）"),
    ("BASIC_EPS", "eps_basic", "basic_eps", "基本每股收益（元/股）"),
)

_CASH_FIELDS = (
    ("NETCASH_OPERATE", "ocf", "operating_cash_flow", "经营活动产生的现金流量净额（合并）"),
    ("CONSTRUCT_LONG_ASSET", "capex", "capex", "购建固定资产、无形资产和其他长期资产支付的现金"),
    ("FA_IR_DEPR", "depreciation", "depreciation", "固定资产折旧、油气资产折耗、生产性生物资产折旧"),
    ("IA_AMORTIZE", "amortization_intangible", "amortization_intangible", "无形资产摊销"),
    ("LPE_AMORTIZE", "amortization_lt_prepaid", "amortization_lt_prepaid", "长期待摊费用摊销"),
    ("USERIGHT_ASSET_AMORTIZE", "amortization_right_of_use", "amortization_right_of_use", "使用权资产摊销"),
    # 现金流量表补充资料里的营运资本变动项（正数=释放现金）
    ("INVENTORY_REDUCE", "inventory_decrease", "inventory_decrease", "存货的减少（减：增加）"),
    ("OPERATE_RECE_REDUCE", "operating_receivables_decrease", "operating_receivables_decrease", "经营性应收项目的减少（减：增加）"),
    ("OPERATE_PAYABLE_ADD", "operating_payables_add", "operating_payables_add", "经营性应付项目的增加（减：减少）"),
    ("DEFER_TAX", "deferred_tax", "deferred_tax", "递延所得税资产/负债变动"),
)

_BALANCE_FIELDS = (
    ("MONETARYFUNDS", "cash", "cash_and_equivalents", "货币资金"),
    ("SHORT_LOAN", "short_loan", "short_term_loan", "短期借款"),
    ("LONG_LOAN", "long_loan", "long_term_loan", "长期借款"),
    ("BOND_PAYABLE", "bond_payable", "bond_payable", "应付债券"),
    ("NONCURRENT_LIAB_1YEAR", "current_portion_lt_debt", "current_portion_long_term_debt", "一年内到期的非流动负债"),
    ("LEASE_LIAB", "lease_liab", "lease_liability", "租赁负债"),
    ("MINORITY_EQUITY", "minority_equity", "minority_equity", "少数股东权益"),
    ("TOTAL_PARENT_EQUITY", "equity_parent", "equity_parent", "归属于母公司股东权益合计"),
    ("TOTAL_ASSETS", "total_assets", "total_assets", "资产总计"),
    ("TOTAL_LIABILITIES", "total_liabilities", "total_liabilities", "负债合计"),
    ("INVENTORY", "inventory", "inventory", "存货"),
    ("NOTE_ACCOUNTS_RECE", "receivables_notes", "notes_and_accounts_receivable", "应收票据及应收账款"),
    ("ACCOUNTS_RECE", "receivables", "accounts_receivable", "应收账款"),
    ("NOTE_ACCOUNTS_PAY", "payables_notes", "notes_and_accounts_payable", "应付票据及应付账款"),
    ("ACCOUNTS_PAY", "payables", "accounts_payable", "应付账款"),
    ("SHARE_CAPITAL", "share_capital", "share_capital", "股本（元；A股面值每股 1 元）"),
    ("LOAN_ADVANCE", "loan_advance", "loan_advance", "发放贷款及垫款（银行专用）"),
)


def _statement_periods(df: pd.DataFrame, column: str = "REPORT_DATE") -> list[str]:
    out = []
    for _, row in df.iterrows():
        text = _text(row.get(column))
        if text:
            out.append(text[:10])
    return out


# --- 分红 -------------------------------------------------------------------
_FISCAL_PATTERN = ("年报", "半年报", "三季报", "一季报", "季报")


def _fiscal_label(report_time: str | None) -> str | None:
    """把巨潮『报告时间』规整为「YYYY年报 / YYYY半年报 / YYYY三季报」。"""
    if not report_time:
        return None
    text = report_time.strip()
    for pattern in _FISCAL_PATTERN:
        if text.endswith(pattern):
            head = text[: -len(pattern)]
            digits = "".join(ch for ch in head if ch.isdigit())
            if len(digits) == 4:
                return f"{digits}{pattern}"
            return None
    return None


def _collect_dividends(c: _Collector, code: str) -> dict:
    cninfo = ak.stock_dividend_cninfo(symbol=code)
    cninfo.to_csv(CACHE_DIR / f"equity_dividend_cninfo_{code}.csv", index=False)
    detail = ak.stock_history_dividend_detail(symbol=code, indicator="分红")
    detail.to_csv(CACHE_DIR / f"equity_dividend_detail_{code}.csv", index=False)
    detail_by_date = {}
    for _, row in detail.iterrows():
        key = _text(row.get("公告日期"))
        if key:
            detail_by_date[key[:10]] = row

    records: list[dict] = []
    used_bases: dict[str, int] = {}
    for _, row in cninfo.iterrows():
        per10 = _f(row.get("派息比例"))
        kind = _text(row.get("分红类型")) or "未标注类型"
        announce = _text(row.get("实施方案公告日期"))
        fiscal = _fiscal_label(_text(row.get("报告时间")))
        ex_date = _text(row.get("除权日"))
        record_date = _text(row.get("股权登记日"))
        pay_date = _text(row.get("派息日"))
        matched = detail_by_date.get((announce or "")[:10])
        status = _text(matched.get("进度")) if matched is not None else None
        status = status or "未取得"
        if per10 is None:
            continue

        # 所属财年缺失时，不猜年份：单独成一条 unlabeled 记录
        slot = fiscal if fiscal else f"unlabeled-{(announce or 'unknown')[:10]}"
        fact_base = f"div.{slot}.{kind}"
        # 同一财年同一类型可能有多条（例如同一年两笔特别分红）：用公告日区分，不合并
        if fact_base in used_bases:
            used_bases[fact_base] += 1
            fact_base = f"{fact_base}#{(announce or 'unknown')[:10]}"
        else:
            used_bases[fact_base] = 1

        note = (
            f"分红类型={kind}；实施状态={status}；实施公告日={announce or '未知'}；"
            f"除权日={ex_date or '未知'}；股权登记日={record_date or '未知'}；"
            f"派息日={pay_date or '未知'}；所属财年={'缺失（来源未标注）' if not fiscal else fiscal}"
        )
        c.add(
            f"{fact_base}.per_10",
            "cash_dividend_per_10_shares",
            per10,
            source="dividend",
            period=fiscal,
            available_at=announce,
            unit="CNY",
            currency="CNY",
            transform="巨潮『派息比例』（每 10 股，税前，含税口径未在来源中细分）",
            notes=note,
        )
        if ex_date:
            c.add(
                f"{fact_base}.ex_date",
                "ex_dividend_date",
                ex_date[:10],
                source="dividend",
                period=fiscal,
                available_at=announce,
                transform="巨潮『除权日』",
            )
        c.add(
            f"{fact_base}.status",
            "dividend_implementation_status",
            status,
            source="dividend_detail",
            period=fiscal,
            available_at=announce,
            transform="新浪『进度』（实施/预案）",
            notes="巨潮与新浪按实施公告日对齐",
        )
        records.append(
            {
                "fact_base": fact_base,
                "fiscal": fiscal,
                "kind": kind,
                "per_10": per10,
                "announce": announce,
                "ex_date": ex_date,
                "status": status,
            }
        )
    return {"records": records, "cninfo_rows": int(len(cninfo)), "detail_rows": int(len(detail))}


# --- 银行专项字段探测 -------------------------------------------------------
_BANK_METRIC_ATTEMPTS = (
    "stock_financial_abstract（80 项常用/每股/盈利/成长/质量/风险指标，无不良率与资本充足率）",
    "stock_financial_abstract_new_ths（24 项指标，无不良率与资本充足率）",
    "stock_financial_analysis_indicator（86 列财务比率，无不良率与资本充足率）",
    "stock_financial_analysis_indicator_em（调用失败：返回结构异常）",
)


def _collect_bank_gaps(c: _Collector, code: str, periods: list[str]) -> dict:
    """银行资产质量/资本充足率：免费接口确实不提供，如实记 missing。"""
    note = "免费接口未提供该字段；已尝试的接口见 bank_metric_probe 记录"
    for fact_id, metric, extra in (
        ("bank.npl_ratio", "non_performing_loan_ratio", "不良贷款率（银行专项）"),
        ("bank.provision_coverage", "provision_coverage_ratio", "拨备覆盖率（银行专项）"),
        ("bank.cet1_ratio", "cet1_capital_adequacy_ratio", "核心一级资本充足率（银行专项）"),
        ("bank.capital_adequacy_ratio", "capital_adequacy_ratio", "资本充足率（银行专项）"),
        ("bank.risk_weighted_assets", "risk_weighted_assets", "风险加权资产"),
        (
            "bank.required_cet1_minimum",
            "regulatory_cet1_minimum",
            "适用的核心一级资本监管下限（需监管文件来源，不由实现方自造）",
        ),
    ):
        c.add_missing(fact_id, metric, source="bank_probe", notes=f"{extra}：{note}")
    return {
        "available": False,
        "fields_missing": [
            "bank.npl_ratio",
            "bank.provision_coverage",
            "bank.cet1_ratio",
            "bank.capital_adequacy_ratio",
            "bank.risk_weighted_assets",
            "bank.required_cet1_minimum",
        ],
        "attempts": list(_BANK_METRIC_ATTEMPTS),
        "note": "银行路线本轮不填造风险阈值；缺失即返回 insufficient_data，并列出待补字段",
        "statement_periods_checked": periods[:4],
    }


# --- 时点精度 ---------------------------------------------------------------
def _point_in_time_block(facts: list[Fact]) -> dict:
    counts = {"second": 0, "date": 0, "unknown": 0}
    unknown: list[str] = []
    for fact in facts:
        instant = parse_instant(fact.available_at) if fact.available_at else None
        precision = instant.precision if instant else None
        counts[precision or "unknown"] += 1
        if precision is None:
            unknown.append(fact.fact_id)
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
        "availability_unknown": sorted(unknown),
        "availability_note": (
            "个股事实的可获得时间主要来自公告日期（日精度）；按「当日结束时才可知」处理，"
            "因此不标记 point_in_time_safe，也不用于盘中历史判断。价格事实带收盘时刻。"
        ),
    }


# --- 主构建 -----------------------------------------------------------------
def build_equity_snapshot(
    code: str, *, expected_as_of: str | None = None, now: datetime | None = None
) -> Snapshot:
    now = now or datetime.now(TZ)
    c = _Collector(code, now)
    venue = "SH" if code.startswith(("6", "9")) else "SZ"
    is_bank = code in BANK_CODES

    # 1) 价格：市场日期以不复权日线最后一根为准
    hist = _with_retry(
        f"{code} 日线",
        lambda: ak.stock_zh_a_hist(
            symbol=code, period="daily", start_date="20240101", end_date="20301231", adjust=""
        ),
    )
    if hist.empty:
        raise BuildError(f"{code} 日线为空，无法确定市场日期")
    hist.to_csv(CACHE_DIR / f"equity_hist_{code}.csv", index=False)
    hist, market_date, dropped = select_bar(hist, "日期", expected_as_of, f"{code} 日线")
    market_date_str = market_date.isoformat()
    status = bar_status(market_date, now)
    close = _f(hist["收盘"].iloc[-1])
    if close is None or close <= 0:
        raise BuildError(f"{code} 收盘价非法: {close!r}")

    c.add(
        "price.close",
        "market_close_price",
        close,
        source="price",
        period=market_date_str,
        available_at=f"{market_date_str}T15:00:00+08:00" if status == "closed" else None,
        unit="CNY_per_share",
        currency="CNY",
        transform=f"不复权日线最后一根收盘价（bar_status={status}）",
        notes=("已收盘交易日的收盘价" if status == "closed" else "当日未收盘行情，不得标为收盘价"),
    )
    c.add(
        "price.trade_date",
        "price_trade_date",
        market_date_str,
        source="price",
        period=market_date_str,
        available_at=f"{market_date_str}T15:00:00+08:00" if status == "closed" else None,
        transform="不复权日线行情日期",
    )
    c.add(
        "price.bar_status",
        "price_bar_status",
        status,
        source="price",
        period=market_date_str,
        transform="按交易所收盘时间判定",
        notes="intraday 表示最新一根未收盘，不能当收盘价使用",
    )

    # 2) 公司资料与股本
    info = _with_retry(f"{code} 个股信息", lambda: ak.stock_individual_info_em(symbol=code))
    info_map = dict(zip(info["item"], info["value"]))
    for fact_id, metric, key in (
        ("profile.name", "security_name", "股票简称"),
        ("profile.industry", "industry", "行业"),
    ):
        value = _text(info_map.get(key))
        if value is None:
            c.add_missing(fact_id, metric, source="stock_info", notes="个股信息接口未返回该字段")
        else:
            c.add(
                fact_id,
                metric,
                value,
                source="stock_info",
                transform="个股信息当前值",
                notes="当前快照值，无历史版本；可获得时间未知",
            )
    listing = _text(info_map.get("上市时间"))
    if listing:
        c.add(
            "profile.listing_date",
            "listing_date",
            normalize_date_text(listing),
            source="stock_info",
            transform="个股信息『上市时间』，统一为 YYYY-MM-DD",
        )
    total_shares = _f(info_map.get("总股本"))
    if total_shares is None:
        c.add_missing("profile.total_shares", "total_shares", source="stock_info", notes="未取得总股本")
    else:
        c.add(
            "profile.total_shares",
            "total_shares",
            total_shares,
            source="stock_info",
            unit="shares",
            transform="个股信息『总股本』",
            notes=(
                "当前总股本（A股总股本），用于把股权价值换算为每股价值；"
                f"未标注股本变动生效日，抓取时间 {c.stamp}"
            ),
        )

    # 3) 三张报表（最近 12 个报告期）
    profit = _with_retry(
        f"{code} 利润表", lambda: ak.stock_profit_sheet_by_report_em(symbol=f"{venue}{code}")
    )
    balance = _with_retry(
        f"{code} 资产负债表", lambda: ak.stock_balance_sheet_by_report_em(symbol=f"{venue}{code}")
    )
    cash = _with_retry(
        f"{code} 现金流量表", lambda: ak.stock_cash_flow_sheet_by_report_em(symbol=f"{venue}{code}")
    )
    for frame, name in ((profit, "profit"), (balance, "balance"), (cash, "cash_flow")):
        frame.to_csv(CACHE_DIR / f"equity_{name}_{code}.csv", index=False)

    profit = profit.sort_values("REPORT_DATE", ascending=False).head(_PERIODS)
    balance = balance.sort_values("REPORT_DATE", ascending=False).head(_PERIODS)
    cash = cash.sort_values("REPORT_DATE", ascending=False).head(_PERIODS)

    notice_by_period: dict[str, str | None] = {}
    for frame in (profit, balance, cash):
        for _, row in frame.iterrows():
            period = (_text(row.get("REPORT_DATE")) or "")[:10]
            if not period:
                continue
            notice = _text(row.get("NOTICE_DATE"))
            if notice and (period not in notice_by_period or not notice_by_period[period]):
                notice_by_period[period] = notice[:10]

    for _, row in profit.iterrows():
        period = (_text(row.get("REPORT_DATE")) or "")[:10]
        if not period:
            continue
        notice = (_text(row.get("NOTICE_DATE")) or "")[:10] or None
        available = notice or notice_by_period.get(period)
        for column, suffix, metric, label in _PROFIT_FIELDS:
            fact_id = f"fin.{period}.{suffix}"
            if suffix == "eps_basic":
                value = _f(row.get(column))
                if value is None:
                    c.add_missing(
                        fact_id, metric, source="profit", period=period, notes="利润表未返回基本每股收益"
                    )
                else:
                    c.add(
                        fact_id,
                        metric,
                        value,
                        source="profit",
                        period=period,
                        available_at=available,
                        unit="CNY_per_share",
                        currency="CNY",
                        transform=f"利润表『{column}』（{label}）",
                        notes="公告日期只有日期精度，按当日结束才视为已知",
                    )
                continue
            c.add_amount(
                fact_id,
                metric,
                row.get(column),
                source="profit",
                period=period,
                available_at=available,
                transform=f"利润表『{column}』（{label}）",
                notes="合并报表口径；公告日期只有日期精度，按当日结束才视为已知",
            )
        c.add(
            f"fin.{period}.notice_date",
            "notice_date",
            notice or "未知",
            source="profit",
            period=period,
            transform="利润表『NOTICE_DATE』（公告日期，日精度）",
        )

    for _, row in cash.iterrows():
        period = (_text(row.get("REPORT_DATE")) or "")[:10]
        if not period:
            continue
        notice = (_text(row.get("NOTICE_DATE")) or "")[:10] or None
        available = notice or notice_by_period.get(period)
        for column, suffix, metric, label in _CASH_FIELDS:
            c.add_amount(
                f"fin.{period}.{suffix}",
                metric,
                row.get(column),
                source="cash",
                period=period,
                available_at=available,
                transform=f"现金流量表『{column}』（{label}）",
                notes="合并报表口径；折旧摊销列取自现金流量表补充资料",
            )

    for _, row in balance.iterrows():
        period = (_text(row.get("REPORT_DATE")) or "")[:10]
        if not period:
            continue
        notice = (_text(row.get("NOTICE_DATE")) or "")[:10] or None
        available = notice or notice_by_period.get(period)
        for column, suffix, metric, label in _BALANCE_FIELDS:
            if suffix == "loan_advance" and not is_bank:
                continue
            c.add_amount(
                f"fin.{period}.{suffix}",
                metric,
                row.get(column),
                source="balance",
                period=period,
                available_at=available,
                transform=f"资产负债表『{column}』（{label}）",
                notes="合并报表口径",
            )
        if is_bank:
            c.facts.append(
                Fact.not_applicable(
                    fact_id=f"fin.{period}.net_debt",
                    metric="net_debt",
                    period=period,
                    fetched_at=c.stamp,
                    source=c.src["balance"],
                    notes=(
                        "银行以存款/同业负债为主，普通企业「有息负债−现金」净债务口径不适用；"
                        "银行路线使用资本充足与资产质量口径，不使用净债务"
                    ),
                )
            )

    # 4) 分红
    dividend_block = _collect_dividends(c, code)

    # 5) 银行专项缺口
    bank_block = None
    periods_sorted = sorted(notice_by_period, reverse=True)
    if is_bank:
        bank_block = _collect_bank_gaps(c, code, periods_sorted)

    # 6) 覆盖度
    latest_period = periods_sorted[0] if periods_sorted else None
    coverage = {
        "build": {
            "mode": "latest_closed_bar",
            "market_date": market_date_str,
            "bar_status": status,
            "built_at": c.stamp,
            "date_source": "按日期选行" if expected_as_of else "日线最后一根",
            "rows_after_market_date_dropped": dropped,
        },
        "price": {
            "source_id": "em.stock_zh_a_hist",
            "price_type": "close_unadjusted",
            "trade_date": market_date_str,
            "bar_status": status,
            "available_at": f"{market_date_str}T15:00:00+08:00" if status == "closed" else None,
            "note": "不复权收盘价；盘中行情不标为收盘价，缺价格时不做现价比较",
        },
        "financial_statements": {
            "source_ids": ["em.profit_sheet", "em.balance_sheet", "em.cash_flow_sheet"],
            "periods_available": len(periods_sorted),
            "latest_period": latest_period,
            "currency": "CNY",
            "scale": "元",
            "consolidation": "合并报表",
            "period_type": "累计数（年初至报告期末）",
            "has_notice_date": True,
            "note": "年报/中报/季报均为年初至期末累计数，不能与单季数直接混加；TTM 需按代码内规则转换",
        },
        "dividends": {
            "source_id": "cninfo.dividend",
            "records": dividend_block["cninfo_rows"],
            "detail_rows": dividend_block["detail_rows"],
            "fiscal_year_labeled": sum(1 for r in dividend_block["records"] if r["fiscal"]),
            "fiscal_year_missing": sum(1 for r in dividend_block["records"] if not r["fiscal"]),
            "types": sorted({r["kind"] for r in dividend_block["records"]}),
            "note": (
                "巨潮『分红类型』区分年度/中期/季度/特别；『报告时间』给出所属财年；"
                "缺失所属财年的记录不按年份猜测"
            ),
        },
        "share_count": {
            "current_total_shares": total_shares,
            "source_id": "em.stock_individual_info",
            "note": "当前总股本用于每股换算；历史加权股数由归母净利润/基本每股收益推导后交叉核对",
        },
        "bank_specific": bank_block,
        **_point_in_time_block(c.facts),
    }

    as_of = market_date_str
    available_at = (
        f"{market_date_str}T15:00:00+08:00" if status == "closed" else c.stamp
    )
    return Snapshot(
        instrument_id=f"EQUITY:{code}.{venue}",
        asset_class="equity",
        as_of=as_of,
        available_at=available_at,
        fetched_at=c.stamp,
        facts=c.facts,
        coverage=coverage,
        producers=["tools/build_equity_snapshots.py", "akshare"],
        notes=(
            "阶段二个股研究快照：三张合并报表最近 12 个报告期 + 分红明细 + 已收盘价格；"
            f"估值日 {as_of}，bar_status={status}"
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建阶段二个股研究快照")
    parser.add_argument("--as-of", dest="as_of", default=None, help="预期市场日期 YYYY-MM-DD")
    parser.add_argument(
        "--only", default=None, help="只构建指定代码，逗号分隔（默认 600519,600900,601398）"
    )
    args = parser.parse_args(argv)

    codes = tuple(x.strip() for x in args.only.split(",")) if args.only else DEFAULT_CODES
    now = datetime.now(TZ)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    store = SnapshotStore(SNAPSHOT_DIR)

    built: list[Snapshot] = []
    for code in codes:
        snap = build_equity_snapshot(code, expected_as_of=args.as_of, now=now)
        store.save(snap)
        built.append(snap)
        print(
            f"个股 {code}: {snap.snapshot_id} as_of={snap.as_of} facts={len(snap.facts)}",
            file=sys.stderr,
        )

    store.rebuild_manifest()
    summary = {
        "built_at": now.isoformat(timespec="seconds"),
        "expected_as_of": args.as_of,
        "snapshots": [
            {
                "snapshot_id": s.snapshot_id,
                "instrument_id": s.instrument_id,
                "as_of": s.as_of,
                "facts": len(s.facts),
            }
            for s in built
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
