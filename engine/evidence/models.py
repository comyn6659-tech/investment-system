"""结构化业务证据：只装事实与出处，不装主观结论。

证据文件由 `tools/build_business_evidence.py` 从公开接口生成，内容哈希即
`evidence_id`。模型适用性判定在 `applicability.py` 里，依据这些事实与显式规则
完成，并把判定理由、指标与规则版本一起保存到分析结果。
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from engine.data.snapshot import stable_digest

EVIDENCE_SCHEMA_VERSION = "business-evidence/1"


class EvidenceError(RuntimeError):
    """证据文件缺失或结构不合法。"""


@dataclass(frozen=True)
class Segment:
    segment: str
    revenue_share: float | None
    revenue: float | None = None
    gross_margin: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "segment": self.segment,
            "revenue_share": self.revenue_share,
            "revenue": self.revenue,
            "gross_margin": self.gross_margin,
        }


@dataclass(frozen=True)
class AnnualPoint:
    fiscal_year: int
    revenue: float | None
    net_profit_parent: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "fiscal_year": self.fiscal_year,
            "revenue": self.revenue,
            "net_profit_parent": self.net_profit_parent,
        }


@dataclass(frozen=True)
class BusinessEvidence:
    instrument_id: str
    display_name: str
    as_of: str
    version: str
    prepared_at: str
    industry_clue: str | None
    segments: tuple[Segment, ...]
    regional_segments: tuple[Segment, ...]
    annual: tuple[AnnualPoint, ...]
    sources: tuple[dict[str, Any], ...] = ()
    notes: tuple[str, ...] = ()
    schema_version: str = EVIDENCE_SCHEMA_VERSION

    # --- 身份 -----------------------------------------------------------
    def _content_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "instrument_id": self.instrument_id,
            "display_name": self.display_name,
            "as_of": self.as_of,
            "version": self.version,
            "prepared_at": self.prepared_at,
            "industry_clue": self.industry_clue,
            "segments": [s.to_dict() for s in self.segments],
            "regional_segments": [s.to_dict() for s in self.regional_segments],
            "annual": [a.to_dict() for a in self.annual],
            "sources": list(self.sources),
            "notes": list(self.notes),
        }

    @property
    def evidence_id(self) -> str:
        return "biz-" + stable_digest(self._content_payload())

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_payload()
        payload["evidence_id"] = self.evidence_id
        return payload

    # --- 指标 -----------------------------------------------------------
    def revenue_growth_series(self) -> list[float]:
        """按年排序的营业收入同比增速（只用相邻两年都有的数据）。"""
        points = sorted(
            [a for a in self.annual if a.revenue is not None], key=lambda a: a.fiscal_year
        )
        growth: list[float] = []
        for prior, current in zip(points, points[1:]):
            if not prior.revenue:
                continue
            growth.append((current.revenue or 0.0) / prior.revenue - 1.0)
        return growth

    def revenue_volatility(self) -> float | None:
        """同比增速的样本标准差：周期性/不稳定的粗略量化，不是预测。"""
        growth = self.revenue_growth_series()
        return statistics.pstdev(growth) if len(growth) >= 2 else None

    def revenue_drawdown(self) -> float | None:
        """窗口内最大峰谷回撤（正数表示跌幅）。"""
        revenues = [a.revenue for a in sorted(self.annual, key=lambda a: a.fiscal_year) if a.revenue]
        if len(revenues) < 2:
            return None
        peak = revenues[0]
        worst = 0.0
        for value in revenues:
            peak = max(peak, value)
            if peak > 0:
                worst = max(worst, (peak - value) / peak)
        return worst

    def loss_years(self, *, recent: int = 4) -> tuple[int, ...]:
        ordered = sorted(self.annual, key=lambda a: a.fiscal_year, reverse=True)[:recent]
        return tuple(
            a.fiscal_year
            for a in ordered
            if a.net_profit_parent is not None and a.net_profit_parent < 0
        )

    def latest_annual(self) -> AnnualPoint | None:
        ordered = sorted(self.annual, key=lambda a: a.fiscal_year, reverse=True)
        return ordered[0] if ordered else None

    def top_segments(self, limit: int = 3) -> tuple[Segment, ...]:
        ranked = sorted(
            [s for s in self.segments if s.revenue_share is not None],
            key=lambda s: s.revenue_share or 0.0,
            reverse=True,
        )
        return tuple(ranked[:limit])

    # --- 反序列化 -------------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BusinessEvidence":
        if data.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
            raise EvidenceError(f"未知的业务证据 schema: {data.get('schema_version')!r}")
        mix = data.get("business_mix") or {}
        evidence = cls(
            instrument_id=str(data["instrument_id"]),
            display_name=str(data.get("display_name", "")),
            as_of=str(data.get("as_of", "")),
            version=str(data.get("version", "")),
            prepared_at=str(data.get("prepared_at", "")),
            industry_clue=data.get("industry_clue"),
            segments=tuple(_segment(item) for item in mix.get("by_product") or ()),
            regional_segments=tuple(_segment(item) for item in mix.get("by_region") or ()),
            annual=tuple(
                AnnualPoint(
                    fiscal_year=int(item["fiscal_year"]),
                    revenue=item.get("revenue"),
                    net_profit_parent=item.get("net_profit_parent"),
                )
                for item in data.get("annual_history") or ()
            ),
            sources=tuple(data.get("sources") or ()),
            notes=tuple(data.get("notes") or ()),
            schema_version=data.get("schema_version", EVIDENCE_SCHEMA_VERSION),
        )
        declared = data.get("evidence_id")
        if declared is not None and declared != evidence.evidence_id:
            raise EvidenceError(
                f"证据文件声明 {declared}，内容哈希为 {evidence.evidence_id}：内容已被改动"
            )
        return evidence


def _segment(item: dict[str, Any]) -> Segment:
    return Segment(
        segment=str(item.get("segment", "")),
        revenue_share=item.get("revenue_share"),
        revenue=item.get("revenue"),
        gross_margin=item.get("gross_margin"),
    )


class EvidenceBook:
    """证据目录：按 instrument_id 取结构化业务证据。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._cache: dict[str, BusinessEvidence] = {}
        self._errors: dict[str, str] = {}
        if self.root.exists():
            for path in sorted(self.root.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    evidence = BusinessEvidence.from_dict(data)
                except Exception as exc:  # 损坏的文件不当作有效证据
                    self._errors[path.name] = f"{type(exc).__name__}: {exc}"
                    continue
                self._cache[evidence.instrument_id] = evidence

    def get(self, instrument_id: str) -> BusinessEvidence | None:
        return self._cache.get(instrument_id)

    @property
    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    def instrument_ids(self) -> Iterable[str]:
        return sorted(self._cache)


__all__ = [
    "AnnualPoint",
    "BusinessEvidence",
    "EVIDENCE_SCHEMA_VERSION",
    "EvidenceBook",
    "EvidenceError",
    "Segment",
]
