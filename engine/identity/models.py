"""标的身份对象。

设计书 §5.1：不要用「六位数字=A股」这种规则充当识别器。
InstrumentRef 必须包含规范 ID、名称、别名、原始输入、市场/发布机构、
证券类型、币种、基金对应指数、范围判定、身份来源、有效期。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.contracts.enums import (
    AssetClass,
    EtfStrategy,
    IndexClass,
    Region,
    ScopeStatus,
)


@dataclass(frozen=True)
class EvidenceRef:
    """身份或事实的来源证据。"""

    evidence_id: str
    kind: str  # index_publisher / exchange_list / fund_profile / fund_contract / vendor_api
    publisher: str
    url: str | None
    retrieved_at: str
    fields_cited: tuple[str, ...] = ()
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kind": self.kind,
            "publisher": self.publisher,
            "url": self.url,
            "retrieved_at": self.retrieved_at,
            "fields_cited": list(self.fields_cited),
            "note": self.note,
        }


@dataclass(frozen=True)
class ScopeDecision:
    """范围判定结果及理由。"""

    status: ScopeStatus
    reason_code: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "reason_code": self.reason_code, "detail": self.detail}


@dataclass
class InstrumentRef:
    instrument_id: str
    display_name: str
    asset_class: AssetClass
    code: str
    market: str  # CN / HK / US
    venue: str  # SSE / SZSE / CSI / fund manager ...
    venue_type: str  # exchange / index_publisher / fund
    currency: str | None
    aliases: tuple[str, ...] = ()
    vendor_codes: tuple[str, ...] = ()
    index_class: IndexClass | None = None
    etf_strategy: EtfStrategy | None = None
    region: Region = Region.DOMESTIC
    underlying_index_id: str | None = None
    scope: ScopeDecision | None = None
    in_first_batch: bool = False
    data_coverage_verified: bool = False
    evidence: tuple[EvidenceRef, ...] = ()
    valid_from: str | None = None
    valid_to: str | None = None
    raw_input: str = ""
    identity_source: str = ""  # registry / live_catalog / heuristic

    # --- 展示 -----------------------------------------------------------
    @property
    def ticker(self) -> str:
        if self.asset_class == AssetClass.INDEX:
            return f"{self.code}.{self.venue}"
        return f"{self.code}.{self.venue}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "display_name": self.display_name,
            "asset_class": self.asset_class.value,
            "code": self.code,
            "market": self.market,
            "venue": self.venue,
            "venue_type": self.venue_type,
            "currency": self.currency,
            "aliases": list(self.aliases),
            "vendor_codes": list(self.vendor_codes),
            "index_class": self.index_class.value if self.index_class else None,
            "etf_strategy": self.etf_strategy.value if self.etf_strategy else None,
            "region": self.region.value,
            "underlying_index_id": self.underlying_index_id,
            "scope": self.scope.to_dict() if self.scope else None,
            "in_first_batch": self.in_first_batch,
            "data_coverage_verified": self.data_coverage_verified,
            "evidence": [e.to_dict() for e in self.evidence],
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "raw_input": self.raw_input,
            "identity_source": self.identity_source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InstrumentRef":
        scope = data.get("scope")
        return cls(
            instrument_id=data["instrument_id"],
            display_name=data["display_name"],
            asset_class=AssetClass(data["asset_class"]),
            code=data["code"],
            market=data["market"],
            venue=data["venue"],
            venue_type=data["venue_type"],
            currency=data.get("currency"),
            aliases=tuple(data.get("aliases", ())),
            vendor_codes=tuple(data.get("vendor_codes", ())),
            index_class=IndexClass(data["index_class"]) if data.get("index_class") else None,
            etf_strategy=EtfStrategy(data["etf_strategy"]) if data.get("etf_strategy") else None,
            region=Region(data.get("region", "domestic")),
            underlying_index_id=data.get("underlying_index_id"),
            scope=ScopeDecision(
                status=ScopeStatus(scope["status"]),
                reason_code=scope["reason_code"],
                detail=scope.get("detail", ""),
            )
            if scope
            else None,
            in_first_batch=bool(data.get("in_first_batch", False)),
            data_coverage_verified=bool(data.get("data_coverage_verified", False)),
            evidence=tuple(EvidenceRef(**e) for e in data.get("evidence", ())),
            valid_from=data.get("valid_from"),
            valid_to=data.get("valid_to"),
            raw_input=data.get("raw_input", ""),
            identity_source=data.get("identity_source", ""),
        )


@dataclass(frozen=True)
class Resolution:
    """解析结果：唯一标的、候选列表、或明确失败。"""

    status: str  # ResolutionStatus 的值
    raw_input: str
    instrument: InstrumentRef | None = None
    candidates: tuple[InstrumentRef, ...] = ()
    reason_codes: tuple[str, ...] = ()
    clarification_question: str | None = None
    matched_by: str = ""  # exact_id / exact_name / alias / code / substring

    @property
    def is_resolved(self) -> bool:
        return self.status == "resolved" and self.instrument is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "raw_input": self.raw_input,
            "matched_by": self.matched_by,
            "instrument": self.instrument.to_dict() if self.instrument else None,
            "candidates": [c.to_dict() for c in self.candidates],
            "reason_codes": list(self.reason_codes),
            "clarification_question": self.clarification_question,
        }
