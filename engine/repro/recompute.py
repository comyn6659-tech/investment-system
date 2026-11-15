"""正式结论复算：按记录引用的快照、政策与行情重算并逐项对比。

支持三类记录：
1. 新记录（带 `trace`）：校验财务快照/行情快照/政策文件哈希后重算；
2. 旧个股记录：若引用的单标的快照文件仍在，或能映射到保留的批量快照，尽力重算；
3. 旧银行记录（`bank-valuation-v2.1:*`）：按已提交的银行估值输入重算。
旧记录缺少追溯字段、或派生 id 无法映射到保留快照时，明确返回「不可复算」及原因。
"""

from __future__ import annotations

import json
from functools import lru_cache
from datetime import date
from pathlib import Path
from typing import Any

from engine.bank.conclusion import BINDING_TEXT, valuation_for as bank_valuation_for
from engine.data.snapshot import Snapshot
from engine.evidence.models import EvidenceBook
from engine.repro import trace as policy_trace
from engine.policy.equity import load_policy
from engine.policy.freshness import check_freshness
from engine.policy.trial import evaluate_company
from engine.policy.valuation import load_macro_series
from engine.pool_valuation.adapter import BatchStore, build_snapshot, load_evidence

ROOT = Path(__file__).resolve().parent.parent.parent
EQUITY_POLICY = ROOT / "data" / "policies" / "equity-policy-v1.1.json"
BANK_POLICY = ROOT / "data" / "policies" / "bank-valuation-v2.1.json"
MACRO = ROOT / "data" / "policy-trial" / "macro" / "cn-10y-yield.json"
POLICY_EVIDENCE = ROOT / "data" / "policy-trial" / "evidence"
POOL_VALUATION_RESULTS = (
    ROOT / "data" / "pool-valuation" / "pool-member-valuation.json",
    ROOT / "data" / "pool-valuation" / "cyclical-rerun.json",
)
REL_TOL = 1e-9


def _rel_diff(recorded: float | None, recomputed: float | None) -> float | None:
    if recorded is None or recomputed is None:
        return None
    if recorded == recomputed:
        return 0.0
    scale = max(abs(float(recorded)), 1e-12)
    return abs(float(recomputed) - float(recorded)) / scale


def _same_number(recorded: Any, recomputed: Any) -> bool:
    if recorded is None or recomputed is None:
        return recorded is None and recomputed is None
    try:
        return abs(float(recorded) - float(recomputed)) <= REL_TOL * max(abs(float(recorded)), 1e-12)
    except (TypeError, ValueError):
        return recorded == recomputed


