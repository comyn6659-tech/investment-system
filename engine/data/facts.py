"""带状态的数据事实（Fact）。

设计书 §5.2：每个事实要能追到来源、原始科目、值、单位、报告期、
公告/可获得时间、抓取时间、是否重述、转换规则；并且必须区分
已知零 / 缺失 / 不适用 / 过期 / 冲突。

这里的原则是：
- 不允许「缺失」用 0 表示；
- 不允许「已知为零」和「缺失」在类型上混同；
- 不允许 NaN / Infinity 进入 value。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.contracts.enums import DataStatus
from engine.data.numeric import require_finite


class FactError(ValueError):
    """事实对象本身违反契约。"""


@dataclass(frozen=True)
class SourceRef:
    """一条可追溯的来源。"""

    source_id: str
    publisher: str
    interface: str
    url: str | None = None
    retrieved_at: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "publisher": self.publisher,
            "interface": self.interface,
            "url": self.url,
            "retrieved_at": self.retrieved_at,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class Fact:
    """一个带状态与来源的事实。

    value 的类型取决于 status：
    - known / known_zero / stale：必须是有限数值或字符串；
    - missing / not_applicable：value 必须为 None；
    - conflict：value 必须为 None，取值放在 conflict_values。
    """

    fact_id: str
    metric: str
    status: DataStatus
    value: Any
    unit: str | None = None
    currency: str | None = None
    period: str | None = None
    available_at: str | None = None
    fetched_at: str | None = None
    source: SourceRef | None = None
    transform: str = ""
    restated: bool = False
    notes: str = ""
    conflict_values: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        problems = self.validate()
        if problems:
            raise FactError("; ".join(problems))

    # --- 构造器 ---------------------------------------------------------
    @classmethod
    def known(cls, *, fact_id: str, metric: str, value: Any, **kw: Any) -> "Fact":
        _require_value_bearing(value, fact_id=fact_id)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            require_finite(value, field=fact_id)
        return cls(fact_id=fact_id, metric=metric, status=DataStatus.KNOWN, value=value, **kw)

    @classmethod
    def known_zero(cls, *, fact_id: str, metric: str, **kw: Any) -> "Fact":
        return cls(fact_id=fact_id, metric=metric, status=DataStatus.KNOWN_ZERO, value=0, **kw)

    @classmethod
    def missing(cls, *, fact_id: str, metric: str, **kw: Any) -> "Fact":
        return cls(fact_id=fact_id, metric=metric, status=DataStatus.MISSING, value=None, **kw)

    @classmethod
    def not_applicable(cls, *, fact_id: str, metric: str, **kw: Any) -> "Fact":
        return cls(fact_id=fact_id, metric=metric, status=DataStatus.NOT_APPLICABLE, value=None, **kw)

    @classmethod
    def stale(cls, *, fact_id: str, metric: str, value: Any, **kw: Any) -> "Fact":
        _require_value_bearing(value, fact_id=fact_id)
        return cls(fact_id=fact_id, metric=metric, status=DataStatus.STALE, value=value, **kw)

    @classmethod
    def conflict(cls, *, fact_id: str, metric: str, values: list[Any], **kw: Any) -> "Fact":
        if len(values) < 2:
            raise FactError(f"{fact_id}: 冲突状态至少要两个取值")
        return cls(
            fact_id=fact_id,
            metric=metric,
            status=DataStatus.CONFLICT,
            value=None,
            conflict_values=tuple(values),
            **kw,
        )

    # --- 语义 -----------------------------------------------------------
    @property
    def is_usable_number(self) -> bool:
        """能否作为数值参与计算。缺失/不适用/冲突一律不能。"""
        return (
            self.status in (DataStatus.KNOWN, DataStatus.KNOWN_ZERO, DataStatus.STALE)
            and isinstance(self.value, (int, float))
            and not isinstance(self.value, bool)
        )

    @property
    def is_zero(self) -> bool:
        return self.status == DataStatus.KNOWN_ZERO

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "metric": self.metric,
            "status": self.status.value,
            "value": self.value,
            "unit": self.unit,
            "currency": self.currency,
            "period": self.period,
            "available_at": self.available_at,
            "fetched_at": self.fetched_at,
            "source": self.source.to_dict() if self.source else None,
            "transform": self.transform,
            "restated": self.restated,
            "notes": self.notes,
            "conflict_values": list(self.conflict_values),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Fact":
        source = data.get("source")
        return cls(
            fact_id=data["fact_id"],
            metric=data["metric"],
            status=DataStatus(data["status"]),
            value=data.get("value"),
            unit=data.get("unit"),
            currency=data.get("currency"),
            period=data.get("period"),
            available_at=data.get("available_at"),
            fetched_at=data.get("fetched_at"),
            source=SourceRef(**source) if source else None,
            transform=data.get("transform", ""),
            restated=bool(data.get("restated", False)),
            notes=data.get("notes", ""),
            conflict_values=tuple(data.get("conflict_values", ())),
        )

    def validate(self) -> list[str]:
        """返回违规说明列表（空表示通过）。"""
        problems: list[str] = []
        if not self.fact_id:
            problems.append("fact_id 为空")
        if self.status in (DataStatus.MISSING, DataStatus.NOT_APPLICABLE):
            if self.value is not None:
                problems.append(f"{self.fact_id}: {self.status.value} 状态必须 value=None")
        if self.status == DataStatus.KNOWN_ZERO:
            if self.value != 0:
                problems.append(f"{self.fact_id}: known_zero 状态 value 必须为 0")
        if self.status == DataStatus.CONFLICT:
            if self.value is not None:
                problems.append(f"{self.fact_id}: conflict 状态 value 必须为 None")
            if len(self.conflict_values) < 2:
                problems.append(f"{self.fact_id}: conflict 状态需要至少两个来源取值")
        if self.correlation_hint():
            problems.append(f"{self.fact_id}: 禁止把指数点位标成人民币金额")
        return problems

    def correlation_hint(self) -> bool:
        return (self.unit or "").startswith("index_point") and bool(self.currency)


def _require_value_bearing(value: Any, *, fact_id: str) -> None:
    if value is None:
        raise FactError(f"{fact_id}: 带值状态不能传 None（缺失请用 Fact.missing）")
    if isinstance(value, float) and value != value:  # NaN
        raise FactError(f"{fact_id}: 不接受 NaN")
