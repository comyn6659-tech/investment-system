"""好公司池的数据模型：公司、财年、条件结果与整体运行结果。

所有字段都来自固定快照；本模块不做政策判断，也不补默认值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

# 公司层状态
STATUS_IN_POOL = "in_pool"
STATUS_NOT_IN_POOL = "not_in_pool"
STATUS_PENDING = "pending"
STATUS_OUT_OF_SCOPE = "out_of_scope"

# 条件层状态
CONDITION_PASS = "pass"
CONDITION_FAIL = "fail"
CONDITION_PENDING = "pending"
CONDITION_UNTESTED = "untested"

OUT_OF_SCOPE_ST = "ST"
OUT_OF_SCOPE_FINANCIAL = "FINANCIAL_INDUSTRY"
OUT_OF_SCOPE_SHORT_HISTORY = "SHORT_HISTORY"
OUT_OF_SCOPE_MARKET = "OUT_OF_SCOPE_MARKET"


@dataclass(frozen=True)
class FinancialYear:
    fiscal_year: int
    parent_netprofit: float | None = None
    revenue: float | None = None
    parent_equity: float | None = None
    operating_cashflow: float | None = None
    capex: float | None = None
    total_profit: float | None = None
    interest_expense: float | None = None
    depreciation: float | None = None
    amortization: float | None = None
    interest_debt: float | None = None
    monetary_funds: float | None = None

    @property
    def ebitda(self) -> float | None:
        if self.total_profit is None or self.interest_expense is None:
            return None
        return (
            self.total_profit
            + self.interest_expense
            + (self.depreciation or 0.0)
            + (self.amortization or 0.0)
        )

    @property
    def net_debt(self) -> float | None:
        if self.interest_debt is None or self.monetary_funds is None:
            return None
        return self.interest_debt - self.monetary_funds

    @property
    def free_cash_flow(self) -> float | None:
        if self.operating_cashflow is None or self.capex is None:
            return None
        return self.operating_cashflow - self.capex

    def to_dict(self) -> dict[str, Any]:
        return {
            "fiscal_year": self.fiscal_year,
            "parent_netprofit": self.parent_netprofit,
            "revenue": self.revenue,
            "parent_equity": self.parent_equity,
            "operating_cashflow": self.operating_cashflow,
            "capex": self.capex,
            "total_profit": self.total_profit,
            "interest_expense": self.interest_expense,
            "depreciation": self.depreciation,
            "amortization": self.amortization,
            "interest_debt": self.interest_debt,
            "monetary_funds": self.monetary_funds,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FinancialYear":
        return cls(
            fiscal_year=int(payload["fiscal_year"]),
            parent_netprofit=payload.get("parent_netprofit"),
            revenue=payload.get("revenue"),
            parent_equity=payload.get("parent_equity"),
            operating_cashflow=payload.get("operating_cashflow"),
            capex=payload.get("capex"),
            total_profit=payload.get("total_profit"),
            interest_expense=payload.get("interest_expense"),
            depreciation=payload.get("depreciation"),
            amortization=payload.get("amortization"),
            interest_debt=payload.get("interest_debt"),
            monetary_funds=payload.get("monetary_funds"),
        )


@dataclass(frozen=True)
class GovernancePenalty:
    date: str
    event_type: str
    title: str
    reason: str | None = None
    doc_no: str | None = None
    url: str | None = None
    info_disclosure: bool = False
    counted: bool = False
    subject: str | None = None
    subject_role: str | None = None
    subject_basis: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "event_type": self.event_type,
            "title": self.title,
            "reason": self.reason,
            "doc_no": self.doc_no,
            "url": self.url,
            "info_disclosure": self.info_disclosure,
            "counted": self.counted,
            "subject": self.subject,
            "subject_role": self.subject_role,
            "subject_basis": self.subject_basis,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GovernancePenalty":
        return cls(
            date=str(payload.get("date") or payload.get("event_date") or ""),
            event_type=str(payload.get("event_type") or ""),
            title=str(payload.get("title") or ""),
            reason=payload.get("reason"),
            doc_no=payload.get("doc_no"),
            url=payload.get("url"),
            info_disclosure=str(payload.get("info_disclosure")).lower() in ("true", "1", "yes"),
            counted=str(payload.get("counted")).lower() in ("true", "1", "yes"),
            subject=payload.get("subject"),
            subject_role=payload.get("subject_role"),
            subject_basis=payload.get("subject_basis"),
        )


@dataclass(frozen=True)
class Company:
    code: str
    name: str
    exchange: str
    board: str
    industry: str | None
    listing_date: str | None
    is_st: bool
    financial_excluded: bool
    financial_exclusion_reason: str | None
    expected_years: tuple[int, ...]
    years: Mapping[int, FinancialYear]
    dividends: Mapping[int, float | None]
    pledge_company_ratio: float | None = None
    audit_opinions: Mapping[int, str] = field(default_factory=dict)
    audit_available: bool = False
    penalties: tuple[GovernancePenalty, ...] = ()
    penalty_available: bool = False
    controlling_shareholder: str | None = None
    controlling_pledge_ratio: float | None = None
    pledge_report_date: str | None = None
    pledge_available: bool = False
    pledge_note: str | None = None
    pledge_subject_role: str | None = None
    pledge_detail: str | None = None
    pledge_source_url: str | None = None
    penalty_subject_unknown: bool = False
    governance_in_scope: bool = False

    @property
    def window_years(self) -> tuple[int, ...]:
        return self.expected_years

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "exchange": self.exchange,
            "board": self.board,
            "industry": self.industry,
            "listing_date": self.listing_date,
            "is_st": self.is_st,
            "financial_excluded": self.financial_excluded,
            "financial_exclusion_reason": self.financial_exclusion_reason,
            "expected_years": list(self.expected_years),
            "years": {
                str(year): self.years[year].to_dict()
                for year in sorted(self.years)
            },
            "dividends": {str(year): value for year, value in sorted(self.dividends.items())},
            "pledge_company_ratio": self.pledge_company_ratio,
            "audit_opinions": {str(year): value for year, value in sorted(self.audit_opinions.items())},
            "audit_available": self.audit_available,
            "penalties": [item.to_dict() for item in self.penalties],
            "penalty_available": self.penalty_available,
            "controlling_shareholder": self.controlling_shareholder,
            "controlling_pledge_ratio": self.controlling_pledge_ratio,
            "pledge_report_date": self.pledge_report_date,
            "pledge_available": self.pledge_available,
            "pledge_note": self.pledge_note,
            "pledge_subject_role": self.pledge_subject_role,
            "pledge_detail": self.pledge_detail,
            "pledge_source_url": self.pledge_source_url,
            "penalty_subject_unknown": self.penalty_subject_unknown,
            "governance_in_scope": self.governance_in_scope,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Company":
        years = {
            int(year): FinancialYear.from_dict(value)
            for year, value in (payload.get("years") or {}).items()
        }
        return cls(
            code=str(payload["code"]),
            name=str(payload["name"]),
            exchange=str(payload["exchange"]),
            board=str(payload["board"]),
            industry=payload.get("industry"),
            listing_date=payload.get("listing_date"),
            is_st=bool(payload.get("is_st")),
            financial_excluded=bool(payload.get("financial_excluded")),
            financial_exclusion_reason=payload.get("financial_exclusion_reason"),
            expected_years=tuple(int(year) for year in payload.get("expected_years") or []),
            years=years,
            dividends={
                int(year): value
                for year, value in (payload.get("dividends") or {}).items()
            },
            pledge_company_ratio=payload.get("pledge_company_ratio"),
            audit_opinions={
                int(year): value
                for year, value in (payload.get("audit_opinions") or {}).items()
            },
            audit_available=bool(payload.get("audit_available")),
            penalties=tuple(
                GovernancePenalty.from_dict(item)
                for item in payload.get("penalties") or []
            ),
            penalty_available=bool(payload.get("penalty_available")),
            controlling_shareholder=payload.get("controlling_shareholder"),
            controlling_pledge_ratio=payload.get("controlling_pledge_ratio"),
            pledge_report_date=payload.get("pledge_report_date"),
            pledge_available=bool(payload.get("pledge_available")),
            pledge_note=payload.get("pledge_note"),
            pledge_subject_role=payload.get("pledge_subject_role"),
            pledge_detail=payload.get("pledge_detail"),
            pledge_source_url=payload.get("pledge_source_url"),
            penalty_subject_unknown=bool(payload.get("penalty_subject_unknown")),
            governance_in_scope=bool(payload.get("governance_in_scope")),
        )


@dataclass
class MetricValue:
    value: float | int | None
    missing: tuple[str, ...] = ()
    note: str | None = None

    @property
    def available(self) -> bool:
        return self.value is not None


@dataclass
class ConditionResult:
    condition_id: str
    group_id: str
    label: str
    status: str
    value: float | int | None = None
    threshold: float | int | None = None
    distance_abs: float | None = None
    distance_rel: float | None = None
    reason: str | None = None
    missing: tuple[str, ...] = ()
    unit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "group_id": self.group_id,
            "label": self.label,
            "status": self.status,
            "value": self.value,
            "threshold": self.threshold,
            "distance_abs": self.distance_abs,
            "distance_rel": self.distance_rel,
            "reason": self.reason,
            "missing": list(self.missing),
            "unit": self.unit,
        }


@dataclass
class CompanyResult:
    code: str
    name: str
    exchange: str
    board: str
    industry: str | None
    listing_date: str | None
    status: str
    reason: str | None
    window_years: tuple[int, ...]
    available_years: tuple[int, ...]
    missing_years: tuple[int, ...]
    conditions: list[ConditionResult] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    financial_excluded: bool = False

    @property
    def failing(self) -> list[ConditionResult]:
        return [item for item in self.conditions if item.status == CONDITION_FAIL]

    @property
    def pending(self) -> list[ConditionResult]:
        return [item for item in self.conditions if item.status == CONDITION_PENDING]

    @property
    def untested(self) -> list[ConditionResult]:
        return [item for item in self.conditions if item.status == CONDITION_UNTESTED]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "exchange": self.exchange,
            "board": self.board,
            "industry": self.industry,
            "listing_date": self.listing_date,
            "status": self.status,
            "reason": self.reason,
            "financial_excluded": self.financial_excluded,
            "window_years": list(self.window_years),
            "available_years": list(self.available_years),
            "missing_years": list(self.missing_years),
            "metrics": self.metrics,
            "conditions": [item.to_dict() for item in self.conditions],
        }


@dataclass
class PoolRun:
    as_of: str
    policy_id: str
    policy_digest: str
    snapshot_id: str
    generated_at: str
    counts: dict[str, int]
    results: list[CompanyResult]
    untested_conditions: list[dict[str, Any]]
    funnel: list[dict[str, Any]]
    near_misses: list[dict[str, Any]]
    sensitivity: dict[str, Any]
    industry_distribution: list[dict[str, Any]]
    test_set: list[dict[str, Any]]

    @property
    def in_pool(self) -> list[CompanyResult]:
        return [item for item in self.results if item.status == STATUS_IN_POOL]

    @property
    def pending(self) -> list[CompanyResult]:
        return [item for item in self.results if item.status == STATUS_PENDING]

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "policy_id": self.policy_id,
            "policy_digest": self.policy_digest,
            "snapshot_id": self.snapshot_id,
            "generated_at": self.generated_at,
            "counts": self.counts,
            "untested_conditions": self.untested_conditions,
            "funnel": self.funnel,
            "near_misses": self.near_misses,
            "sensitivity": self.sensitivity,
            "industry_distribution": self.industry_distribution,
            "test_set": self.test_set,
            "results": [item.to_dict() for item in self.results],
        }
