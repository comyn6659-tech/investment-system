"""返工回归测试：逐条覆盖设计方 R1–R10。

每个测试都直接构造非法/边界对象，再调用真实出口，
不依赖「先自动降级、再宣称拒绝生效」这种假验证。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from engine.contracts.enums import AnalysisStatus, Decision, ModelStatus, ValuationState
from engine.contracts.result import AnalysisResult
from engine.contracts.validation import ContractViolation, validate_result
from engine.data.facts import Fact, FactError
from engine.data.snapshot import Snapshot, SnapshotError, SnapshotStore, facts_available_by
from engine.identity.catalog import IdentityCatalog
from engine.identity.resolver import resolve
from engine.pipeline import Analyzer
from engine.reporting.json_exporter import export_json
from engine.reporting.text_summary import render_text

ROOT = Path(__file__).resolve().parent.parent


# --- R1：快照必须属于请求标的 ------------------------------------------------
def test_r1_snapshot_from_other_instrument_is_rejected(analyzer, snapshot_dir):
    store = SnapshotStore(snapshot_dir)
    other = store.latest_for("ETF:510500.SH")
    assert other is not None

    result = analyzer.analyze("510300", snapshot_id=other)
    assert result.analysis_status == AnalysisStatus.ENGINE_ERROR.value
    assert result.decision == Decision.UNDETERMINED.value
    assert "SNAPSHOT_INSTRUMENT_MISMATCH" in result.reason_codes
    assert result.snapshot_id is None
    assert result.payload.get("price") is None, "不得把另一只 ETF 的价格挂到本标的"
    assert validate_result(result) == []


def test_r1_each_instrument_uses_its_own_latest_snapshot(analyzer):
    for query, expected_id in (
        ("510300", "ETF:510300.SH"),
        ("510500", "ETF:510500.SH"),
        ("512100", "ETF:512100.SH"),
    ):
        result = analyzer.analyze(query)
        assert result.instrument_id == expected_id
        assert result.snapshot_id is not None
        assert analyzer.store.load(result.snapshot_id).instrument_id == expected_id


def test_r1_asset_class_mismatch_is_rejected(analyzer, snapshot_dir, tmp_path):
    store = SnapshotStore(snapshot_dir)
    source = store.load(store.latest_for("INDEX:000300.SH"))
    # 造一个「instrument_id 对、asset_class 错」的合法快照（哈希自洽）
    mismatched = Snapshot(
        instrument_id=source.instrument_id,
        asset_class="etf",
        as_of=source.as_of,
        available_at=source.available_at,
        fetched_at=source.fetched_at,
        facts=source.facts,
        coverage=source.coverage,
    )
    tmp_store = SnapshotStore(tmp_path)
    tmp_store.save(mismatched)

    instrument = analyzer.catalog.get("INDEX:000300.SH")
    original_store = analyzer.store
    analyzer.store = SnapshotStore(tmp_path)
    try:
        with pytest.raises(SnapshotError) as exc:
            analyzer._load_snapshot(instrument, mismatched.snapshot_id)
    finally:
        analyzer.store = original_store
    assert exc.value.code == "SNAPSHOT_ASSET_CLASS_MISMATCH"


# --- R2：硬规则在所有出口共同执行 -------------------------------------------
def _valid_etf_result(analyzer) -> AnalysisResult:
    return analyzer.analyze("510300")


def test_r2_research_cannot_export_formal_price_decision(analyzer):
    result = _valid_etf_result(analyzer)
    result.analysis_status = AnalysisStatus.COMPLETE.value
    result.model_status = ModelStatus.RESEARCH.value
    result.valuation_state = ValuationState.NEUTRAL.value
    result.decision = Decision.WAIT.value
    result.reason_codes = []

    for exporter in (lambda: export_json(result), lambda: render_text(result.to_dict())):
        with pytest.raises(ContractViolation):
            exporter()


def test_r2_unsupported_cannot_carry_valuation_payload(analyzer):
    result = analyzer.analyze("工商银行")
    result.payload["valuation"] = {"buy_hi": {"value": 99, "unit": "CNY", "currency": "CNY"}}
    with pytest.raises(ContractViolation):
        export_json(result)


def test_r2_missing_status_fields_are_violations(analyzer):
    data = analyzer.analyze("510300").to_dict()
    for key in ("analysis_status", "valuation_state", "decision"):
        broken = dict(data)
        broken.pop(key)
        problems = validate_result(broken)
        assert any("缺少必填字段" in p for p in problems), key


def test_r2_null_status_fields_are_violations(analyzer):
    data = analyzer.analyze("510300").to_dict()
    for key in ("analysis_status", "valuation_state", "decision"):
        broken = dict(data)
        broken[key] = None
        assert validate_result(broken)


def test_r2_research_signal_requires_model_provenance(analyzer):
    result = _valid_etf_result(analyzer)
    result.model_results = [{"result_type": "research_signal", "value": 1}]
    with pytest.raises(ContractViolation):
        export_json(result)


def test_r2_priced_payload_requires_complete_validated(analyzer):
    result = _valid_etf_result(analyzer)
    result.payload["premium_discount"] = {"value": 0.1, "unit": "percent", "currency": None}
    with pytest.raises(ContractViolation):
        export_json(result)


# --- R3：显式交易所冲突 ------------------------------------------------------
@pytest.mark.parametrize("text", ["601398.SZ", "510300.SZ"])
def test_r3_wrong_exchange_is_rejected(text, catalog):
    resolution = resolve(text, catalog)
    assert resolution.status == "ambiguous"
    assert resolution.instrument is None
    assert "IDENTITY_VENUE_CONFLICT" in resolution.reason_codes


def test_r3_prefix_suffix_conflict_is_rejected(catalog):
    resolution = resolve("SZ601398.SH", catalog)
    assert resolution.instrument is None
    assert "IDENTITY_VENUE_SYNTAX_CONFLICT" in resolution.reason_codes


def test_r3_matching_exchange_still_resolves(catalog):
    assert resolve("601398.SH", catalog).instrument.instrument_id == "EQUITY:601398.SH"
    assert resolve("000852.SZ", catalog).instrument.instrument_id == "EQUITY:000852.SZ"


def test_r3_cli_json_returns_clarification_not_crash():
    from tests.test_cli import run_cli

    proc = run_cli("--json", "601398.SZ")
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["analysis_status"] == "needs_clarification"
    assert "IDENTITY_VENUE_CONFLICT" in payload["reason_codes"]


# --- R4：无实体的范围排除也要是合法契约 --------------------------------------
def test_r4_heuristic_scope_exclusion_is_structured(analyzer):
    result = analyzer.analyze("全球ETF")
    assert result.analysis_status == AnalysisStatus.UNSUPPORTED.value
    assert result.instrument is None
    assert result.decision == Decision.UNDETERMINED.value
    assert result.valuation_state == ValuationState.NOT_APPLICABLE.value
    assert "OUT_OF_SCOPE_CROSS_BORDER" in result.reason_codes
    assert result.payload == {"payload_type": "none"}
    assert validate_result(result) == []


def test_r4_cli_json_for_scope_only_input():
    from tests.test_cli import run_cli

    proc = run_cli("--json", "全球ETF")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["instrument"] is None
    assert payload["analysis_status"] == "unsupported"


# --- R5：时间按带时区的时刻比较 ----------------------------------------------
def _snapshot_with(available_at: str | None) -> Snapshot:
    fact = Fact.known(fact_id="t", metric="m", value=1, available_at=available_at)
    return Snapshot(
        instrument_id="INDEX:000300.SH",
        asset_class="index",
        as_of="2026-11-10",
        available_at="2026-11-10T15:00:00+08:00",
        fetched_at="2026-11-11T02:00:00+08:00",
        facts=[fact],
    )


def test_r5_utc_fact_is_not_released_before_local_time():
    snap = _snapshot_with("2026-11-10T08:00:00+00:00")  # 当地 16:00
    assert facts_available_by(snap, "2026-11-10T12:00:00+08:00") == []
    assert len(facts_available_by(snap, "2026-11-10T17:00:00+08:00")) == 1


def test_r5_date_only_is_not_known_at_midnight():
    snap = _snapshot_with("2026-11-10")
    assert facts_available_by(snap, "2026-11-10T00:00:00+08:00") == []
    # 当日结束时才视为已知（保守上界）
    assert len(facts_available_by(snap, "2026-11-10T23:59:59+08:00")) == 1


def test_r5_unknown_availability_never_released():
    snap = _snapshot_with(None)
    assert facts_available_by(snap, "2099-12-31") == []


def test_r5_snapshot_records_precision(snapshot_dir):
    store = SnapshotStore(snapshot_dir)
    for entry in store.load_manifest():
        snap = store.load(entry["snapshot_id"])
        coverage = snap.coverage
        assert "point_in_time_granularity" in coverage
        assert "availability_precision_counts" in coverage
        # 只要存在未知精度，就不能声称 point_in_time_safe
        if coverage["availability_precision_counts"]["unknown"]:
            assert coverage["point_in_time_safe"] is False


# --- R6：不得把新行情倒填到旧日期 --------------------------------------------
def test_r6_builder_uses_actual_market_date():
    import pandas as pd

    from tools import build_snapshots as builder

    dated = "2030-01-02"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            builder.ak,
            "stock_zh_index_value_csindex",
            lambda symbol: pd.DataFrame({"日期": [dated], "市盈率1": [12.0], "股息率1": [3.0]}),
        )
        mp.setattr(
            builder.ak,
            "stock_index_pe_lg",
            lambda symbol: pd.DataFrame({"日期": [dated], "滚动市盈率": [12.0]}),
        )
        mp.setattr(
            builder.ak,
            "stock_index_pb_lg",
            lambda symbol: pd.DataFrame({"日期": [dated], "市净率": [1.0]}),
        )
        mp.setattr(
            builder.ak,
            "stock_zh_index_daily_em",
            lambda symbol: pd.DataFrame({"date": [dated], "open": [1.0], "close": [9999.0]}),
        )
        mp.setattr(pd.DataFrame, "to_csv", lambda self, *a, **k: "")
        snap = builder.build_index_snapshot("000300", now=builder.datetime(2030, 1, 2, 16, 0, tzinfo=builder.TZ))
    fact = snap.fact("index.level")
    assert fact.period == dated
    assert fact.value == 9999.0


def test_r6_builder_refuses_mismatched_expected_date(monkeypatch):
    import pandas as pd

    from tools import build_snapshots as builder

    monkeypatch.setattr(
        builder.ak,
        "stock_zh_index_daily_em",
        lambda symbol: pd.DataFrame({"date": ["2030-01-02"], "open": [1.0], "close": [1.0]}),
    )
    monkeypatch.setattr(pd.DataFrame, "to_csv", lambda self, *a, **k: "")
    with pytest.raises(builder.BuildError):
        builder.build_index_snapshot(
            "000300",
            expected_as_of="2030-01-03",
            now=builder.datetime(2030, 1, 3, 16, 0, tzinfo=builder.TZ),
        )


def test_r6_intraday_bar_is_not_labelled_as_close(monkeypatch):
    import pandas as pd

    from tools import build_snapshots as builder

    monkeypatch.setattr(
        builder.ak,
        "stock_zh_index_daily_em",
        lambda symbol: pd.DataFrame({"date": ["2030-01-02"], "open": [1.0], "close": [1.0]}),
    )
    monkeypatch.setattr(
        builder.ak,
        "stock_zh_index_value_csindex",
        lambda symbol: pd.DataFrame({"日期": ["2030-01-02"], "市盈率1": [1.0], "股息率1": [1.0]}),
    )
    monkeypatch.setattr(
        builder.ak,
        "stock_index_pe_lg",
        lambda symbol: pd.DataFrame({"日期": ["2030-01-02"], "滚动市盈率": [1.0]}),
    )
    monkeypatch.setattr(
        builder.ak,
        "stock_index_pb_lg",
        lambda symbol: pd.DataFrame({"日期": ["2030-01-02"], "市净率": [1.0]}),
    )
    monkeypatch.setattr(pd.DataFrame, "to_csv", lambda self, *a, **k: "")
    snap = builder.build_index_snapshot("000300", now=builder.datetime(2030, 1, 2, 10, 0, tzinfo=builder.TZ))
    level = snap.fact("index.level")
    assert level.available_at is None
    assert snap.coverage["build"]["bar_status"] == "intraday"


# --- R7：调查器不得写死结论 --------------------------------------------------
def test_r7_total_return_probe_reflects_actual_result(monkeypatch):
    import pandas as pd

    from tools import probe_data_sources as probe

    monkeypatch.setattr(
        probe.ak,
        "stock_zh_index_daily_em",
        lambda symbol: pd.DataFrame({"date": ["2026-11-10"], "close": [100.0]}),
    )
    probe.probe_index_total_return()
    record = probe.PROBES[-1]
    assert record["status"] == "ok"
    assert record["rows"] > 0
    assert record["called"] is True
    assert record["parameters"]["targets"]["H00300"][0] == "shH00300"


def test_r7_total_return_probe_marks_empty_and_errors(monkeypatch):
    import pandas as pd

    from tools import probe_data_sources as probe

    monkeypatch.setattr(
        probe.ak, "stock_zh_index_daily_em", lambda symbol: pd.DataFrame({"date": [], "close": []})
    )
    probe.probe_index_total_return()
    record = probe.PROBES[-1]
    assert record["status"] == "failed"
    # 三条目标都没有数据；不再把「空表」写成笼统的接口错误
    assert record["sample"]["per_series"] == []
    assert all(t["outcome"] != "data" for t in record["sample"]["targets"])


def test_r7_untested_tracking_error_is_not_counted(monkeypatch):
    from tools import probe_data_sources as probe

    probe.probe_etf_tracking_error()
    record = probe.PROBES[-1]
    assert record["status"] == "not_tested"
    assert record["called"] is False


def test_r7_frequency_is_derived_from_observations():
    from engine.data.timeutil import infer_frequency

    monthly = [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]
    weekly = [date(2026, 1, 2), date(2026, 1, 9), date(2026, 1, 16), date(2026, 1, 23)]
    daily = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8)]
    assert infer_frequency(monthly) == "monthly"
    assert infer_frequency(weekly) == "weekly"
    assert infer_frequency(daily) == "daily"


# --- R8：快照加载必须校验 ID 与结构 ------------------------------------------
def test_r8_missing_snapshot_id_is_rejected(snapshot_dir):
    store = SnapshotStore(snapshot_dir)
    data = store.load(store.load_manifest()[0]["snapshot_id"]).to_dict()
    data.pop("snapshot_id")
    with pytest.raises(SnapshotError):
        Snapshot.from_dict(data)


def test_r8_content_change_without_new_id_is_rejected(snapshot_dir):
    store = SnapshotStore(snapshot_dir)
    data = store.load(store.load_manifest()[0]["snapshot_id"]).to_dict()
    data["facts"][0]["notes"] = "被改过的内容"
    with pytest.raises(SnapshotError):
        Snapshot.from_dict(data)


def test_r8_requested_id_must_match_file_content(snapshot_dir, tmp_path):
    store = SnapshotStore(snapshot_dir)
    source = store.load(store.load_manifest()[0]["snapshot_id"])
    (tmp_path / "snap-0000000000000000.json").write_text(
        json.dumps(source.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    other = SnapshotStore(tmp_path)
    with pytest.raises(SnapshotError) as exc:
        other.load("snap-0000000000000000")
    assert exc.value.code == "SNAPSHOT_ID_MISMATCH"


def test_r8_duplicate_facts_are_rejected(snapshot_dir):
    store = SnapshotStore(snapshot_dir)
    snap = store.load(store.load_manifest()[0]["snapshot_id"])
    data = snap.to_dict()
    data["facts"].append(data["facts"][0])
    data.pop("snapshot_id")
    with pytest.raises(SnapshotError):
        Snapshot.from_dict({**data, "snapshot_id": snap.snapshot_id})


# --- R9：同日多版本按时间选最新 ----------------------------------------------
def test_r9_latest_uses_fetch_time_not_hash_order(tmp_path, snapshot_dir):
    store = SnapshotStore(snapshot_dir)
    entries = [
        {
            "instrument_id": "ETF:510300.SH",
            "as_of": "2026-11-10",
            "snapshot_id": "snap-ffffffffffffffff",
            "fetched_at": "2026-11-10T16:00:00+08:00",
        },
        {
            "instrument_id": "ETF:510300.SH",
            "as_of": "2026-11-10",
            "snapshot_id": "snap-0000000000000000",
            "fetched_at": "2026-11-11T16:00:00+08:00",
        },
    ]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"snapshots": entries}), encoding="utf-8")
    bare = SnapshotStore(tmp_path)
    assert bare.latest_for("ETF:510300.SH") == "snap-0000000000000000"


def test_r9_manifest_records_fetch_time(snapshot_dir):
    store = SnapshotStore(snapshot_dir)
    for entry in store.load_manifest():
        assert entry.get("fetched_at"), "manifest 必须保存抓取/修订时间"


# --- R10：事实反序列化与直接构造规则一致 -------------------------------------
@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), float("-inf")])
def test_r10_from_dict_rejects_invalid_known_values(value):
    with pytest.raises(FactError):
        Fact.from_dict({"fact_id": "x", "metric": "m", "status": "known", "value": value})


def test_r10_direct_and_helper_construction_agree():
    with pytest.raises(FactError):
        Fact(fact_id="x", metric="m", status="known", value=None)
    with pytest.raises(FactError):
        Fact.known(fact_id="x", metric="m", value=None)


def test_r10_missing_status_cannot_smuggle_value_via_from_dict():
    with pytest.raises(FactError):
        Fact.from_dict({"fact_id": "x", "metric": "m", "status": "missing", "value": 0})


# --- A.4：其他公开序列化出口不得绕过校验 -------------------------------------
def test_dump_json_cannot_bypass_validation(analyzer):
    result = analyzer.analyze("510300")
    result.decision = Decision.WAIT.value  # 未 validated 的正式价格判断
    with pytest.raises(ContractViolation):
        result.dump_json()


def test_snapshot_save_refuses_invalid_snapshot(tmp_path):
    from engine.data.facts import Fact
    from engine.data.snapshot import Snapshot, SnapshotError, SnapshotStore

    snap = Snapshot(
        instrument_id="INDEX:000300.SH",
        asset_class="index",
        as_of="2026-11-10",
        available_at="2026-11-10T15:00:00+08:00",
        fetched_at="2026-11-11T02:00:00+08:00",
        facts=[
            Fact.known(fact_id="dup", metric="a", value=1),
            Fact.known(fact_id="dup", metric="a", value=2),
        ],
    )
    with pytest.raises(SnapshotError):
        SnapshotStore(tmp_path).save(snap)


# --- F3：隔离与版本元信息必须受完整性约束 ------------------------------------
def test_f3_metadata_changes_are_caught_by_content_hash(snapshot_dir):
    """notes / fetched_at / producers 都进入内容哈希，改动即身份不符。"""
    store = SnapshotStore(snapshot_dir)
    snap = store.load(store.latest_for("ETF:510300.SH"))
    base = snap.to_dict()

    for field, value in (
        ("notes", "被改过的备注"),
        ("fetched_at", "2026-11-11T23:59:59+08:00"),
        ("producers", ["someone-else"]),
    ):
        tampered = dict(base)
        tampered[field] = value
        with pytest.raises(SnapshotError) as exc:
            Snapshot.from_dict(tampered, expected_id=snap.snapshot_id)
        assert exc.value.code == "SNAPSHOT_HASH_MISMATCH", field


def test_f3_retired_sample_is_out_of_production(analyzer, snapshot_dir):
    """作废样例已移出生产快照目录：按旧 ID 取数只能得到明确的缺失错误。"""
    store = SnapshotStore(snapshot_dir)
    old_id = "snap-23eaf5b0bfc82144"
    assert old_id not in {e["snapshot_id"] for e in store.load_manifest()}
    result = analyzer.analyze("510300", snapshot_id=old_id)
    assert result.analysis_status == AnalysisStatus.ENGINE_ERROR.value
    assert "SNAPSHOT_NOT_FOUND" in result.reason_codes
    assert result.snapshot_id is None


def test_f3_manifest_must_match_snapshot(tmp_path, snapshot_dir):
    """manifest 记录的版本时间被改动时，加载必须报不一致。"""
    source = SnapshotStore(snapshot_dir)
    sid = source.latest_for("ETF:510300.SH")
    snap = source.load(sid)

    target = SnapshotStore(tmp_path)
    (tmp_path / f"{sid}.json").write_text(
        json.dumps(snap.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "snapshots": [
                    {
                        "snapshot_id": sid,
                        "instrument_id": snap.instrument_id,
                        "asset_class": snap.asset_class,
                        "as_of": snap.as_of,
                        "available_at": snap.available_at,
                        "fetched_at": "2026-01-01T00:00:00+08:00",
                        "file": f"{sid}.json",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(SnapshotError) as exc:
        target.load(sid)
    assert exc.value.code == "SNAPSHOT_MANIFEST_MISMATCH"


def test_f3_snapshot_directory_is_self_consistent(snapshot_dir):
    store = SnapshotStore(snapshot_dir)
    assert store.check_consistency() == []


def test_f3_default_analysis_uses_registered_snapshot(analyzer, snapshot_dir):
    store = SnapshotStore(snapshot_dir)
    result = analyzer.analyze("510300")
    assert result.analysis_status == AnalysisStatus.COMPLETE.value
    assert result.model_status == "research"
    assert result.snapshot_id in {e["snapshot_id"] for e in store.load_manifest()}


# --- F1：合法研究结果必须仍可输出（正例） ------------------------------------
def _research_result(analyzer):
    from engine.contracts.enums import AnalysisStatus as AS
    from engine.contracts.enums import Decision as D
    from engine.contracts.enums import ModelStatus as MS

    result = analyzer.analyze("沪深300")
    result.analysis_status = AS.PARTIAL.value
    result.model_status = MS.RESEARCH.value
    result.valuation_state = ValuationState.UNKNOWN.value
    result.decision = D.UNDETERMINED.value
    result.reason_codes = ["RESEARCH_SIGNAL_ONLY", "MISSING_VALUATION_HISTORY"]
    result.model_plan = {
        "primary_model": "index_strategy_a_valuation_reversion",
        "required_checks": [],
        "selection_reason": "研究测算：仅用于验证契约与展示，不构成买点",
        "not_selected": [],
        "candidate_models": [],
    }
    # 该样例只验证 research_signal 契约：清掉真实画像，避免与改写的 model_results 冲突
    result.payload["valuation_profile"] = None
    result.model_results = [
        {
            "result_type": "research_signal",
            "model_id": "index_strategy_a_valuation_reversion",
            "model_version": "0.0.0-research",
            "model_status": "research",
            "pe_percentile_5y": 18.2,
            "note": "研究测算，不是买点",
        }
    ]
    return result


def test_f1_legitimate_research_result_is_allowed(analyzer):
    result = _research_result(analyzer)
    text = export_json(result)  # 不抛异常
    assert "research_signal" in text
    render_text(result.to_dict())
    assert result.decision == "undetermined"


def test_f1_research_signal_requires_registered_model(analyzer):
    result = _research_result(analyzer)
    result.model_results[0]["model_id"] = "nonexistent_model"
    result.model_plan["primary_model"] = "nonexistent_model"
    with pytest.raises(ContractViolation):
        export_json(result)


def test_f1_research_signal_requires_matching_version(analyzer):
    result = _research_result(analyzer)
    result.model_results[0]["model_version"] = "9.9.9"
    with pytest.raises(ContractViolation):
        export_json(result)


def test_f1_research_signal_must_match_model_plan(analyzer):
    result = _research_result(analyzer)
    result.model_plan["primary_model"] = "index_valuation_profile"
    with pytest.raises(ContractViolation):
        export_json(result)


def test_f1_research_signal_not_allowed_on_unsupported(analyzer):
    result = analyzer.analyze("工商银行")
    # 该样例只验证 research_signal 契约：清掉真实画像，避免与改写的 model_results 冲突
    result.payload["valuation_profile"] = None
    result.model_results = [
        {
            "result_type": "research_signal",
            "model_id": "index_strategy_a_valuation_reversion",
            "model_version": "0.0.0-research",
            "model_status": "research",
            "value": 1,
        }
    ]
    result.model_plan["primary_model"] = "index_strategy_a_valuation_reversion"
    with pytest.raises(ContractViolation):
        export_json(result)
