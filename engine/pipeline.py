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
from engine.assumptions.schema import AssumptionSet, load_assumptions
from engine.data.snapshot import Snapshot, SnapshotError, SnapshotStore
from engine.decision.policy import apply_guards, assert_not_fabricated
from engine.evidence.models import EvidenceBook
from engine.identity.catalog import IdentityCatalog
from engine.identity.models import InstrumentRef, Resolution
from engine.identity.resolver import resolve
from engine.models.registry import MODELS_BY_ID, planned_model_plan
from engine.reporting.payload_builders import build_equity_payload, build_etf_payload, build_index_payload
from engine.reporting.product_check import build_product_check
from engine.series.models import (
    FREQ_DAILY_SHORT,
    FREQ_MONTHLY_REFERENCE,
    METRIC_OFFICIAL_PE_1,
    SeriesStore,
)
from engine.series.profile import POOL_METRICS, build_index_profile
from engine.signals.candidate_a import build_candidate_a_signal
from engine.signals.ledger import LedgerError, SignalLedger
from engine.valuation.runner import run_research_valuation
from engine.reporting.payload_builders import (
    collect_missing_data,
    strategy_candidates,
)
from engine.version import CODE_VERSION, MODEL_VERSION, POLICY_VERSION

TZ = ZoneInfo("Asia/Singapore")
DEFAULT_INTENT = "buy_point_check"

# 快照失败原因 → 结果里的专用原因码
_SNAPSHOT_ERROR_CODES = {
    "SNAPSHOT_NOT_FOUND": rc.SNAPSHOT_NOT_FOUND,
    "SNAPSHOT_HASH_MISMATCH": rc.SNAPSHOT_HASH_MISMATCH,
    "SNAPSHOT_ID_MISMATCH": rc.SNAPSHOT_ID_MISMATCH,
    "SNAPSHOT_INSTRUMENT_MISMATCH": rc.SNAPSHOT_INSTRUMENT_MISMATCH,
    "SNAPSHOT_ASSET_CLASS_MISMATCH": rc.SNAPSHOT_ASSET_CLASS_MISMATCH,
    "SNAPSHOT_INVALID_STRUCTURE": rc.SNAPSHOT_INVALID_STRUCTURE,
}


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = PROJECT_ROOT / "data" / "registry" / "instruments.json"
DEFAULT_EQUITY_LIST = PROJECT_ROOT / "data" / "registry" / "cn_a_equities.json"
DEFAULT_SNAPSHOTS = PROJECT_ROOT / "data" / "snapshots"
DEFAULT_EVIDENCE = PROJECT_ROOT / "data" / "evidence"
DEFAULT_SERIES = PROJECT_ROOT / "data" / "series"
DEFAULT_BACKTEST = PROJECT_ROOT / "data" / "backtest" / "candidate-a.json"
DEFAULT_LEDGER = PROJECT_ROOT / "data" / "ledger" / "candidate-a-signals.jsonl"


@dataclass(frozen=True)
class AnalyzerConfig:
    registry_path: Path = DEFAULT_REGISTRY
    equity_list_path: Path = DEFAULT_EQUITY_LIST
    snapshot_dir: Path = DEFAULT_SNAPSHOTS
    evidence_dir: Path = DEFAULT_EVIDENCE
    series_dir: Path = DEFAULT_SERIES
    backtest_path: Path = DEFAULT_BACKTEST
    ledger_path: Path = DEFAULT_LEDGER
    assumptions_path: Path | None = None
    cross_check_assumptions_path: Path | None = None


