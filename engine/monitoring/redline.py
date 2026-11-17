"""红线判定：把公告、审计意见、质押比例三类事实按已批准口径判成「通过/命中/未完成」。

口径与好公司池 v1.2 治理条件逐项相同（见 `data/policies/pool-monitoring-v1.json`）：
- 立案 / 信息披露类行政处罚 / 公开谴责：最近 5 年，0 件；
- 审计意见：最近 5 个财年均为标准无保留，0 年非标；
- 控股股东（无控股股东时第一大股东）质押比例 ≤ 50%。

取数失败或数据缺失记入 `missing`，既不默认通过，也不剔除。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from typing import Protocol, Sequence

from engine.monitoring.models import (
    GovernanceFacts,
    Member,
    MemberScan,
    PenaltyEvent,
    RedLineHit,
    STATUS_HIT,
    STATUS_INCOMPLETE,
    STATUS_OK,
)
from engine.monitoring.policy import MonitoringPolicy

_PENALTY_LABELS = {
    "立案": "证监会立案调查",
    "行政处罚": "信息披露类行政处罚",
    "公开谴责": "交易所公开谴责",
    "纪律处分": "交易所纪律处分（公开谴责）",
}
_AUDIT_STANDARD = "标准无保留意见"


class GovernanceSource(Protocol):
    """取数源协议：生产用东方财富，测试用夹具。"""

    def fetch(self, code: str, name: str = "") -> GovernanceFacts: ...


@dataclass(frozen=True)
class ScanReport:
    as_of: str
    members: tuple[MemberScan, ...]

    @property
    def total(self) -> int:
        return len(self.members)

    @property
    def hits(self) -> list[MemberScan]:
        return [item for item in self.members if item.status == STATUS_HIT]

    @property
    def incomplete(self) -> list[MemberScan]:
        return [item for item in self.members if item.status == STATUS_INCOMPLETE]

    @property
    def ok(self) -> list[MemberScan]:
        return [item for item in self.members if item.status == STATUS_OK]

    @property
    def pending(self) -> list[MemberScan]:
        return [
            item
            for item in self.members
            if any(event.pending for event in item.facts.penalties)
        ]

    @property
    def live_checked(self) -> int:
        """本次扫描真实查询过质押明细的公司数。"""
        return sum(1 for item in self.members if item.facts.pledge_live_checked)

    @property
    def covered(self) -> int:
        return sum(1 for item in self.members if item.status != STATUS_INCOMPLETE)

    @property
    def coverage(self) -> float:
        return (self.covered / self.total) if self.total else 0.0

    def counts(self) -> dict[str, int]:
        return {
            "members": self.total,
            "covered": self.covered,
            "hit": len(self.hits),
            "incomplete": len(self.incomplete),
            "ok": len(self.ok),
        }

    def to_dict(self) -> dict:
        return {"as_of": self.as_of, "counts": self.counts(), "members": [item.to_dict() for item in self.members]}


def audit_years(as_of: date, *, window_years: int = 5) -> list[int]:
    """最近 N 个应交年报的财年。

    A 股年报法定披露截止 4 月 30 日：4 月 30 日之前，上一年年报尚未到期，
    取再前一年作为最新财年；之后取上一年。
    """
    latest = as_of.year - 1 if (as_of.month, as_of.day) >= (4, 30) else as_of.year - 2
    return list(range(latest - window_years + 1, latest + 1))


def _five_years_before(day: date) -> date:
    try:
        return day.replace(year=day.year - 5)
    except ValueError:
        return day.replace(year=day.year - 5, day=28)


def _penalty_label(event: PenaltyEvent) -> str:
    label = _PENALTY_LABELS.get(event.event_type, event.event_type)
    subject = event.subject or "主体未注明"
    return f"{label}（{subject}）"


def _audit_label(year: int, opinion: str) -> str:
    return f"{year} 年审计意见：{opinion}"


def _pledge_label(ratio: float, subject: str) -> str:
    who = subject or "控股股东"
    return f"控股股东质押比例 {ratio:.2%}（{who}）"


def evaluate_member(
    member: Member,
    facts: GovernanceFacts,
    policy: MonitoringPolicy,
    *,
    as_of: date,
) -> MemberScan:
    red_lines = policy.red_lines
    penalty_rule = policy.red_line("penalty")
    audit_rule = policy.red_line("audit_opinion")
    pledge_rule = policy.red_line("pledge_ratio")

    hits: list[RedLineHit] = []
    missing: list[str] = []

    # --- 公告：立案 / 信息披露类行政处罚 / 公开谴责 ---------------------
    if facts.failure("penalty"):
        missing.append("penalty")
    else:
        start = _five_years_before(as_of)
        unknown = False
        pending = False
        for event in facts.penalties:
            event_day = event.date[:10]
            if event_day and event_day < start.isoformat():
                continue
            if event.counted:
                hits.append(
                    RedLineHit(
                        code=member.code,
                        name=member.name,
                        red_line="penalty",
                        label=_penalty_label(event),
                        date=event_day,
                        doc_no=event.doc_no,
                        url=event.url,
                        evidence=event.evidence or event.title,
                    )
                )
            elif event.pending:
                pending = True
            elif event.subject_unknown:
                unknown = True
        if (pending or unknown) and not hits:
            missing.append("penalty")

    # --- 审计意见：最近 5 个财年均为标准无保留 ---------------------------
    if facts.failure("audit"):
        missing.append("audit")
    else:
        years = audit_years(as_of, window_years=int(audit_rule.get("window_years") or 5))
        absent = facts.audit_missing_years(years)
        if absent:
            missing.append("audit")
        for year in years:
            opinion = str(facts.audit_opinions.get(year) or "").strip()
            if opinion and opinion != _AUDIT_STANDARD:
                hits.append(
                    RedLineHit(
                        code=member.code,
                        name=member.name,
                        red_line="audit_opinion",
                        label=_audit_label(year, opinion),
                        date=f"{year}-12-31",
                        doc_no="",
                        url="",
                        evidence=f"{year} 年年报审计意见：{opinion}",
                    )
                )

    # --- 质押比例 -------------------------------------------------------
    if facts.failure("pledge"):
        missing.append("pledge")
    elif facts.pledge_ratio is None or not 0 <= facts.pledge_ratio <= 1:
        # R3：质押比例 >100% 视为明细不可用，记未完成，不得据此剔除。
        missing.append("pledge")
    else:
        maximum = float(pledge_rule.get("max", 0.5))
        if facts.pledge_ratio > maximum:
            hits.append(
                RedLineHit(
                    code=member.code,
                    name=member.name,
                    red_line="pledge_ratio",
                    label=_pledge_label(facts.pledge_ratio, facts.pledge_subject),
                    date=str(facts.pledge_date or as_of.isoformat()),
                    doc_no="",
                    url=facts.pledge_source,
                    evidence=facts.pledge_detail
                    or f"{facts.pledge_subject or '控股股东'} 质押 {facts.pledge_ratio:.2%}",
                )
            )

    return MemberScan(member=member, facts=facts, hits=tuple(hits), missing=tuple(dict.fromkeys(missing)))


def _facts_for(member: Member, source: GovernanceSource) -> GovernanceFacts:
    try:
        return source.fetch(member.code, member.name)
    except Exception as exc:  # noqa: BLE001 - 单家取数失败不阻断全表，记未完成
        message = f"{type(exc).__name__}: {exc}"
        return GovernanceFacts(
            code=member.code,
            name=member.name,
            failures={"penalty": message, "audit": message, "pledge": message},
        )


def scan_members(
    members: Sequence[Member],
    source: GovernanceSource,
    policy: MonitoringPolicy,
    *,
    as_of: date,
    workers: int = 1,
) -> ScanReport:
    if workers <= 1:
        facts_list = [_facts_for(member, source) for member in members]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            facts_list = list(executor.map(lambda member: _facts_for(member, source), members))
    results = [
        evaluate_member(member, facts, policy, as_of=as_of)
        for member, facts in zip(members, facts_list)
    ]
    return ScanReport(as_of=as_of.isoformat(), members=tuple(results))


__all__ = [
    "GovernanceSource",
    "ScanReport",
    "audit_years",
    "evaluate_member",
    "scan_members",
]
