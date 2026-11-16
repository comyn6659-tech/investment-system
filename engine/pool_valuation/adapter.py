"""从全市场批量表生成个股政策输入（不改政策与估值代码）。

批量表来自 `data/pool-valuation/snapshot-*`：按报告期的合并报表压缩表、分红表、
全市场行情快照与总股本。适配层把行还原成与个股快照同名的 `fin.*` / `div.*` /
`profile.*` / `price.*` 事实，再交给原有的 `engine.policy.trial.evaluate_company`
与 `engine.policy.valuation` 代码；政策与估值代码零修改。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.data.facts import Fact, SourceRef
from engine.data.snapshot import Snapshot
from engine.evidence.models import AnnualPoint, BusinessEvidence
from engine.quality.snapshot import manifest_hash, verify_table
from engine.quality.tables import read_table

ROOT = Path(__file__).resolve().parent.parent.parent
POOL_DIR = ROOT / "data" / "pool-valuation"
POLICY_EVIDENCE = ROOT / "data" / "policy-trial" / "evidence"

BALANCE_MAP = {
    "TOTAL_PARENT_EQUITY": "equity_parent",
    "MONETARYFUNDS": "cash",
    "SHORT_LOAN": "short_loan",
    "NONCURRENT_LIAB_1YEAR": "current_portion_lt_debt",
    "LONG_LOAN": "long_loan",
    "BOND_PAYABLE": "bond_payable",
    "LEASE_LIAB": "lease_liab",
    "MINORITY_EQUITY": "minority_equity",
    "INVENTORY": "inventory",
    "NOTE_ACCOUNTS_RECE": "receivables_notes",
    "ACCOUNTS_RECE": "receivables",
    "SHARE_CAPITAL": "share_capital",
    "LOAN_ADVANCE": "loan_advance",
    "ACCEPT_DEPOSIT_INTERBANK": "accept_deposit_interbank",
    "TOTAL_ASSETS": "total_assets",
    "TOTAL_LIABILITIES": "total_liabilities",
}
INCOME_MAP = {
    "TOTAL_OPERATE_INCOME": "revenue",
    "OPERATE_INCOME": "operating_revenue",
    "OPERATE_PROFIT": "operating_profit",
    "TOTAL_PROFIT": "pretax_profit",
    "INCOME_TAX": "income_tax",
    "NETPROFIT": "net_profit",
    "PARENT_NETPROFIT": "net_profit_parent",
    "FE_INTEREST_EXPENSE": "interest_expense",
    "FINANCE_EXPENSE": "finance_expense",
    "BASIC_EPS": "eps_basic",
}
CASHFLOW_MAP = {
    "NETCASH_OPERATE": "ocf",
    "CONSTRUCT_LONG_ASSET": "capex",
    "FA_IR_DEPR": "depreciation",
    "IA_AMORTIZE": "amortization_intangible",
    "LPE_AMORTIZE": "amortization_lt_prepaid",
    "USERIGHT_ASSET_AMORTIZE": "amortization_right_of_use",
    "END_CASH_EQUIVALENTS": "cash_equivalents_component",
    "END_CCE": "cash_equivalents_end",
    "INVENTORY_REDUCE": "inventory_decrease",
    "OPERATE_RECE_REDUCE": "operating_receivables_decrease",
    "OPERATE_PAYABLE_ADD": "operating_payables_add",
    "DEFER_TAX": "deferred_tax",
}


def _num(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value) -> str | None:
    text = str(value or "")[:10]
    return text if len(text) == 10 else None


def _source() -> SourceRef:
    return SourceRef(
        source_id="pool.batch.financials",
        publisher="东方财富",
        interface="按报告期批量合并报表（RPT_F10_FINANCE_G*/RPT_F10_EH_EQUITY）",
        url="https://datacenter-web.eastmoney.com/api/data/v1/get",
        notes="池成员估值批量输入；字段映射见 docs/phase4j-pool-valuation-report.md",
    )


def _price_source() -> SourceRef:
    return SourceRef(
        source_id="tencent.quote_snapshot",
        publisher="腾讯财经",
        interface="qt.gtimg.cn 全市场行情快照",
        url="https://qt.gtimg.cn/",
        notes="全市场一次性快照；价格日期逐行记录",
    )


class BatchStore:
    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory or POOL_DIR / json.loads((POOL_DIR / "manifest.json").read_text(encoding="utf-8"))["current"])
        manifest_path = self.directory / "manifest.json"
        if not manifest_path.exists():
            raise RuntimeError(f"批量估值快照清单不存在：{manifest_path}")
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if self.manifest.get("content_hash") != manifest_hash(self.manifest):
            raise RuntimeError("批量估值快照 content_hash 与清单不一致")
        for entry in self.manifest.get("tables") or []:
            verify_table(self.directory / str(entry["file"]), entry)
        self.tables = {
            entry["file"]: read_table(self.directory / entry["file"])
            for entry in self.manifest.get("tables") or []
        }
        self.entries = {entry["file"]: entry for entry in self.manifest.get("tables") or []}
        self.report_periods = sorted(
            {
                str(entry.get("report_date"))
                for entry in self.manifest.get("tables") or []
                if entry.get("kind")
                in ("balance_full", "income_full", "cashflow_full", "interim_income_full")
                and entry.get("report_date")
            }
        )
        self.earnings_latest_period = str(
            self.manifest.get("earnings_latest_period")
            or (self.manifest.get("build_stats") or {}).get("earnings_period")
            or ""
        ) or None
        self._prepare()

    def _prepare(self):
        self.balance: dict[str, dict[str, dict]] = {}
        self.income: dict[str, dict[str, dict]] = {}
        self.cashflow: dict[str, dict[str, dict]] = {}
        self.interim: dict[str, dict[str, dict]] = {}
        self.dividends: dict[str, list[dict]] = {}
        self.universe = {}
        self.industry = {}
        self.price = {}
        self.shares = {}
        for name, rows in self.tables.items():
            entry = self.entries[name]
            kind = entry.get("kind")
            if kind == "balance_full":
                self.balance[_date(entry.get("report_date"))] = {
                    str(row["SECURITY_CODE"]).zfill(6): row for row in rows
                }
            elif kind == "income_full":
                self.income[_date(entry.get("report_date"))] = {
                    str(row["SECURITY_CODE"]).zfill(6): row for row in rows
                }
            elif kind == "cashflow_full":
                self.cashflow[_date(entry.get("report_date"))] = {
                    str(row["SECURITY_CODE"]).zfill(6): row for row in rows
                }
            elif kind == "interim_income_full":
                period = _date(entry.get("report_date")) or "2026-09-30"
                self.interim[period] = {
                    str(row["SECURITY_CODE"]).zfill(6): row for row in rows
                }
            elif kind in ("dividends", "dividends_full"):
                for row in rows:
                    code = str(row.get("SECURITY_CODE") or "").zfill(6)
                    self.dividends.setdefault(code, []).append(row)
            elif kind == "universe":
                self.universe = {str(row["code"]).zfill(6): row for row in rows}
            elif kind == "industry":
                self.industry = {
                    str(row["code"]).zfill(6): str(row.get("industry") or "")
                    for row in rows
                }
            elif kind == "price":
                self.price = {str(row["code"]).zfill(6): row for row in rows}
            elif kind == "shares":
                self.shares = {str(row["code"]).zfill(6): row for row in rows}

    def annual_rows(self, code: str) -> dict[int, dict]:
        output: dict[int, dict] = {}
        for period, by_code in self.balance.items():
            year = int(period[:4])
            if year in output:
                continue
            merged = dict(by_code.get(code) or {})
            merged.update(self.income.get(period, {}).get(code) or {})
            merged.update(self.cashflow.get(period, {}).get(code) or {})
            if merged:
                output[year] = merged
        return output

    def dividend_rows(self, code: str) -> list[dict]:
        return self.dividends.get(code, [])

    def shares_for(self, code: str) -> float | None:
        row = self.shares.get(code) or {}
        return _num(row.get("total_shares"))

    def price_for(self, code: str) -> tuple[float | None, str | None]:
        row = self.price.get(code) or {}
        return _num(row.get("price")), _date(row.get("price_date"))

    def name_for(self, code: str) -> str:
        row = self.universe.get(code) or self.price.get(code) or self.shares.get(code) or {}
        return str(row.get("name") or code)

    def industry_for(self, code: str) -> str:
        return self.industry.get(code, "")

    def has_period(self, code: str, period: str) -> bool:
        code = str(code).zfill(6)
        period = str(period or "")
        if period in self.balance:
            return (
                code in self.balance[period]
                and code in self.income.get(period, {})
                and code in self.cashflow.get(period, {})
            )
        if period in self.interim:
            return code in self.interim[period]
        return False

    def latest_period_for(self, code: str) -> str | None:
        code = str(code).zfill(6)
        periods = [period for period in self.balance if code in self.balance[period]]
        periods += [period for period in self.interim if code in self.interim[period]]
        return max(periods) if periods else None

    def report_note(self, code: str) -> str | None:
        """新报告期缺该公司报表时的卡片提示；有数据返回 None。"""
        expected = self.earnings_latest_period
        if not expected or self.has_period(code, expected):
            return None
        latest = self.latest_period_for(code)
        if not latest:
            return f"财报未更新：{_period_label(expected)}未取得，且没有旧报表可沿用。"
        return f"财报未更新：{_period_label(expected)}未取得，沿用 {_period_label(latest)} 数据。"


def _period_label(period: str) -> str:
    text = str(period or "")
    if len(text) < 10:
        return text
    year, month = text[:4], text[5:7]
    return {
        "12": f"{year} 年报",
        "06": f"{year} 半年报",
        "09": f"{year} 三季报",
        "03": f"{year} 一季报",
    }.get(month, text)


def _fiscal_label(report_date: str | None) -> str | None:
    day = _date(report_date)
    if not day:
        return None
    year, month = day[:4], day[5:7]
    suffix = {
        "12": "年报",
        "06": "半年报",
        "09": "三季报",
        "03": "一季报",
    }.get(month)
    return f"{year}{suffix}" if suffix else None


def _dividend_kind(report_date: str | None, profile: str) -> str:
    day = _date(report_date) or ""
    if "特别" in (profile or "") or "特殊" in (profile or ""):
        return "特别分红"
    month = day[5:7]
    return {"12": "年度分红", "06": "中期分红", "09": "季度分红", "03": "季度分红"}.get(
        month, "分红"
    )


def build_snapshot(
    store: BatchStore,
    code: str,
    *,
    price_override: float | None = None,
    price_date_override: str | None = None,
    shares_override: float | None = None,
    as_of_override: str | None = None,
) -> Snapshot:
    code = str(code).zfill(6)
    source = _source()
    stamp = as_of_override or store.manifest.get("as_of") or "2026-11-13"
    facts: list[Fact] = []
    rows = store.annual_rows(code)
    for year in sorted(rows):
        row = rows[year]
        period = f"{year}-12-31"
        available = str(row.get("NOTICE_DATE") or "")[:10] or period
        for upstream, downstream in {**BALANCE_MAP, **INCOME_MAP, **CASHFLOW_MAP}.items():
            value = _num(row.get(upstream))
            fact_id = f"fin.{period}.{downstream}"
            if value is None:
                continue
            facts.append(
                Fact.known(
                    fact_id=fact_id,
                    metric=downstream,
                    value=value,
                    unit="CNY",
                    currency="CNY",
                    period=period,
                    available_at=f"{available}T00:00:00+08:00",
                    fetched_at=stamp,
                    source=source,
                    transform=f"{upstream} 原值",
                )
            )
        if row.get("NOTICE_DATE"):
            facts.append(
                Fact.known(
                    fact_id=f"fin.{period}.notice_date",
                    metric="notice_date",
                    value=str(row["NOTICE_DATE"])[:10],
                    period=period,
                    fetched_at=stamp,
                    source=source,
                )
            )

    for period, interim_rows in sorted(store.interim.items()):
        interim = interim_rows.get(code)
        if not interim:
            continue
        available = str(interim.get("NOTICE_DATE") or "")[:10] or period
        for upstream, downstream in INCOME_MAP.items():
            value = _num(interim.get(upstream))
            if value is None:
                continue
            facts.append(
                Fact.known(
                    fact_id=f"fin.{period}.{downstream}",
                    metric=downstream,
                    value=value,
                    unit="CNY",
                    currency="CNY",
                    period=period,
                    available_at=f"{available}T00:00:00+08:00",
                    fetched_at=stamp,
                    source=source,
                    transform=f"{period} 累计季报 {upstream} 原值",
                )
            )

    # 分红事实：quality 池的分红表包含 2021–2025 中期/年度方案
    for row in store.dividend_rows(code):
        report_date = _date(row.get("REPORT_DATE"))
        label = _fiscal_label(report_date)
        if not label:
            continue
        profile = str(row.get("IMPL_PLAN_PROFILE") or "")
        kind = _dividend_kind(report_date, profile)
        per_10 = _num(row.get("PRETAX_BONUS_RMB"))
        if per_10 is None or per_10 < 0:
            continue
        status = str(row.get("ASSIGN_PROGRESS") or "")
        if "取消" in status or "终止" in status:
            continue
        base = f"div.{label}.{kind}"
        facts.append(
            Fact.known(
                fact_id=f"{base}.per_10",
                metric="dividend_per_10",
                value=per_10,
                period=label,
                available_at=(_date(row.get("NOTICE_DATE")) or report_date or "") + "T00:00:00+08:00",
                fetched_at=stamp,
                source=source,
                transform="PRETAX_BONUS_RMB（每 10 股税前派息）",
            )
        )
        facts.append(
            Fact.known(
                fact_id=f"{base}.status",
                metric="dividend_status",
                value="实施" if "实施" in status else status,
                period=label,
                fetched_at=stamp,
                source=source,
            )
        )

    price = price_override if price_override is not None else store.price_for(code)[0]
    price_date = (
        price_date_override
        if price_date_override is not None
        else store.price_for(code)[1]
    )
    as_of = as_of_override or price_date or stamp
    if price is not None and price_date:
        facts.append(
            Fact.known(
                fact_id="price.close",
                metric="close_price",
                value=float(price),
                unit="CNY_per_share",
                currency="CNY",
                period=price_date,
                available_at=f"{price_date}T15:00:00+08:00",
                fetched_at=stamp,
                source=_price_source(),
                transform="全市场行情快照收盘价（收盘前取上一交易日收盘）",
            )
        )
        facts.append(
            Fact.known(
                fact_id="price.bar_status",
                metric="bar_status",
                value="closed",
                period=price_date,
                fetched_at=stamp,
                source=_price_source(),
            )
        )

    shares = shares_override if shares_override is not None else store.shares_for(code)
    if shares is not None and shares > 0:
        facts.append(
            Fact.known(
                fact_id="profile.total_shares",
                metric="total_shares",
                value=float(shares),
                unit="shares",
                period=as_of,
                fetched_at=stamp,
                source=source,
                transform="最近一期股本变动总股本",
            )
        )

    coverage = {
        "price": {"source_id": "tencent.quote_snapshot", "price_date": price_date},
        "share_count": {"source_id": "eastmoney.f10.equity"},
        "financial_statements": {"source_ids": ["eastmoney.f10.batch_financials"]},
        "valuation_inputs": {"kind": "pool_valuation_batch", "snapshot_id": store.manifest["snapshot_id"]},
    }
    return Snapshot(
        instrument_id=f"EQUITY:{code}.{'SH' if code.startswith('6') else 'SZ'}",
        asset_class="equity",
        as_of=as_of,
        available_at=f"{as_of}T15:00:00+08:00",
        fetched_at=stamp,
        facts=facts,
        coverage=coverage,
        producers=["tools/build_pool_valuation_data.py", "engine/pool_valuation/adapter.py"],
        notes="由全市场批量表重建的个股输入；不修改原始个股快照。",
    )


def load_evidence(
    code: str,
    *,
    name: str | None = None,
    industry: str | None = None,
    snapshot: Snapshot | None = None,
) -> BusinessEvidence:
    code = str(code).zfill(6)
    venue = "SH" if code.startswith("6") else "SZ"
    path = POLICY_EVIDENCE / f"EQUITY-{code}.{venue}.json"
    if path.exists():
        return BusinessEvidence.from_dict(json.loads(path.read_text(encoding="utf-8")))
    annual = []
    if snapshot is not None:
        from engine.financials.annual import load_annual_history

        history = load_annual_history(snapshot)
        for year in sorted(history.years, key=lambda item: item.fiscal_year):
            annual.append(
                AnnualPoint(
                    fiscal_year=year.fiscal_year,
                    revenue=year.number("revenue"),
                    net_profit_parent=year.number("net_profit_parent"),
                )
            )
    display = name or f"{code}"
    return BusinessEvidence(
        instrument_id=f"EQUITY:{code}.{venue}",
        display_name=display,
        as_of="2026-11-13",
        version="batch-derived/1",
        prepared_at="2026-11-13T00:00:00+08:00",
        industry_clue=industry,
        segments=(),
        regional_segments=(),
        annual=tuple(annual),
        sources=(),
        notes=("由批量财务表与行业字段生成的最小业务证据；未含分部构成",),
    )


__all__ = ["BatchStore", "build_snapshot", "load_evidence"]