class Analyzer:
    def __init__(self, config: AnalyzerConfig | None = None) -> None:
        self.config = config or AnalyzerConfig()
        self.catalog = IdentityCatalog.load(self.config.registry_path)
        if Path(self.config.equity_list_path).exists():
            self.catalog.merge_equity_list(Path(self.config.equity_list_path))
        self.store = SnapshotStore(self.config.snapshot_dir)
        self.evidence = EvidenceBook(Path(self.config.evidence_dir))
        self.series = SeriesStore(Path(self.config.series_dir))

    # --- 主流程 ---------------------------------------------------------
    def analyze(
        self,
        query: str | None,
        *,
        intent: str = DEFAULT_INTENT,
        snapshot_id: str | None = None,
        now: datetime | None = None,
        assumptions: AssumptionSet | None = None,
        cross_check_assumptions: AssumptionSet | None = None,
    ) -> AnalysisResult:
        generated_at = (now or datetime.now(TZ)).isoformat(timespec="seconds")
        resolution = resolve(query, self.catalog)

        if resolution.status == ResolutionStatus.INVALID.value:
            return self._invalid_result(resolution, intent=intent, generated_at=generated_at)
        if resolution.status == ResolutionStatus.AMBIGUOUS.value:
            return self._ambiguous_result(resolution, intent=intent, generated_at=generated_at)
        if resolution.status == ResolutionStatus.OUT_OF_SCOPE.value and resolution.instrument is None:
            return self._scope_only_result(resolution, intent=intent, generated_at=generated_at)

        instrument = resolution.instrument
        assert instrument is not None

        try:
            snapshot = self._load_snapshot(instrument, snapshot_id)
        except SnapshotError as exc:
            return self._engine_error_result(
                instrument,
                resolution,
                str(exc),
                code=getattr(exc, "code", "SNAPSHOT_INVALID_STRUCTURE"),
                intent=intent,
                generated_at=generated_at,
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
        if instrument.asset_class == AssetClass.EQUITY:
            return self._equity_result(
                instrument,
                resolution,
                snapshot,
                intent=intent,
                generated_at=generated_at,
                assumptions=assumptions,
                cross_check_assumptions=cross_check_assumptions,
            )
        if instrument.asset_class == AssetClass.INDEX:
            return self._index_result(
                instrument, resolution, snapshot, intent=intent, generated_at=generated_at
            )
        if instrument.asset_class == AssetClass.ETF:
            return self._etf_result(
                instrument, resolution, snapshot, intent=intent, generated_at=generated_at
            )
        return self._in_scope_result(
            instrument, resolution, snapshot, intent=intent, generated_at=generated_at
        )

    # --- 快照 -----------------------------------------------------------
    def _load_snapshot(self, instrument: InstrumentRef, snapshot_id: str | None) -> Snapshot | None:
        """取快照并强制校验它与请求标的、资产类别一致。

        不允许「拿另一只标的的快照继续算」：身份与数据必须同源，
        否则价格会挂到错误的基金上。
        """
        if snapshot_id:
            snapshot = self.store.load(snapshot_id)
        else:
            latest = self.store.latest_for(instrument.instrument_id)
            if latest is None:
                return None
            snapshot = self.store.load(latest)

        if snapshot.instrument_id != instrument.instrument_id:
            raise SnapshotError(
                f"快照 {snapshot.snapshot_id} 属于 {snapshot.instrument_id}，"
                f"与请求标的 {instrument.instrument_id} 不一致",
                code="SNAPSHOT_INSTRUMENT_MISMATCH",
            )
        if snapshot.asset_class != instrument.asset_class.value:
            raise SnapshotError(
                f"快照 {snapshot.snapshot_id} 的资产类别为 {snapshot.asset_class}，"
                f"与请求标的 {instrument.asset_class.value} 不一致",
                code="SNAPSHOT_ASSET_CLASS_MISMATCH",
            )
        return snapshot

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

    def _scope_only_result(
        self, resolution: Resolution, *, intent: str, generated_at: str
    ) -> AnalysisResult:
        """身份未知但可按名称规则排除：给出结构化范围说明，不伪造身份。

        这类结果必须仍然是合法契约（instrument=null + payload none），
        不能因为缺少 instrument 就抛异常变成 engine_error。
        """
        result = self._base_result(
            intent=intent,
            generated_at=generated_at,
            instrument=None,
            resolution=resolution,
            snapshot=None,
        )
        result.analysis_status = AnalysisStatus.UNSUPPORTED.value
        result.valuation_state = ValuationState.NOT_APPLICABLE.value
        result.reason_codes = list(resolution.reason_codes)
        result.model_plan = _no_model_plan("标的未取得具体身份，不进入模型环节")
        result.payload = {"payload_type": "none"}
        result.evidence_refs = []
        result.invalidation = ["若取得该产品真实身份并核实跟踪对象，本结论需要重跑。"]
        result.warnings = [
            "该输入按名称规则被排除在第一版范围之外，未取得可核实的证券身份。",
            "范围外只代表「本版不建模」，不代表对其投资价值的判断。",
        ]
        result.clarification = (
            f"「{resolution.raw_input.strip()}」被识别为范围外对象，未做身份登记。"
            "如需精确核对，请提供 6 位证券代码。"
        )
        return apply_guards(result)

    def _engine_error_result(
        self,
        instrument: InstrumentRef,
        resolution: Resolution,
        message: str,
        *,
        code: str,
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
        reason = _SNAPSHOT_ERROR_CODES.get(code, rc.SNAPSHOT_INVALID_STRUCTURE)
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

    # --- 指数：描述性估值画像 -------------------------------------------
    def _index_profile(self, instrument: InstrumentRef, as_of: str) -> dict:
        """同一指数无论从指数还是 ETF 进入，都必须走这一个函数。"""
        series = {}
        for metric in POOL_METRICS:
            item = self.series.find(
                instrument.instrument_id, metric, frequency=FREQ_MONTHLY_REFERENCE
            )
            if item is not None:
                series[metric] = item
        short = {}
        official = self.series.find(
            instrument.instrument_id, METRIC_OFFICIAL_PE_1, frequency=FREQ_DAILY_SHORT
        )
        if official is not None:
            short[METRIC_OFFICIAL_PE_1] = official
        return build_index_profile(
            instrument_id=instrument.instrument_id,
            as_of=as_of,
            series=series,
            short_series=short,
        )

    def _index_result(
        self,
        instrument: InstrumentRef,
        resolution: Resolution,
        snapshot: Snapshot | None,
        *,
        intent: str,
        generated_at: str,
    ) -> AnalysisResult:
        as_of = snapshot.as_of if snapshot is not None else generated_at[:10]
        profile = self._index_profile(instrument, as_of)
        signal = build_candidate_a_signal(
            self.series,
            instrument.instrument_id,
            as_of=as_of,
            backtest_path=self.config.backtest_path,
        )
        signal["ledger"] = self._signal_ledger_info(instrument.instrument_id)
        return self._compose_index_result(
            instrument,
            resolution,
            snapshot,
            profile,
            signal,
            intent=intent,
            generated_at=generated_at,
        )

    def _compose_index_result(
        self,
        instrument: InstrumentRef,
        resolution: Resolution,
        snapshot: Snapshot | None,
        profile: dict,
        signal: dict | None,
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
        profile_status = profile.get("status")
        status_map = {
            "complete": AnalysisStatus.COMPLETE.value,
            "partial": AnalysisStatus.PARTIAL.value,
            "insufficient_data": AnalysisStatus.INSUFFICIENT_DATA.value,
        }
        result.analysis_status = status_map.get(
            profile_status, AnalysisStatus.INSUFFICIENT_DATA.value
        )
        computed = result.analysis_status in (
            AnalysisStatus.COMPLETE.value,
            AnalysisStatus.PARTIAL.value,
        )
        result.model_status = "research" if computed else None
        result.valuation_state = ValuationState.UNKNOWN.value
        result.decision = Decision.UNDETERMINED.value

        codes: list[str] = []
        if not instrument.in_first_batch:
            codes.append(rc.INDEX_NOT_IN_FIRST_BATCH)
        if not profile.get("metrics"):
            codes.append(rc.INDEX_PROFILE_SERIES_MISSING)
        if profile.get("missing"):
            codes.append(rc.INDEX_PROFILE_COVERAGE_INSUFFICIENT)
        codes.extend([rc.INDEX_PROFILE_DESCRIPTIVE_ONLY, rc.RESEARCH_PARAMETERS_UNAPPROVED])
        if signal is not None and signal.get("status") == "complete":
            codes.append(rc.INDEX_SIGNAL_RESEARCH_ONLY)
        else:
            codes.append(rc.INDEX_STRATEGY_NOT_IMPLEMENTED)
        if snapshot is None:
            codes.append(rc.SNAPSHOT_NOT_FOUND)

        result.reason_codes = codes
        result.model_plan = _index_profile_plan(profile, signal=signal)
        result.payload = self._build_payload(
            instrument, snapshot, index_profile=profile, candidate_a_signal=signal
        )
        result.model_results = (
            [_index_profile_result(profile, instrument)]
            if computed
            else []
        )
        result.missing_data = _profile_missing(profile) + collect_missing_data(
            AssetClass.INDEX, snapshot
        )
        result.evidence_refs = self._evidence_refs(instrument, snapshot)
        result.conditions = [
            "画像只回答「当前值相对该供应商历史分布处于什么位置」，不是交易信号。",
            "月度参考样本与官方短历史分开使用；官方最新值不参与第三方历史排名。",
            "候选 A 研究信号只描述周度分位条件是否成立，不代表建议持仓，也不构成买卖结论。",
        ]
        result.invalidation = [
            "供应商历史序列被修订或更换口径时，分位需要重算。",
            "若窗口内有效月份少于 54 个，对应分位将不再给出。",
            "官方日频序列被修订或规则版本变化时，研究信号与账本记录需要按新序列重放。",
        ]
        result.warnings = [
            "候选 A 只以研究信号展示：规则与阈值未获批准，不构成买点，也不代替投资决策。",
            "PE/PB 的聚合方法与亏损样本处理在来源中未说明，只能作同源同列的描述性比较。",
            "研究信号按官方日频滚动市盈率计算，与第三方月度画像不是同一条序列。",
        ]
        if profile_status == "partial":
            result.warnings.append("部分指标覆盖不足：对应分位未计算，已在缺失清单中说明。")
        if profile_status == "insufficient_data":
            result.warnings.append("没有足够的同源历史序列：本次不输出任何分位。")
        return self._finalize(result, instrument)

    # --- ETF：产品资料 + 关联同一份指数画像 ------------------------------
    def _etf_result(
        self,
        instrument: InstrumentRef,
        resolution: Resolution,
        snapshot: Snapshot | None,
        *,
        intent: str,
        generated_at: str,
    ) -> AnalysisResult:
        underlying = self.catalog.underlying(instrument)
        as_of = snapshot.as_of if snapshot is not None else generated_at[:10]
        profile = (
            self._index_profile(underlying, as_of) if underlying is not None else None
        )
        product = build_product_check(instrument, underlying, snapshot)

        result = self._base_result(
            intent=intent,
            generated_at=generated_at,
            instrument=instrument,
            resolution=resolution,
            snapshot=snapshot,
        )
        profile_status = (profile or {}).get("status")
        statuses = {profile_status, product.get("status")}
        if "insufficient_data" in statuses or profile is None:
            result.analysis_status = AnalysisStatus.INSUFFICIENT_DATA.value
        elif "partial" in statuses:
            result.analysis_status = AnalysisStatus.PARTIAL.value
        else:
            result.analysis_status = AnalysisStatus.COMPLETE.value
        computed = result.analysis_status in (
            AnalysisStatus.COMPLETE.value,
            AnalysisStatus.PARTIAL.value,
        )
        result.model_status = "research" if computed else None
        result.valuation_state = ValuationState.UNKNOWN.value
        result.decision = Decision.UNDETERMINED.value

        codes: list[str] = [rc.ETF_PRODUCT_FACTS_ONLY, rc.RESEARCH_PARAMETERS_UNAPPROVED]
        if profile is None:
            codes.append(rc.INDEX_PROFILE_SERIES_MISSING)
        elif profile.get("missing"):
            codes.append(rc.INDEX_PROFILE_COVERAGE_INSUFFICIENT)
        codes.extend([rc.INDEX_PROFILE_DESCRIPTIVE_ONLY, rc.INDEX_STRATEGY_NOT_IMPLEMENTED])
        if product.get("trading", {}).get("secondary_market_status", {}).get("status") != "known":
            codes.append(rc.ETF_TRADING_STATUS_UNKNOWN)
        if not product.get("nav", {}).get("disclosure_time_known"):
            codes.append(rc.ETF_NAV_DISCLOSURE_TIME_UNKNOWN)
        premium = product.get("premium_discount_same_day") or {}
        if premium.get("value") is None:
            codes.append(rc.ETF_SAME_DAY_PREMIUM_UNAVAILABLE)
        if snapshot is None:
            codes.append(rc.SNAPSHOT_NOT_FOUND)
        result.reason_codes = codes

        result.model_plan = _index_profile_plan(
            profile or {"status": "insufficient_data", "metrics": {}, "missing": []},
            note_asset="ETF 本身不单独建模：产品资料单列，指数画像沿用其跟踪指数",
        )
        result.payload = self._build_payload(
            instrument, snapshot, index_profile=profile, product_check=product
        )
        result.model_results = (
            [_index_profile_result(profile, underlying or instrument)]
            if computed and profile
            else []
        )
        result.missing_data = _profile_missing(profile) + list(product.get("missing") or [])
        result.evidence_refs = self._evidence_refs(instrument, snapshot)
        result.conditions = [
            "ETF 只输出产品资料完整性检查；资料齐备不等于满足买入条件。",
            "指数估值位置与 ETF 产品资料不互相替代。",
        ]
        result.invalidation = [
            "净值日期或披露口径变化时，同日折溢价需要重算。",
            "若取得停复牌与盘口数据，交易状态相关结论需要更新。",
        ]
        result.warnings = [
            "同日折溢价是事后计算，不能证明当日收盘前可交易，也不用于回测当日成交。",
            "本轮不制定溢价、流动性或费率的买卖门槛，也不做基金之间的比较或推荐。",
        ]
        return self._finalize(result, instrument)

    # --- 个股：研究估值 -------------------------------------------------
    def _equity_result(
        self,
        instrument: InstrumentRef,
        resolution: Resolution,
        snapshot: Snapshot | None,
        *,
        intent: str,
        generated_at: str,
        assumptions: AssumptionSet | None,
        cross_check_assumptions: AssumptionSet | None,
    ) -> AnalysisResult:
        """个股走「证据 → 适用性 → 假设 → 计算 → 研究报告」链路。

        无论走到哪一步，decision 都是 undetermined；模型只有 research 状态，
        因此研究估值不会变成买点结论。
        """
        evidence = self.evidence.get(instrument.instrument_id)
        primary_assumptions = assumptions or self._configured_assumptions()
        alt_assumptions = cross_check_assumptions or self._configured_cross_check()

        valuation = run_research_valuation(
            instrument_id=instrument.instrument_id,
            snapshot=snapshot,
            evidence=evidence,
            assumptions=primary_assumptions,
            cross_check_assumptions=alt_assumptions,
        )

        result = self._base_result(
            intent=intent,
            generated_at=generated_at,
            instrument=instrument,
            resolution=resolution,
            snapshot=snapshot,
        )
        status_map = {
            "complete": AnalysisStatus.COMPLETE.value,
            "partial": AnalysisStatus.PARTIAL.value,
            "insufficient_data": AnalysisStatus.INSUFFICIENT_DATA.value,
            "unsupported": AnalysisStatus.UNSUPPORTED.value,
        }
        result.analysis_status = status_map.get(
            valuation.status, AnalysisStatus.INSUFFICIENT_DATA.value
        )
        computed = result.analysis_status in (
            AnalysisStatus.COMPLETE.value,
            AnalysisStatus.PARTIAL.value,
        )
        # 只有真正跑出研究估值才标记模型状态；未执行时不假装模型已运行
        result.model_status = "research" if computed else None
        result.valuation_state = (
            ValuationState.NOT_APPLICABLE.value
            if valuation.status == "unsupported"
            else ValuationState.UNKNOWN.value
        )
        result.decision = Decision.UNDETERMINED.value
        result.reason_codes = list(valuation.reason_codes)
        result.model_plan = _equity_model_plan(valuation)
        result.payload = self._build_payload(
            instrument,
            snapshot,
            research_valuation=valuation.to_dict() if computed else None,
            model_applicability={
                "status": valuation.applicability.get("status"),
                "family": valuation.applicability.get("family"),
                "family_label": valuation.applicability.get("family_label"),
                "primary_model": valuation.applicability.get("primary_model"),
                "cross_check_model": valuation.applicability.get("cross_check_model"),
                "evidence_id": valuation.applicability.get("evidence_id"),
                "evidence_version": valuation.applicability.get("evidence_version"),
                "applicable_model_category": valuation.applicability.get(
                    "applicable_model_category"
                ),
                "policy_version": valuation.applicability.get("policy", {}).get(
                    "policy_version"
                ),
            },
        )
        result.model_results = (
            [
                {
                    "result_type": "research_valuation",
                    "model_id": valuation.model_id,
                    "model_version": valuation.model_version,
                    "model_status": "research",
                    "assumption_id": valuation.assumption_id,
                    "headline_value_per_share": valuation.headline_value_per_share,
                    "scenario_count": len(valuation.scenarios),
                    "cross_check_model": (
                        valuation.cross_check or {}
                    ).get("model_id")
                    if valuation.cross_check
                    else None,
                }
            ]
            if computed
            else []
        )
        result.assumptions = _assumption_summary(primary_assumptions, alt_assumptions, valuation)
        result.conditions = [
            "研究估值只在所附假设成立时成立；假设与参数未经设计方批准，不得当作正式买点。",
            "估值以快照中的合并报表数据为基准，未经审计调整或重述核对。",
        ]
        if valuation.status == "partial":
            result.conditions.append("勾稽或交叉检查存在未解释差额，结论按部分完成处理。")
        result.missing_data = _merge_missing(
            list(valuation.missing), collect_missing_data(AssetClass.EQUITY, snapshot)
        )
        result.evidence_refs = self._evidence_refs(instrument, snapshot, evidence)
        result.invalidation = list(valuation.invalidation)
        result.warnings = list(valuation.warnings)
        if snapshot is None:
            result.warnings.append("没有可用快照：本结果不含任何财务事实或估值数字。")
        return self._finalize(result, instrument)

    # --- 已配置的假设 ---------------------------------------------------
    def _signal_ledger_info(self, instrument_id: str) -> dict:
        """账本状态：只追加、已有记录不可修改；这里只读取，不写入。"""
        path = Path(self.config.ledger_path)
        try:
            relative = str(path.relative_to(PROJECT_ROOT))
        except ValueError:
            relative = str(path)
        try:
            records = [
                record
                for record in SignalLedger(path).load()
                if record.get("instrument_id") == instrument_id
            ]
        except LedgerError as exc:
            return {"path": relative, "status": "unavailable", "note": str(exc)}
        if not records:
            return {
                "path": relative,
                "status": "empty",
                "records": 0,
                "note": "尚未写入任何观察点：运行 tools/update_signal_ledger.py 前向记录",
            }
        latest = max(records, key=lambda record: str(record["date"]))
        return {
            "path": relative,
            "status": "ok",
            "records": len(records),
            "last_week": latest.get("week"),
            "last_date": latest.get("date"),
            "last_signal": latest.get("signal"),
            "note": "只追加账本：每次运行把新出现的周度观察点写入，已有记录不可修改",
        }

    def _configured_assumptions(self) -> AssumptionSet | None:
        if not hasattr(self, "_assumption_cache"):
            self._assumption_cache = (
                load_assumptions(self.config.assumptions_path)
                if self.config.assumptions_path
                else None
            )
        return self._assumption_cache

    def _configured_cross_check(self) -> AssumptionSet | None:
        if not hasattr(self, "_cross_cache"):
            self._cross_cache = (
                load_assumptions(self.config.cross_check_assumptions_path)
                if self.config.cross_check_assumptions_path
                else None
            )
        return self._cross_cache

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
    def _build_payload(
        self,
        instrument: InstrumentRef,
        snapshot: Snapshot | None,
        *,
        research_valuation: dict | None = None,
        model_applicability: dict | None = None,
        index_profile: dict | None = None,
        product_check: dict | None = None,
        candidate_a_signal: dict | None = None,
    ) -> dict:
        if instrument.asset_class == AssetClass.EQUITY:
            return build_equity_payload(
                instrument,
                snapshot,
                research_valuation=research_valuation,
                model_applicability=model_applicability,
            )
        if instrument.asset_class == AssetClass.INDEX:
            return build_index_payload(
                instrument,
                snapshot,
                strategy_candidates=strategy_candidates(),
                profile=index_profile,
                signal=candidate_a_signal,
            )
        if instrument.asset_class == AssetClass.ETF:
            underlying = self.catalog.underlying(instrument)
            return build_etf_payload(
                instrument,
                underlying,
                snapshot,
                product_check=product_check,
                index_profile=index_profile,
            )
        return {"payload_type": "none"}

    def _evidence_refs(
        self,
        instrument: InstrumentRef,
        snapshot: Snapshot | None,
        business_evidence=None,
    ) -> list[dict]:
        refs: list[dict] = [e.to_dict() for e in instrument.evidence]
        if business_evidence is not None:
            for source in business_evidence.sources:
                refs.append(
                    {
                        "evidence_id": source.get("evidence_id"),
                        "kind": source.get("kind", "vendor_api"),
                        "publisher": source.get("publisher"),
                        "url": source.get("url"),
                        "retrieved_at": source.get("retrieved_at"),
                        "fields_cited": list(source.get("fields_cited") or []),
                        "note": source.get("note", ""),
                    }
                )
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


def _index_profile_plan(
    profile: dict, *, signal: dict | None = None, note_asset: str | None = None
) -> dict:
    metrics = profile.get("metrics") or {}
    model = MODELS_BY_ID["index_valuation_profile"]
    return {
        "primary_model": "index_valuation_profile" if metrics else None,
        "model_version": model.version,
        "model_status": "research",
        "cross_check_model": None,
        "required_checks": [
            "同源同列月度参考池，有效月份不少于 54/60",
            "比较值不参与自己的历史排名",
            "PE 与 PB 分别计算，不拼综合分",
        ],
        "selection_reason": note_asset
        or "宽基指数用描述性估值画像：只给出当前值在历史分布中的位置，不产生波段信号",
        "research_signals": [
            {
                "model_id": "index_strategy_a_valuation_reversion",
                "model_version": MODELS_BY_ID["index_strategy_a_valuation_reversion"].version,
                "model_status": "research",
                "approved": False,
                "status": (signal or {}).get("status"),
                "signal": (signal or {}).get("signal"),
                "reason_code": rc.INDEX_SIGNAL_RESEARCH_ONLY,
                "note": "候选 A 已实现为研究信号并前向记账；规则未批准，结论仍为 undetermined",
            }
        ]
        if signal is not None
        else [],
        "not_selected": [
            {
                "model_id": "index_strategy_b_cross_confirmation",
                "reason_code": rc.INDEX_STRATEGY_NOT_IMPLEMENTED,
                "reason": "候选 B（PE+PB 交叉确认）与正式策略状态尚未实现，本版不输出该信号",
            },
        ],
        "candidate_models": [],
        "profile_status": profile.get("status"),
        "window_rule": profile.get("window_rule"),
        "series_ids": profile.get("series_ids"),
    }


def _index_profile_result(profile: dict, instrument: InstrumentRef) -> dict:
    model = MODELS_BY_ID["index_valuation_profile"]
    metrics = profile.get("metrics") or {}
    return {
        "result_type": "index_profile",
        "model_id": "index_valuation_profile",
        "model_version": model.version,
        "model_status": "research",
        "is_trading_signal": False,
        "instrument_id": instrument.instrument_id,
        "as_of": profile.get("as_of"),
        "status": profile.get("status"),
        "series_ids": profile.get("series_ids"),
        "metrics": {
            name: {
                "percentile": block.get("percentile"),
                "percentile_display": block.get("percentile_display"),
                "valid_months": block["window"].get("valid_months"),
                "pool_size": block["window"]["pool"].get("size"),
            }
            for name, block in metrics.items()
        },
    }


def _profile_missing(profile: dict | None) -> list[dict]:
    if not profile:
        return []
    out: list[dict] = []
    for item in profile.get("missing") or []:
        out.append(
            {
                "fact_id": f"series.{item.get('metric')}",
                "metric": item.get("metric"),
                "status": "insufficient_coverage",
                "note": f"{item.get('label') or ''}：{item.get('reason')}".strip("："),
            }
        )
    return out


def _equity_model_plan(valuation) -> dict:
    applicability = valuation.applicability
    conclusions = [
        item.get("note") or item.get("conclusion")
        for item in applicability.get("reasons") or []
        if item.get("note") or item.get("conclusion")
    ]
    return {
        "primary_model": valuation.model_id,
        "model_version": valuation.model_version,
        "model_status": "research",
        "cross_check_model": (valuation.cross_check or {}).get("model_id")
        if valuation.cross_check
        else applicability.get("cross_check_model"),
        "required_checks": _required_checks(valuation),
        "selection_reason": "；".join(str(x) for x in conclusions[:4])
        or "未选主模型",
        "not_selected": list(applicability.get("rejected_models") or []),
        # 个股的候选模型不是「登记表里的描述符」，而是未被选中的路线，
        # 因此这里留空，避免与阶段一 planned_model_plan 的结构混用
        "candidate_models": [],
        "family": applicability.get("family"),
        "family_label": applicability.get("family_label"),
        "applicable_model_category": applicability.get("applicable_model_category"),
        "evidence_id": applicability.get("evidence_id"),
        "evidence_version": applicability.get("evidence_version"),
        "selection_policy_version": (applicability.get("policy") or {}).get(
            "policy_version"
        ),
        "applicability": applicability,
    }


def _merge_missing(
    first: list[dict], second: list[dict]
) -> list[dict]:
    """合并两个缺口清单：同一 fact_id 只保留一条，优先保留带模型解释的那条。"""
    merged: dict[str, dict] = {}
    for item in first + second:
        key = str(item.get("fact_id") or item.get("metric") or "")
        if key in merged:
            existing = merged[key]
            if not existing.get("note") and item.get("note"):
                merged[key] = item
            continue
        merged[key] = item
    return sorted(merged.values(), key=lambda x: str(x.get("fact_id")))


def _required_checks(valuation) -> list[str]:
    model_id = valuation.model_id
    if model_id == "bank_dividend_discount":
        return [
            "核心一级资本充足率不低于适用下限",
            "拨备覆盖率不低于适用下限",
            "不良率趋势不恶化",
            "派息率不超过资本一致性上限",
        ]
    if model_id == "equity_dividend_discount":
        return [
            "派息率不超过当年每股收益（超出需解释）",
            "终值增长不超过 ROE × 留存率",
            "特别分红不进入可持续 DPS",
        ]
    if model_id == "equity_dcf_fcff":
        return [
            "FCFF 与经营现金流勾稽差额在容差内",
            "权益桥接每项只计一次",
            "末期现金流为正才启用永续终值",
        ]
    return []


def _assumption_summary(
    primary: AssumptionSet | None,
    cross: AssumptionSet | None,
    valuation,
) -> list[dict]:
    out: list[dict] = []
    for label, item in (("primary", primary), ("cross_check", cross)):
        if item is None:
            continue
        out.append(
            {
                "role": label,
                "assumption_id": item.assumption_id,
                "model_id": item.model_id,
                "model_version": item.model_version,
                "status": item.status,
                "policy_version": item.policy_version,
                "base_fiscal_year": item.base_fiscal_year,
                "applicable_as_of": item.applicable_as_of,
                "horizon_years": item.horizon_years,
                "discount_rate": item.discount_rate.value,
                "discount_rate_kind": item.discount_rate.provenance.kind,
                "discount_rate_source": item.discount_rate.provenance.source,
                "terminal": item.terminal.to_dict(),
                "scenarios": list(item.scenarios),
                "source_path": item.source_path,
                "notes": item.notes,
            }
        )
    _ = valuation
    return out


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
