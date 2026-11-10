"""模型适用性判断（阶段二实现）。

阶段一的立场：不判断经营形态、不选模型。
设计书 §6.1 要求先确定适用性再估值，所以这里明确返回 not_evaluated，
而不是用行业名称草率分流。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ApplicabilityResult:
    status: str  # evaluated / not_evaluated
    business_form: str | None
    primary_model: str | None
    required_checks: tuple[str, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "business_form": self.business_form,
            "primary_model": self.primary_model,
            "required_checks": list(self.required_checks),
            "reasons": list(self.reasons),
        }


def assess(instrument: Any) -> ApplicabilityResult:
    return ApplicabilityResult(
        status="not_evaluated",
        business_form=None,
        primary_model=None,
        required_checks=(),
        reasons=("阶段一不实现经营形态判断与模型分流（阶段二施工范围）",),
    )
