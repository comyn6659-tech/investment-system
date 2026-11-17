"""监测数据模型：事实（取自公告/报表/质押表）、红线命中、成员扫描结果。

所有字段只描述公开数据；不做推断，也不把缺失当成通过。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

STATUS_OK = "ok"
STATUS_HIT = "hit"
STATUS_INCOMPLETE = "incomplete"

RED_LINE_PENALTY = "penalty"
RED_LINE_AUDIT = "audit_opinion"
RED_LINE_PLEDGE = "pledge_ratio"


@dataclass(frozen=True)
class PenaltyEvent:
    date: str
    event_type: str
    title: str
    subject: str = ""
    role: str = ""
    counted: bool = False
    subject_unknown: bool = False
    info_disclosure: bool = False
    regulator: bool = False
    doc_no: str = ""
    url: str = ""
    evidence: str = ""
    pending: bool = False
    matter: str = ""
    basis: str = ""
    decision_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "event_type": self.event_type,
            "title": self.title,
            "subject": self.subject,
            "role": self.role,
            "counted": self.counted,
            "subject_unknown": self.subject_unknown,
            "info_disclosure": self.info_disclosure,
            "regulator": self.regulator,
            "doc_no": self.doc_no,
            "url": self.url,
            "evidence": self.evidence,
            "pending": self.pending,
            "matter": self.matter,
            "basis": self.basis,
            "decision_source": self.decision_source,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PenaltyEvent":
        return cls(
            date=str(data.get("date") or ""),
            event_type=str(data.get("event_type") or ""),
            title=str(data.get("title") or ""),
            subject=str(data.get("subject") or ""),
            role=str(data.get("role") or ""),
            counted=bool(data.get("counted")),
            subject_unknown=bool(data.get("subject_unknown")),
            info_disclosure=bool(data.get("info_disclosure")),
            regulator=bool(data.get("regulator")),
            doc_no=str(data.get("doc_no") or ""),
            url=str(data.get("url") or ""),
            evidence=str(data.get("evidence") or ""),
            pending=bool(data.get("pending")),
            matter=str(data.get("matter") or ""),
            basis=str(data.get("basis") or ""),
            decision_source=str(data.get("decision_source") or ""),
        )


@dataclass(frozen=True)
class GovernanceFacts:
    """一家公司的三项监测事实。

    `failures` 的键是 `penalty` / `audit` / `pledge`：取数失败或数据不完整，
    对应项记「本周未完成」，不默认通过。
    """

    code: str
    name: str = ""
    penalties: tuple[PenaltyEvent, ...] = ()
    audit_opinions: Mapping[int, str] = field(default_factory=dict)
    pledge_ratio: float | None = None
    pledge_subject: str = ""
    pledge_date: str = ""
    pledge_source: str = ""
    pledge_detail: str = ""
    failures: Mapping[str, str] = field(default_factory=dict)
    penalty_source: str = ""
    penalty_window: str = ""
    audit_sources: Mapping[int, str] = field(default_factory=dict)

    def failure(self, item: str) -> str | None:
        return self.failures.get(item)

    def audit_missing_years(self, years: list[int]) -> list[int]:
        return [year for year in years if not str(self.audit_opinions.get(year) or "").strip()]

    def audit_evidence(self) -> dict[str, dict[str, str]]:
        return {
            str(year): {
                "opinion": str(opinion),
                "source": str(self.audit_sources.get(year) or "未注明"),
            }
            for year, opinion in sorted(self.audit_opinions.items())
        }

    def pledge_evidence(self, *, as_of: str = "") -> dict[str, Any]:
        if self.failure("pledge"):
            status = "missing"
        elif self.pledge_ratio is None:
            status = "missing"
        elif self.pledge_ratio < 0 or self.pledge_ratio > 1:
            status = "rejected"
        else:
            status = "ok"
        return {
            "status": status,
            "ratio": self.pledge_ratio,
            "subject": self.pledge_subject,
            "as_of": self.pledge_date or as_of or "最新定期报告（未注明日期）",
            "source": self.pledge_source,
            "detail": self.pledge_detail or (self.failure("pledge") or ""),
        }

    def penalty_evidence(self, *, as_of: str = "") -> dict[str, Any]:
        return {
            "source": self.penalty_source or "未注明",
            "window": self.penalty_window or "",
            "events": [event.to_dict() for event in self.penalties],
            "failure": self.failure("penalty") or "",
            "as_of": as_of,
        }


@dataclass(frozen=True)
class RedLineHit:
    code: str
    name: str
    red_line: str
    label: str
    date: str
    doc_no: str = ""
    url: str = ""
    evidence: str = ""

    @property
    def reference(self) -> str:
        return self.doc_no or self.url or "文号缺失"

    def to_dict(self) -> dict[str, Any]:
        return {
            "red_line": self.red_line,
            "label": self.label,
            "date": self.date,
            "doc_no": self.doc_no,
            "url": self.url,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class Member:
    code: str
    name: str
    pools: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "name": self.name, "pools": list(self.pools)}


@dataclass(frozen=True)
class MemberScan:
    member: Member
    facts: GovernanceFacts
    hits: tuple[RedLineHit, ...]
    missing: tuple[str, ...]

    @property
    def code(self) -> str:
        return self.member.code

    @property
    def status(self) -> str:
        if self.hits:
            return STATUS_HIT
        if self.missing:
            return STATUS_INCOMPLETE
        return STATUS_OK

    @property
    def status_text(self) -> str:
        if self.status == STATUS_HIT:
            return self.exclusion_status()
        if self.status == STATUS_INCOMPLETE:
            return "本周未完成：" + "；".join(self.missing_notes())
        return "通过"

    def missing_notes(self) -> list[str]:
        notes = []
        for item in self.missing:
            reason = self.facts.failure(item)
            label = {"penalty": "公告检索", "audit": "审计意见", "pledge": "质押比例"}.get(item, item)
            if item == "penalty" and not reason:
                pending = [event for event in self.facts.penalties if event.pending]
                if pending:
                    reason = "待定：" + "；".join(event.basis or "主体/事项未确定" for event in pending)
                elif any(event.subject_unknown for event in self.facts.penalties):
                    reason = "主体身份不明，待定"
            notes.append(f"{label}（{reason}）" if reason else label)
        return notes

    def exclusion_status(self) -> str:
        if not self.hits:
            return ""
        first = self.hits[0]
        reason = first.label
        if len(self.hits) > 1:
            reason += f" 等 {len(self.hits)} 项"
        return f"出池（红线：{reason}，{first.date}，{first.reference}）"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.member.name,
            "pools": list(self.member.pools),
            "status": self.status,
            "status_text": self.status_text,
            "hits": [hit.to_dict() for hit in self.hits],
            "missing": list(self.missing),
            "missing_notes": self.missing_notes(),
            "audit_opinions": {str(k): v for k, v in sorted(self.facts.audit_opinions.items())},
            "pledge_ratio": self.facts.pledge_ratio,
            "pledge_subject": self.facts.pledge_subject,
            "evidence": {
                "pledge": self.facts.pledge_evidence(),
                "audit": self.facts.audit_evidence(),
                "penalty": self.facts.penalty_evidence(),
            },
        }


__all__ = [
    "GovernanceFacts",
    "Member",
    "MemberScan",
    "PenaltyEvent",
    "RedLineHit",
    "STATUS_HIT",
    "STATUS_INCOMPLETE",
    "STATUS_OK",
]