def _compare(record: dict[str, Any], recomputed: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    recorded_values = record.get("values") or {}
    recomputed_values = recomputed.get("values") or {}
    for key in ("pessimistic", "base", "optimistic"):
        fields.append(
            {
                "name": f"values.{key}",
                "recorded": recorded_values.get(key),
                "recomputed": recomputed_values.get(key),
                "relative_difference": _rel_diff(
                    recorded_values.get(key), recomputed_values.get(key)
                ),
                "ok": _same_number(recorded_values.get(key), recomputed_values.get(key)),
            }
        )
    for name in ("price", "price_date", "grade", "margin_of_safety", "conclusion", "binding_condition"):
        recorded = record.get(name)
        fresh = recomputed.get(name)
        numeric = name in ("price", "margin_of_safety")
        fields.append(
            {
                "name": name,
                "recorded": recorded,
                "recomputed": fresh,
                "relative_difference": _rel_diff(recorded, fresh) if numeric else None,
                "ok": _same_number(recorded, fresh) if numeric else recorded == fresh,
            }
        )
    return fields


def _not_recomputable(record: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "record_id": record.get("record_id"),
        "code": record.get("code"),
        "analysis_date": record.get("analysis_date"),
        "status": "not_recomputable",
        "reason": reason,
        "fields": [],
    }


# --- 引用解析 ---------------------------------------------------------------
@lru_cache(maxsize=4)
def _load_batch(directory: str) -> BatchStore:
    return BatchStore(Path(directory))


def _batch_dirs() -> dict[str, Path]:
    output: dict[str, Path] = {}
    for manifest in (ROOT / "data" / "pool-valuation").glob("snapshot-qps-*/manifest.json"):
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        snapshot_id = str(payload.get("snapshot_id") or "")
        if snapshot_id:
            output[snapshot_id] = manifest.parent
    return output


def _derived_sources() -> dict[str, tuple[str, str]]:
    """派生 snapshot_id → (批量快照 id, 代码)。"""
    output: dict[str, tuple[str, str]] = {}
    for path in POOL_VALUATION_RESULTS:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        batch_id = str((payload.get("meta") or {}).get("batch_snapshot_id") or "")
        for row in payload.get("results") or []:
            snapshot_id = str(row.get("batch_snapshot_id") or row.get("snapshot_id") or "")
            code = str(row.get("code") or "").zfill(6)
            if snapshot_id and code:
                output[snapshot_id] = (batch_id, code)
    return output


def _resolve_price(record: dict[str, Any], trace: dict[str, Any] | None) -> tuple[float | None, str | None, str | None]:
    """返回 (价格, 价格日期, 错误原因)。"""
    if trace:
        quote = trace.get("quote") or {}
        kind = str(quote.get("kind") or "")
        path = ROOT / str(quote.get("path") or "")
        code = str(record.get("code") or "").zfill(6)
        if kind == "live_quotes" or kind == "batch_price":
            price, price_date = policy_trace.quote_price(path, code)
            return price, price_date, None
        if kind == "bank_inputs_price":
            import csv
            import gzip

            with gzip.open(path, "rt", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    if str(row.get("code")).zfill(6) == code:
                        return float(row["price"]), str(row.get("price_date") or "")[:10], None
            return None, None, f"银行输入表没有 {code}"
        if kind == "snapshot_fact":
            payload = json.loads(path.read_text(encoding="utf-8"))
            snapshot = Snapshot.from_dict(payload, expected_id=str(quote.get("snapshot_id") or ""))
            price = snapshot.fact("price.close")
            if price is None:
                return None, None, f"快照 {snapshot.snapshot_id} 缺少 price.close"
            return float(price.value), str(price.period)[:10], None
    return None, None, None


def _verify_trace(record: dict[str, Any], trace: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """校验追溯文件；返回 (批量快照目录或 None, 错误原因)。"""
    policy = trace.get("policy") or {}
    policy_file = BANK_POLICY if record.get("policy_version") == "bank-valuation-v2.1" else EQUITY_POLICY
    if policy.get("sha256") != policy_trace.file_sha256(policy_file):
        return None, "政策文件内容已变化（sha256 不一致）"
    financial = trace.get("financial") or {}
    kind = str(financial.get("kind") or "")
    if kind == "batch_snapshot":
        directory = ROOT / str(financial.get("path") or "")
        manifest = directory / "manifest.json"
        if not manifest.exists():
            return None, f"批量快照不存在：{financial.get('path')}"
        if policy_trace.file_sha256(manifest) != financial.get("manifest_sha256"):
            return None, "批量快照清单哈希不一致"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if payload.get("snapshot_id") != financial.get("snapshot_id"):
            return None, "批量快照 id 与清单不一致"
        try:
            batch = _load_batch(str(directory.resolve()))
        except Exception as exc:  # noqa: BLE001
            return None, f"批量快照校验失败：{type(exc).__name__}: {exc}"
        return batch.directory, None
    if kind == "policy_snapshot":
        path = ROOT / str(financial.get("path") or "")
        if not path.exists():
            return None, f"单标的快照文件不存在：{financial.get('path')}"
        if policy_trace.file_sha256(path) != financial.get("sha256"):
            return None, "单标的快照文件哈希不一致"
        return None, None
    if kind == "bank_valuation_inputs":
        path = ROOT / str(financial.get("path") or "")
        if not path.exists() or policy_trace.file_sha256(path) != financial.get("sha256"):
            return None, "银行估值输入文件缺失或哈希不一致"
        for item in financial.get("files") or []:
            extra = ROOT / str(item.get("path") or "")
            if not extra.exists() or policy_trace.file_sha256(extra) != item.get("sha256"):
                return None, f"银行估值依赖文件缺失或哈希不一致：{item.get('path')}"
        return None, None
    return None, f"不支持的财务引用类型：{kind}"


# --- 重算 -------------------------------------------------------------------
def _evaluate_stock_batch(
    record: dict[str, Any], directory: Path, price: float | None, price_date: str | None
) -> dict[str, Any]:
    code = str(record.get("code") or "").zfill(6)
    policy = load_policy(EQUITY_POLICY)
    macro = load_macro_series(MACRO)
    batch = _load_batch(str(Path(directory).resolve()))
    snapshot = build_snapshot(
        batch,
        code,
        price_override=price,
        price_date_override=price_date,
    )
    evidence = load_evidence(
        code,
        name=batch.name_for(code),
        industry=batch.industry_for(code),
        snapshot=snapshot,
    )
    result = evaluate_company(policy, snapshot=snapshot, evidence=evidence, macro=macro, code=code)
    _apply_freshness(policy, snapshot, result, record)
    return _stock_summary(result)


def _evaluate_stock_snapshot(record: dict[str, Any], snapshot_id: str) -> dict[str, Any]:
    path = policy_trace.snapshot_file_for(snapshot_id)
    if path is None:
        raise FileNotFoundError(f"找不到单标的快照 {snapshot_id}")
    snapshot = Snapshot.from_dict(
        json.loads(path.read_text(encoding="utf-8")), expected_id=snapshot_id
    )
    policy = load_policy(EQUITY_POLICY)
    macro = load_macro_series(MACRO)
    evidence = EvidenceBook(POLICY_EVIDENCE).get(str(record["instrument_id"]))
    result = evaluate_company(policy, snapshot=snapshot, evidence=evidence, macro=macro, code=str(record["code"]))
    _apply_freshness(policy, snapshot, result, record)
    return _stock_summary(result)


def _apply_freshness(policy, snapshot, result: dict[str, Any], record: dict[str, Any]) -> None:
    try:
        analysis_date = date.fromisoformat(str(record.get("analysis_date")))
    except ValueError:
        return
    hits = check_freshness(policy, snapshot, analysis_date=analysis_date)
    if hits and result.get("values"):
        result["status"] = "partial"
        result["status_reason"] = "；".join(hit.detail for hit in hits)
        result["grade"] = {
            "grade": "C",
            "margin_of_safety": None,
            "unmet_conditions": [hit.code for hit in hits],
        }
        result["trial_conclusion"] = {"conclusion": "undetermined", "reason": "数据新鲜度护栏触发"}
        result["reason_codes"] = [hit.code for hit in hits]


def _stock_summary(result: dict[str, Any]) -> dict[str, Any]:
    values = result.get("values") or {}
    thresholds = (result.get("trial_conclusion") or {}).get("thresholds") or {}
    return {
        "price": result.get("price"),
        "price_date": str(result.get("price_as_of") or ""),
        "values": {
            "pessimistic": values.get("pessimistic"),
            "base": values.get("base"),
            "optimistic": values.get("optimistic"),
        },
        "grade": (result.get("grade") or {}).get("grade"),
        "margin_of_safety": (result.get("grade") or {}).get("margin_of_safety"),
        "conclusion": (result.get("trial_conclusion") or {}).get("conclusion"),
        "binding_condition": str(thresholds.get("binding_condition_text") or ""),
    }


def _bank_summary(record: dict[str, Any]) -> dict[str, Any]:
    valuation = bank_valuation_for(
        str(record["code"]),
        price=record.get("price"),
        price_date=record.get("price_date"),
    )
    if valuation is None:
        raise ValueError(f"银行估值输入中没有 {record['code']}")
    values = valuation.get("values") or {}
    return {
        "price": valuation.get("price"),
        "price_date": str(valuation.get("price_date") or ""),
        "values": {
            "pessimistic": values.get("pessimistic"),
            "base": values.get("base"),
            "optimistic": values.get("optimistic"),
        },
        "grade": "B",
        "margin_of_safety": valuation.get("margin_of_safety"),
        "conclusion": valuation.get("conclusion"),
        "binding_condition": BINDING_TEXT.get(valuation.get("binding_condition"), ""),
    }


def recompute(record: dict[str, Any]) -> dict[str, Any]:
    """复算一条账本记录；返回带逐项对比的 dict。"""
    if record.get("record_type") == "reversal":
        return _not_recomputable(record, "改判记录没有价格与三档价值，不参与复算")
    trace = record.get("trace")
    policy_version = str(record.get("policy_version") or "")
    if policy_version == "bank-valuation-v2.1":
        if trace:
            _batch, error = _verify_trace(record, trace)
            if error:
                return _not_recomputable(record, error)
            price, price_date, error = _resolve_price(record, trace)
            if error:
                return _not_recomputable(record, error)
            if price is not None and not _same_number(record.get("price"), price):
                return _not_recomputable(
                    record, f"引用的行情快照价格 {price} 与记录价格 {record.get('price')} 不一致"
                )
            if price_date and record.get("price_date") != price_date:
                return _not_recomputable(
                    record, f"引用的行情快照价格日期 {price_date} 与记录不一致"
                )
        try:
            fresh = _bank_summary(record)
        except Exception as exc:  # noqa: BLE001
            return _not_recomputable(record, f"银行估重复算失败：{type(exc).__name__}: {exc}")
        return _result(record, fresh, trace_present=bool(trace), note=None)

    # 个股
    if trace:
        directory, error = _verify_trace(record, trace)
        if error:
            return _not_recomputable(record, error)
        financial = trace.get("financial") or {}
        kind = financial.get("kind")
        price, price_date, error = _resolve_price(record, trace)
        if error:
            return _not_recomputable(record, error)
        if price is not None and not _same_number(record.get("price"), price):
            return _not_recomputable(
                record, f"引用的行情快照价格 {price} 与记录价格 {record.get('price')} 不一致"
            )
        if price_date and record.get("price_date") != price_date:
            return _not_recomputable(
                record, f"引用的行情快照价格日期 {price_date} 与记录不一致"
            )
        if kind == "batch_snapshot":
            fresh = _evaluate_stock_batch(record, directory, price, price_date)
        else:
            fresh = _evaluate_stock_snapshot(record, str(financial.get("snapshot_id") or record["snapshot_id"]))
        return _result(record, fresh, trace_present=True, note=None)

    # 旧记录：优先单标的快照文件，其次批量结果映射。
    snapshot_id = str(record.get("snapshot_id") or "")
    snapshot_file = policy_trace.snapshot_file_for(snapshot_id)
    note = "旧记录无追溯字段；使用保留的单标的快照文件尽力复算"
    try:
        if snapshot_file is not None:
            fresh = _evaluate_stock_snapshot(record, snapshot_id)
            return _result(record, fresh, trace_present=False, note=note)
        sources = _derived_sources()
        if snapshot_id in sources:
            batch_id, code = sources[snapshot_id]
            directories = _batch_dirs()
            directory = directories.get(batch_id)
            if directory is None:
                return _not_recomputable(record, f"批量快照 {batch_id} 不存在")
            fresh = _evaluate_stock_batch(record, directory, None, None)
            return _result(
                record,
                fresh,
                trace_present=False,
                note=f"旧记录无追溯字段；由保留的批量快照 {batch_id} 映射复算",
            )
    except Exception as exc:  # noqa: BLE001
        return _not_recomputable(record, f"旧记录复算失败：{type(exc).__name__}: {exc}")
    return _not_recomputable(
        record,
        "旧记录缺少追溯字段（未记录批量财务快照 ID / 行情快照 ID / 政策 sha256），"
        f"且派生快照 {snapshot_id} 不在任何保留的批量结果中，无法证明所用批量快照版本",
    )


def _result(
    record: dict[str, Any],
    fresh: dict[str, Any],
    *,
    trace_present: bool,
    note: str | None,
) -> dict[str, Any]:
    fields = _compare(record, fresh)
    ok = all(item["ok"] for item in fields)
    return {
        "record_id": record.get("record_id"),
        "code": record.get("code"),
        "analysis_date": record.get("analysis_date"),
        "status": "consistent" if ok else "inconsistent",
        "trace_present": trace_present,
        "note": note,
        "fields": fields,
        "mismatched_fields": [item["name"] for item in fields if not item["ok"]],
    }


__all__ = ["REL_TOL", "recompute"]
