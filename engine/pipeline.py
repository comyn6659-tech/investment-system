"""分析入口：解析 → 取快照 → 组装结果 → 护栏 → 校验。

阶段一不实现任何估值模型：
- 范围内标的返回 unsupported + undetermined，并展示已核实的身份与数据事实；
- 范围外对象明确说明范围与理由，不套用宽基流程；
- 输入歧义只澄清，不猜。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from engine.contracts import reason_codes as rc
from engine.contracts.enums import (
    AnalysisStatus,
    AssetClass,
    Decision,
    ResolutionStatus,
    ScopeStatus,
    ValuationState,
)
from engine.contracts.result import AnalysisResult
from engine.contracts.validation import enforce
from engine.data.snapshot import Snapshot, SnapshotError, SnapshotStore
from engine.decision.policy import apply_guards, assert_not_fabricated
from engine.identity.catalog import IdentityCatalog
from engine.identity.models import InstrumentRef, Resolution
from engine.identity.resolver import resolve
from engine.models.registry import planned_model_plan
from engine.reporting.payload_builders import (
    build_equity_payload,
    build_etf_payload,
    build_index_payload,
    collect_missing_data,
    strategy_candidates,
)
from engine.version import CODE_VERSION, MODEL_VERSION, POLICY_VERSION

TZ = ZoneInfo("Asia/Singapore")
DEFAULT_INTENT = "buy_point_check"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = PROJECT_ROOT / "data" / "registry" / "instruments.json"
DEFAULT_EQUITY_LIST = PROJECT_ROOT / "data" / "registry" / "cn_a_equities.json"
DEFAULT_SNAPSHOTS = PROJECT_ROOT / "data" / "snapshots"


@dataclass(frozen=True)
class AnalyzerConfig:
    registry_path: Path = DEFAULT_REGISTRY
    equity_list_path: Path = DEFAULT_EQUITY_LIST
    snapshot_dir: Path = DEFAULT_SNAPSHOTS


class Analyzer:
    def __init__(self, config: AnalyzerConfig | None = None) -> None:
        self.config = config or AnalyzerConfig()
        self.catalog = IdentityCatalog.load(self.config.registry_path)
        if Path(self.config.equity_list_path).exists():
            self.catalog.merge_equity_list(Path(self.config.equity_list_path))
        self.store = SnapshotStore(self.config.snapshot_dir)

    # --- 主流程 ---------------------------------------------------------
    def analyze(
        self,
        query: str | None,
        *,
        intent: str = DEFAULT_INTENT,
        snapshot_id: str | None = None,
        now: datetime | None = None,
    ) -> AnalysisResult:
        generated_at = (now or datetime.now(TZ)).isoformat(timespec="seconds")
        resolution = resolve(query, self.catalog)

        if resolution.status == ResolutionStatus.INVALID.value:
            return self._invalid_result(resolution, intent=intent, generated_at=generated_at)
        if resolution.status == ResolutionStatus.AMBIGUOUS.value:
            return self._ambiguous_result(resolution, intent=intent, generated_at=generated_at)

        instrument = resolution.instrument
        assert instrument is not None

        try:
            snapshot = self._load_snapshot(instrument, snapshot_id)
        except SnapshotError as exc:
            return self._engine_error_result(
                instrument, resolution, str(exc), intent=intent, generated_at=generated_at
            )

        scope_status = instrument.scope.status if instrument.scope else ScopeStatus.UNVERIFIED
        if scope_status == ScopeStatus.OUT_OF_SCOPE:
            return self._out_of_scope_result(
                instrument, resolution, snapshot, intent=intent, generated_at=generated_at
            )
        if scope_status == ScopeStatus.UNVERIFIED:
            return self._unverified_result(
                instrument, resolution, snapshot, intent=intent, generated_at=generated_at
            )
        return self._in_scope_result(
            instrument, resolution, snapshot, intent=intent, generated_at=generated_at
        )

    # --- 快照 -----------------------------------------------------------
    def _load_snapshot(self, instrument: InstrumentRef, snapshot_id: str | None) -> Snapshot | None:
        if snapshot_id:
            return self.store.load(snapshot_id)
        latest = self.store.latest_for(instrument.instrument_id)
        if latest is None:
            return None
        return self.store.load(latest)

    # --- 结果构造 -------------------------------------------------------
    def _base_result(
        self,
        *,
        intent: str,
        generated_at: str,
        instrument: InstrumentRef | None,
        resolution: Resolution | None,
        snapshot: Snapshot | None,
    ) -> AnalysisResult:
        identity: dict[str, object] = {}
        if resolution is not None:
            payload = resolution.to_dict()
            payload.pop("instrument", None)
            identity = payload
        return AnalysisResult(
            intent=intent,
            as_of=snapshot.as_of if snapshot else None,
            available_at=snapshot.available_at if snapshot else None,
            snapshot_id=snapshot.snapshot_id if snapshot else None,
            analysis_status=AnalysisStatus.UNSUPPORTED.value,
            model_status=None,
            valuation_state=ValuationState.UNKNOWN.value,
            decision=Decision.UNDETERMINED.value,
            instrument=instrument.to_dict() if instrument else None,
            identity=identity,
            generated_at=generated_at,
        )

    def _invalid_result(self, resolution: Resolution, *, intent: str, generated_at: str) -> AnalysisResult:
        result = self._base_result(
            intent=intent, generated_at=generated_at, instrument=None, resolution=resolution, snapshot=None
        )
        result.analysis_status = AnalysisStatus.NEEDS_CLARIFICATION.value
        result.valuation_state = ValuationState.NOT_APPLICABLE.value
        result.reason_codes = list(resolution.reason_codes)
        result.clarification = resolution.clarification_question
        result.payload = {"payload_type": "none"}
        result.model_plan = _no_model_plan("输入未被识别，没有进入模型环节")
        result.warnings = ["输入未被识别，引擎没有调用任何数据源，也没有产生价格结论。"]
        result.invalidation = []
        return apply_guards(result)

    def _ambiguous_result(
        self, resolution: Resolution, *, intent: str, generated_at: str
    ) -> AnalysisResult:
        result = self._base_result(
            intent=intent, generated_at=generated_at, instrument=None, resolution=resolution, snapshot=None
        )
        result.analysis_status = AnalysisStatus.NEEDS_CLARIFICATION.value
        result.valuation_state = ValuationState.NOT_APPLICABLE.value
        result.reason_codes = list(resolution.reason_codes)
        result.clarification = resolution.clarification_question
        result.payload = {"payload_type": "none"}
        result.model_plan = _no_model_plan("身份存在多个候选，未进入模型环节")
        result.warnings = ["存在多个候选标的，引擎不做猜测，也不对任一候选给出买点结论。"]
        return apply_guards(result)

    def _engine_error_result(
        self,
        instrument: InstrumentRef,
        resolution: Resolution,
        message: str,
        *,
        intent: str,
        generated_at: str,
    ) -> AnalysisResult:
        """快照不可用时明确报错，绝不降级成更宽松的结论。"""
        result = self._base_result(
            intent=intent,
            generated_at=generated_at,
            instrument=instrument,
            resolution=resolution,
            snapshot=None,
        )
        result.analysis_status = AnalysisStatus.ENGINE_ERROR.value
        result.valuation_state = ValuationState.UNKNOWN.value
        reason = (
            rc.SNAPSHOT_HASH_MISMATCH if ("校验失败" in message or "哈希" in message) else rc.SNAPSHOT_NOT_FOUND
        )
        result.reason_codes = [rc.ENGINE_ERROR, reason]
        result.model_plan = _no_model_plan("数据快照不可用，未进入模型环节")
        result.payload = self._build_payload(instrument, None)
        result.warnings = [f"快照加载失败：{message}"]
        result.invalidation = ["修复快照或重新取数后需要重跑本分析。"]
        result.conditions = []
        return self._finalize(result, instrument)

    def _out_of_scope_result(
        self,
        instrument: InstrumentRef,
        resolution: Resolution,
        snapshot: Snapshot | None,
        *,
        intent: str,
        generated_at: str,
    ) -> AnalysisResult:
        result = self._base_result(
            intent=intent,
            generated_at=generated_at,
            instrument=instrument,
            resolution=resolution,
            snapshot=snapshot,
        )
        result.analysis_status = AnalysisStatus.UNSUPPORTED.value
        result.valuation_state = ValuationState.NOT_APPLICABLE.value
        reason = instrument.scope.reason_code if instrument.scope else rc.OUT_OF_SCOPE_OTHER
        result.reason_codes = [reason]
        result.model_plan = {
            "primary_model": None,
            "required_checks": [],
            "selection_reason": "标的不在第一版模型范围，不进入宽基指数流程",
            "not_selected": [],
            "candidate_models": [],
        }
        result.payload = self._build_payload(instrument, snapshot)
        result.missing_data = collect_missing_data(instrument.asset_class, snapshot)
        result.evidence_refs = self._evidence_refs(instrument, snapshot)
        result.invalidation = [
            "若该产品的真实跟踪指数或产品性质被更正（例如被证实为普通被动宽基 ETF），本结论需要重跑。"
        ]
        result.warnings = [
            "范围外只代表「本版不建模」，不代表对该产品或行业的投资价值判断。",
        ]
        return self._finalize(result, instrument)

    def _unverified_result(
        self,
        instrument: InstrumentRef,
        resolution: Resolution,
        snapshot: Snapshot | None,
        *,
        intent: str,
        generated_at: str,
    ) -> AnalysisResult:
        result = self._base_result(
            intent=intent,
            generated_at=generated_at,
            instrument=instrument,
            resolution=resolution,
            snapshot=snapshot,
        )
        result.analysis_status = AnalysisStatus.UNSUPPORTED.value
        result.valuation_state = ValuationState.UNKNOWN.value
        result.reason_codes = [rc.IDENTITY_NOT_VERIFIED]
        result.model_plan = planned_model_plan(instrument.asset_class.value)
        result.payload = self._build_payload(instrument, snapshot)
        result.missing_data = collect_missing_data(instrument.asset_class, snapshot)
        result.evidence_refs = self._evidence_refs(instrument, snapshot)
        result.warnings = [
            "该标的身份尚未用产品资料核实，因此不进入任何模型流程。",
        ]
        result.invalidation = ["若身份核实结果变化，本结果需要重跑。"]
        return self._finalize(result, instrument)

    def _in_scope_result(
        self,
        instrument: InstrumentRef,
        resolution: Resolution,
        snapshot: Snapshot | None,
        *,
        intent: str,
        generated_at: str,
    ) -> AnalysisResult:
        result = self._base_result(
            intent=intent,
            generated_at=generated_at,
            instrument=instrument,
            resolution=resolution,
            snapshot=snapshot,
        )
        reason_codes: list[str] = []
        if instrument.asset_class == AssetClass.EQUITY:
            reason_codes = [rc.MODEL_NOT_IMPLEMENTED_EQUITY, rc.MODEL_SELECTION_NOT_IMPLEMENTED]
            model_plan = planned_model_plan("equity")
        elif instrument.asset_class == AssetClass.INDEX:
            reason_codes = [rc.MODEL_NOT_IMPLEMENTED_INDEX]
            model_plan = planned_model_plan("index")
        elif instrument.asset_class == AssetClass.ETF:
            reason_codes = [rc.MODEL_NOT_IMPLEMENTED_ETF, rc.MODEL_NOT_IMPLEMENTED_INDEX]
            model_plan = planned_model_plan("etf")
        else:  # pragma: no cover - 注册表不应出现
            reason_codes = [rc.OUT_OF_SCOPE_OTHER]
            model_plan = planned_model_plan("equity")

        if instrument.asset_class == AssetClass.INDEX and not instrument.in_first_batch:
            reason_codes.append(rc.INDEX_NOT_IN_FIRST_BATCH)
        if instrument.asset_class == AssetClass.INDEX and snapshot is not None:
            if not snapshot.coverage.get("supports_weekly_5y_history", False):
                reason_codes.append(rc.MISSING_VALUATION_HISTORY)
        if instrument.asset_class == AssetClass.ETF and snapshot is None:
            reason_codes.append(rc.MISSING_ETF_NAV)
        if snapshot is None:
            reason_codes.append(rc.SNAPSHOT_NOT_FOUND)

        result.analysis_status = AnalysisStatus.UNSUPPORTED.value
        result.model_status = None
        result.valuation_state = (
            ValuationState.UNKNOWN.value
            if instrument.asset_class != AssetClass.EQUITY
            else ValuationState.UNKNOWN.value
        )
        result.reason_codes = reason_codes
        result.model_plan = model_plan
        result.payload = self._build_payload(instrument, snapshot)
        result.missing_data = collect_missing_data(instrument.asset_class, snapshot)
        result.evidence_refs = self._evidence_refs(instrument, snapshot)
        result.invalidation = [
            "若身份、跟踪关系或数据来源被更正，本结果需要重跑。",
            "本结果不含估值结论，因此不存在「价格变化使买点失效」的情形。",
        ]
        result.warnings = [
            "阶段一未实现任何估值/策略模型：本结果只覆盖身份、契约与数据可用性。",
            "「不适用/未实现」不等于「该标的没有投资价值」。",
        ]
        return self._finalize(result, instrument)

    # --- 载荷与证据 -----------------------------------------------------
    def _build_payload(self, instrument: InstrumentRef, snapshot: Snapshot | None) -> dict:
        if instrument.asset_class == AssetClass.EQUITY:
            return build_equity_payload(instrument, snapshot)
        if instrument.asset_class == AssetClass.INDEX:
            return build_index_payload(
                instrument, snapshot, strategy_candidates=strategy_candidates()
            )
        if instrument.asset_class == AssetClass.ETF:
            underlying = self.catalog.underlying(instrument)
            return build_etf_payload(instrument, underlying, snapshot)
        return {"payload_type": "none"}

    def _evidence_refs(self, instrument: InstrumentRef, snapshot: Snapshot | None) -> list[dict]:
        refs: list[dict] = [e.to_dict() for e in instrument.evidence]
        if snapshot is None:
            return refs
        seen = {r.get("evidence_id") for r in refs}
        for fact in snapshot.facts:
            source = fact.source
            if source is None:
                continue
            key = source.source_id
            if key in seen:
                continue
            seen.add(key)
            refs.append(
                {
                    "evidence_id": key,
                    "kind": "vendor_api",
                    "publisher": source.publisher,
                    "url": source.url,
                    "retrieved_at": source.retrieved_at,
                    "fields_cited": [],
                    "note": source.interface,
                }
            )
        return refs

    # --- 统一收尾 -------------------------------------------------------
    def _finalize(self, result: AnalysisResult, instrument: InstrumentRef) -> AnalysisResult:
        apply_guards(result)
        assert_not_fabricated(result)
        enforce(result)
        return result

    # --- 元信息 ---------------------------------------------------------
    @staticmethod
    def versions() -> dict[str, str]:
        return {
            "code_version": CODE_VERSION,
            "policy_version": POLICY_VERSION,
            "model_version": MODEL_VERSION,
        }


def _no_model_plan(reason: str) -> dict:
    return {
        "primary_model": None,
        "required_checks": [],
        "selection_reason": reason,
        "not_selected": [],
        "candidate_models": [],
    }


def analyze(query: str | None, **kwargs) -> AnalysisResult:
    """便捷函数：使用默认配置分析一个输入。"""
    analyzer = kwargs.pop("analyzer", None) or Analyzer()
    return analyzer.analyze(query, **kwargs)


def clone_result(result: AnalysisResult) -> AnalysisResult:
    return copy.deepcopy(result)


__all__ = [
    "Analyzer",
    "AnalyzerConfig",
    "analyze",
    "clone_result",
    "DEFAULT_REGISTRY",
    "DEFAULT_SNAPSHOTS",
]
