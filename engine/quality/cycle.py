"""周期属性标记：沿用个股政策的 `classify_family`，只标记，不改好公司池规则。

输入是好公司池快照里的 10 年营业收入与归母净利润；判定阈值全部来自
`engine.evidence.applicability.SELECTION_POLICY`，与个股政策共用同一套逻辑。
"""

from __future__ import annotations

from typing import Any

from engine.evidence.applicability import FAMILY_LABELS, classify_family
from engine.evidence.models import AnnualPoint, BusinessEvidence
from engine.quality.models import Company


def classify_cycle(company: Company) -> dict[str, Any]:
    annual = tuple(
        AnnualPoint(
            fiscal_year=year,
            revenue=item.revenue,
            net_profit_parent=item.parent_netprofit,
        )
        for year, item in sorted(company.years.items())
        if year >= 2016
    )
    evidence = BusinessEvidence(
        instrument_id=f"EQUITY:{company.code}.{company.exchange}",
        display_name=company.name,
        as_of="2026-11-12",
        version="quality-pool-cycle/1",
        prepared_at="2026-11-12T00:00:00+08:00",
        industry_clue=company.industry,
        segments=(),
        regional_segments=(),
        annual=annual,
    )
    family, reasons = classify_family(evidence)
    return {
        "family": family,
        "label": FAMILY_LABELS.get(family, family),
        "is_cyclical": family in ("cyclical", "distressed"),
        "reasons": reasons,
    }
