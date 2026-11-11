"""研究假设配置：独立、可保存、可重放、带来源与未批准状态。

设计要点（施工指令 §3.3）：

- 每组假设必须记录：标的、模型 ID/版本、适用日期、现金流基准、预测期、
  增长/再投资/分配路径、折现率、终值规则、来源或推导、未批准状态；
- 参数分三类：数据观测值 / 有证据支持的研究假设 / 待设计方批准的使用政策；
- 假设文件内容哈希即 `assumption_id`，与快照 id 一起构成可重放组合；
- 状态只能是「研究候选」，本代码里没有任何路径能把 assumptions 升级成 validated。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from engine.data.snapshot import stable_digest
from engine.models.registry import MODELS_BY_ID
from engine.valuation import errors as e
from engine.valuation.errors import ValuationError

ASSUMPTION_SCHEMA_VERSION = "assumption-set/1"

# 参数种类：区分观测、研究假设与待批准政策，出口按种类展示
KIND_OBSERVED = "observed_data"
KIND_RESEARCH = "research_hypothesis"
KIND_DERIVATION = "derivation"
KIND_POLICY = "policy_pending_approval"
KINDS = (KIND_OBSERVED, KIND_RESEARCH, KIND_DERIVATION, KIND_POLICY)

# 状态：本版只允许研究候选，不允许 validated
STATUS_CANDIDATE = "research_pending_approval"
STATUS_DRAFT = "draft"
ALLOWED_STATUSES = (STATUS_CANDIDATE, STATUS_DRAFT)

# 模型所需的驱动项
_MODEL_DRIVERS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "equity_dcf_fcff": (
        ("revenue_growth", "ebit_margin", "tax_rate", "da_pct_revenue", "capex_pct_revenue"),
        ("nwc_pct_incremental_revenue",),
    ),
    "equity_dividend_discount": (("eps_growth", "payout"), ("roe",)),
    "bank_dividend_discount": (("net_profit_growth", "payout"), ("roe",)),
}


@dataclass(frozen=True)
class Provenance:
    """一个参数从哪来：观测 / 推导 / 研究假设 / 待批准政策。"""

    kind: str
    source: str
    evidence_ids: tuple[str, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValuationError(
                e.MODEL_INPUT_MISSING, f"未知的参数种类 {self.kind!r}，允许 {list(KINDS)}"
            )
        if not self.source.strip():
            raise ValuationError(e.MODEL_INPUT_MISSING, "参数必须写明来源或推导")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "source": self.source,
            "evidence_ids": list(self.evidence_ids),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DiscountRate:
    value: float
    provenance: Provenance
    build_up: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "value": self.value,
            "provenance": self.provenance.to_dict(),
            "build_up": dict(self.build_up),
        }


@dataclass(frozen=True)
class TerminalSpec:
    method: str  # growing_perpetuity / zero_growth_perpetuity / none
    growth: float
    basis: str
    provenance: Provenance

    def to_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "growth": self.growth,
            "basis": self.basis,
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class Scenario:
    name: str
    drivers: dict[str, Any]
    note: str = ""
    discount_rate: float | None = None
    terminal_growth: float | None = None
    # 末期分配超过盈利时，终值必须改用显式声明的可持续每股分配，并说明资金来源
    sustainable_terminal_dps: float | None = None
    sustainable_terminal_basis: str | None = None

    def __post_init__(self) -> None:
        if self.sustainable_terminal_dps is not None:
            if self.terminal_growth is None:
                raise ValuationError(
                    e.TERMINAL_BASIS_MISSING,
                    f"情景 {self.name} 给出可持续终值分配但没有启用终值增长",
                )
            if not (self.sustainable_terminal_basis or "").strip():
                raise ValuationError(
                    e.TERMINAL_BASIS_MISSING,
                    f"情景 {self.name} 的可持续终值分配必须说明资金来源与口径",
                )

    def series(self, name: str) -> tuple[float, ...]:
        value = self.drivers.get(name)
        if value is None:
            raise ValuationError(e.MODEL_INPUT_MISSING, f"情景 {self.name} 缺少驱动项 {name}")
        return tuple(float(x) for x in value)

    def scalar(self, name: str) -> float:
        value = self.drivers.get(name)
        if value is None:
            raise ValuationError(e.MODEL_INPUT_MISSING, f"情景 {self.name} 缺少驱动项 {name}")
        return float(value)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "drivers": self.drivers,
            "note": self.note,
            "discount_rate": self.discount_rate,
            "terminal_growth": self.terminal_growth,
        }
        # 只在用到时写入：没有超额分配的假设文件不会因为它而改变内容哈希
        if self.sustainable_terminal_dps is not None:
            payload["sustainable_terminal_dps"] = self.sustainable_terminal_dps
            payload["sustainable_terminal_basis"] = self.sustainable_terminal_basis
        return payload


@dataclass(frozen=True)
class AssumptionSet:
    instrument_id: str
    model_id: str
    model_version: str
    applicable_as_of: str
    base_fiscal_year: int
    horizon_years: int
    discount_rate: DiscountRate
    terminal: TerminalSpec
    scenarios: dict[str, Scenario]
    provenance: dict[str, Provenance]
    status: str = STATUS_CANDIDATE
    policy_version: str = "unapproved-phase2"
    created_at: str = ""
    notes: str = ""
    extras: dict[str, Any] = field(default_factory=dict)
    schema_version: str = ASSUMPTION_SCHEMA_VERSION
    source_path: str | None = None

    # --- 身份 -----------------------------------------------------------
    @property
    def assumption_id(self) -> str:
        return "assump-" + stable_digest(self._content_payload())

    def _content_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "instrument_id": self.instrument_id,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "applicable_as_of": self.applicable_as_of,
            "base_fiscal_year": self.base_fiscal_year,
            "horizon_years": self.horizon_years,
            "discount_rate": self.discount_rate.to_dict(),
            "terminal": self.terminal.to_dict(),
            "scenarios": {k: v.to_dict() for k, v in sorted(self.scenarios.items())},
            "provenance": {k: v.to_dict() for k, v in sorted(self.provenance.items())},
            "status": self.status,
            "policy_version": self.policy_version,
            "notes": self.notes,
            "extras": self.extras,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_payload()
        payload["assumption_id"] = self.assumption_id
        return payload

    # --- 查询 -----------------------------------------------------------
    def scenario(self, name: str = "base") -> Scenario:
        if name not in self.scenarios:
            raise ValuationError(
                e.MODEL_INPUT_MISSING,
                f"假设 {self.assumption_id} 中没有情景 {name!r}，现有 {sorted(self.scenarios)}",
            )
        return self.scenarios[name]

    def require_extras(self, name: str) -> Any:
        if name not in self.extras:
            raise ValuationError(
                e.MODEL_INPUT_MISSING,
                f"假设 {self.assumption_id} 缺少必填项 extras.{name}（不提供隐含默认值）",
            )
        return self.extras[name]

    def provenance_of(self, name: str) -> Provenance:
        if name not in self.provenance:
            raise ValuationError(
                e.MODEL_INPUT_MISSING, f"参数 {name} 缺少来源说明（不允许无来源参数）"
            )
        return self.provenance[name]

    # --- 校验 -----------------------------------------------------------
    def validate(self) -> list[str]:
        problems: list[str] = []
        model = MODELS_BY_ID.get(self.model_id)
        if model is None:
            problems.append(f"未登记的模型: {self.model_id}")
        else:
            if model.version != self.model_version:
                problems.append(
                    f"model_version={self.model_version!r} 与登记版本 {model.version!r} 不一致"
                )
            if not model.implemented:
                problems.append(f"模型 {self.model_id} 尚未实现，不能承载研究假设")
        if self.status not in ALLOWED_STATUSES:
            problems.append(
                f"status={self.status!r} 不在 {list(ALLOWED_STATUSES)} 内（本版禁止 validated）"
            )
        if self.horizon_years <= 0:
            problems.append("horizon_years 必须为正")
        if not self.applicable_as_of:
            problems.append("applicable_as_of 不能为空")
        if "base" not in self.scenarios:
            problems.append("必须提供 base 情景")

        series_names, scalar_names = _MODEL_DRIVERS.get(self.model_id, ((), ()))
        for name, scenario in self.scenarios.items():
            for driver in series_names:
                values = scenario.drivers.get(driver)
                if values is None:
                    problems.append(f"情景 {name} 缺少驱动项 {driver}")
                elif len(values) != self.horizon_years:
                    problems.append(
                        f"情景 {name} 的 {driver} 长度 {len(values)} 与预测期 {self.horizon_years} 不一致"
                    )
            for driver in scalar_names:
                if scenario.drivers.get(driver) is None:
                    problems.append(f"情景 {name} 缺少驱动项 {driver}")

        for required in (*series_names, *scalar_names):
            if required not in self.provenance:
                problems.append(f"参数 {required} 缺少来源说明")

        if self.terminal.method not in ("growing_perpetuity", "zero_growth_perpetuity", "none"):
            problems.append(f"未知的终值方法 {self.terminal.method!r}")
        if self.terminal.method == "none" and self.terminal.growth != 0.0:
            problems.append("终值方法为 none 时 growth 必须为 0")
        if self.terminal.method == "growing_perpetuity":
            if self.discount_rate.value <= self.terminal.growth:
                problems.append(
                    f"折现率 {self.discount_rate.value:.4f} 必须大于终值增长 "
                    f"{self.terminal.growth:.4f}"
                )
        return problems

    # --- 序列化 ---------------------------------------------------------
    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, source_path: str | None = None) -> "AssumptionSet":
        problems: list[str] = []
        try:
            provenance = {
                name: _provenance_from_dict(name, body)
                for name, body in (data.get("provenance") or {}).items()
            }
            discount = DiscountRate(
                value=float(data["discount_rate"]["value"]),
                provenance=_provenance_from_dict("discount_rate", data["discount_rate"]["provenance"]),
                build_up={
                    str(k): float(v)
                    for k, v in (data["discount_rate"].get("build_up") or {}).items()
                },
            )
            terminal_body = data["terminal"]
            terminal = TerminalSpec(
                method=str(terminal_body.get("method", "none")),
                growth=float(terminal_body.get("growth", 0.0)),
                basis=str(terminal_body.get("basis", "")),
                provenance=_provenance_from_dict("terminal", terminal_body.get("provenance") or {}),
            )
            scenarios = {
                name: Scenario(
                    name=name,
                    drivers=dict(body.get("drivers") or {}),
                    note=str(body.get("note", "")),
                    discount_rate=_opt_float(body.get("discount_rate")),
                    terminal_growth=_opt_float(body.get("terminal_growth")),
                    sustainable_terminal_dps=_opt_float(
                        body.get("sustainable_terminal_dps")
                    ),
                    sustainable_terminal_basis=body.get("sustainable_terminal_basis"),
                )
                for name, body in (data.get("scenarios") or {}).items()
            }
            result = cls(
                instrument_id=str(data["instrument_id"]),
                model_id=str(data["model_id"]),
                model_version=str(data["model_version"]),
                applicable_as_of=str(data["applicable_as_of"]),
                base_fiscal_year=int(data["base_fiscal_year"]),
                horizon_years=int(data["horizon_years"]),
                discount_rate=discount,
                terminal=terminal,
                scenarios=scenarios,
                provenance=provenance,
                status=str(data.get("status", STATUS_DRAFT)),
                policy_version=str(data.get("policy_version", "unapproved-phase2")),
                created_at=str(data.get("created_at", "")),
                notes=str(data.get("notes", "")),
                extras=dict(data.get("extras") or {}),
                schema_version=str(data.get("schema_version", ASSUMPTION_SCHEMA_VERSION)),
                source_path=source_path,
            )
        except KeyError as exc:
            raise ValuationError(e.MODEL_INPUT_MISSING, f"假设文件缺少字段 {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise ValuationError(e.MODEL_INPUT_MISSING, f"假设文件格式错误：{exc}") from exc

        if result.schema_version != ASSUMPTION_SCHEMA_VERSION:
            raise ValuationError(
                e.MODEL_INPUT_MISSING,
                f"假设文件 schema 为 {result.schema_version!r}，期望 {ASSUMPTION_SCHEMA_VERSION}",
            )
        declared = data.get("assumption_id")
        if declared is not None and declared != result.assumption_id:
            raise ValuationError(
                e.MODEL_INPUT_MISSING,
                f"假设文件声明 {declared}，内容哈希为 {result.assumption_id}：内容已被改动",
            )
        problems = result.validate()
        if problems:
            raise ValuationError(e.MODEL_INPUT_MISSING, "假设文件校验失败：" + "；".join(problems))
        return result


def _provenance_from_dict(name: str, body: Mapping[str, Any]) -> Provenance:
    if not isinstance(body, Mapping):
        raise ValuationError(e.MODEL_INPUT_MISSING, f"参数 {name} 的来源说明不是对象")
    return Provenance(
        kind=str(body.get("kind", "")),
        source=str(body.get("source", "")),
        evidence_ids=tuple(str(x) for x in (body.get("evidence_ids") or ())),
        detail=str(body.get("detail", "")),
    )


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def load_assumptions(path: str | Path) -> AssumptionSet:
    path = Path(path)
    if not path.exists():
        raise ValuationError(e.MODEL_INPUT_MISSING, f"假设文件不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValuationError(e.MODEL_INPUT_MISSING, f"假设文件必须是 JSON 对象: {path}")
    return AssumptionSet.from_dict(data, source_path=str(path))


def save_assumptions(assumptions: AssumptionSet, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(assumptions.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


__all__ = [
    "ASSUMPTION_SCHEMA_VERSION",
    "ALLOWED_STATUSES",
    "AssumptionSet",
    "DiscountRate",
    "KIND_DERIVATION",
    "KIND_OBSERVED",
    "KIND_POLICY",
    "KIND_RESEARCH",
    "Provenance",
    "STATUS_CANDIDATE",
    "STATUS_DRAFT",
    "Scenario",
    "TerminalSpec",
    "load_assumptions",
    "save_assumptions",
]
