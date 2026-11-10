"""AnalysisResult：统一出口对象。

设计书 §9 建议的字段在这里全部落地；同时保证：
- analysis_id 与 snapshot_id 一起可重放（不含生成时间）；
- 研究结果、未建模型、数据不足都不能输出正式 candidate；
- 文本与 JSON 使用同一个校验器。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.data.snapshot import canonical_json, stable_digest
from engine.version import CODE_VERSION, MODEL_VERSION, POLICY_VERSION, RESULT_SCHEMA_VERSION


@dataclass
class AnalysisResult:
    # --- 身份与目的 -----------------------------------------------------
    intent: str
    # --- 时点 -----------------------------------------------------------
    as_of: str | None
    available_at: str | None
    snapshot_id: str | None
    # --- 结论维度 -------------------------------------------------------
    analysis_status: str
    model_status: str | None
    valuation_state: str
    decision: str
    reason_codes: list[str] = field(default_factory=list)
    # --- 证据与说明 -----------------------------------------------------
    instrument: dict[str, Any] | None = None
    identity: dict[str, Any] = field(default_factory=dict)
    model_plan: dict[str, Any] = field(default_factory=dict)
    assumptions: list[dict[str, Any]] = field(default_factory=list)
    model_results: list[dict[str, Any]] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    invalidation: list[str] = field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    missing_data: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    clarification: str | None = None
    payload: dict[str, Any] = field(default_factory=lambda: {"payload_type": "none"})
    # --- 版本 -----------------------------------------------------------
    schema_version: str = RESULT_SCHEMA_VERSION
    code_version: str = CODE_VERSION
    policy_version: str = POLICY_VERSION
    model_version: str = MODEL_VERSION
    # --- 生成时间（唯一允许在重放中变化的部分）--------------------------
    generated_at: str | None = None

    # --- 派生 -----------------------------------------------------------
    @property
    def instrument_id(self) -> str | None:
        return (self.instrument or {}).get("instrument_id")

    @property
    def analysis_id(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "instrument_id": self.instrument_id,
            "snapshot_id": self.snapshot_id,
            "intent": self.intent,
            "code_version": self.code_version,
            "policy_version": self.policy_version,
            "model_version": self.model_version,
            "analysis_status": self.analysis_status,
            "model_status": self.model_status,
            "valuation_state": self.valuation_state,
            "decision": self.decision,
            "reason_codes": sorted(self.reason_codes),
        }
        return "analysis-" + stable_digest(payload)

    # --- 序列化 ---------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "analysis_id": self.analysis_id,
            "generated_at": self.generated_at,
            "instrument": self.instrument,
            "instrument_id": self.instrument_id,
            "intent": self.intent,
            "identity": self.identity,
            "snapshot_id": self.snapshot_id,
            "as_of": self.as_of,
            "available_at": self.available_at,
            "analysis_status": self.analysis_status,
            "model_status": self.model_status,
            "valuation_state": self.valuation_state,
            "decision": self.decision,
            "reason_codes": list(self.reason_codes),
            "model_plan": self.model_plan,
            "assumptions": self.assumptions,
            "model_results": self.model_results,
            "conditions": self.conditions,
            "invalidation": self.invalidation,
            "evidence_refs": self.evidence_refs,
            "missing_data": self.missing_data,
            "warnings": self.warnings,
            "clarification": self.clarification,
            "payload": self.payload,
            "code_version": self.code_version,
            "policy_version": self.policy_version,
            "model_version": self.model_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalysisResult":
        return cls(
            intent=data["intent"],
            as_of=data.get("as_of"),
            available_at=data.get("available_at"),
            snapshot_id=data.get("snapshot_id"),
            analysis_status=data["analysis_status"],
            model_status=data.get("model_status"),
            valuation_state=data["valuation_state"],
            decision=data["decision"],
            reason_codes=list(data.get("reason_codes", [])),
            instrument=data.get("instrument"),
            identity=data.get("identity", {}),
            model_plan=data.get("model_plan", {}),
            assumptions=data.get("assumptions", []),
            model_results=data.get("model_results", []),
            conditions=data.get("conditions", []),
            invalidation=data.get("invalidation", []),
            evidence_refs=data.get("evidence_refs", []),
            missing_data=data.get("missing_data", []),
            warnings=data.get("warnings", []),
            clarification=data.get("clarification"),
            payload=data.get("payload", {"payload_type": "none"}),
            schema_version=data.get("schema_version", RESULT_SCHEMA_VERSION),
            code_version=data.get("code_version", CODE_VERSION),
            policy_version=data.get("policy_version", POLICY_VERSION),
            model_version=data.get("model_version", MODEL_VERSION),
            generated_at=data.get("generated_at"),
        )

    def replay_fingerprint(self) -> str:
        """用于重放一致性比较：排除生成时间。"""
        data = self.to_dict()
        data.pop("generated_at", None)
        return stable_digest(data, length=32)

    def dump_json(self, *, indent: int = 2) -> str:
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
