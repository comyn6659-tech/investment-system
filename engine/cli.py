"""命令行入口。

用法：
    python -m engine.cli '工商银行'
    python -m engine.cli --json '沪深300'
    python -m engine.cli --json --snapshot snap-xxxxxxxxxxxxxxxx '510300'
    python -m engine.cli --request examples/requests/ambiguous.json

退出码：
    0  已产出并通过契约校验的结果（任何 analysis_status 都可能）
    2  需要澄清 / 输入非法
    3  引擎错误（快照损坏、数据源失败等）
    4  结果契约校验失败（实现缺陷，需修复，不得当作正常结果）
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from engine import clock, ledger_paths
from engine.repro import trace as policy_trace
from engine.assumptions.schema import load_assumptions
from engine.contracts.validation import ContractViolation
from engine.identity.catalog import RegistryError
from engine.valuation.errors import ValuationError
from engine.pipeline import Analyzer, AnalyzerConfig
from engine.reporting.json_exporter import export_json
from engine.reporting.text_summary import render_text

TZ = ZoneInfo("Asia/Singapore")
ROOT = Path(__file__).resolve().parent.parent


def _bank_query(query: str) -> tuple[str, str] | None:
    """识别银行池/银行样本名称或代码，返回 (code, name)。"""
    from engine.quality.tables import read_table

    path = ROOT / "data" / "bank-pool" / "valuation-v2" / "inputs.csv.gz"
    if not path.exists():
        return None
    text = str(query or "").strip()
    for row in read_table(path):
        code = str(row["code"]).zfill(6)
        name = str(row.get("name") or "")
        if text == code or text == name or (name and name in text):
            return code, name
    return None


def _bank_cli(
    query: str,
    *,
    as_json: bool,
    analysis_date: str | None,
    ledger: Path | None = None,
) -> tuple[str, int]:
    from datetime import date as _date

    from engine.ask.service import AskService
    from engine.bank.conclusion import append_bank_formal, valuation_for

    service = AskService(
        allow_update=False,
        write_ledger=False,
        analysis_date=_date.fromisoformat(analysis_date) if analysis_date else None,
    )
    card = service.ask(query)
    code = str(card.target.code if card.target else "").zfill(6)
    conclusion = str(card.data.get("conclusion") or "undetermined")
    payload = {
        "query": query,
        "instrument_id": f"EQUITY:{code}.{'SH' if code.startswith('6') else 'SZ'}" if code else None,
        "asset_class": "equity",
        "analysis_status": "complete" if conclusion in ("candidate_buy_point", "wait", "avoid") else "insufficient_data",
        "model_id": "bank_valuation_v2",
        "model_version": "2.1.0",
        "model_status": "validated",
        "policy": {
            "policy_id": "bank-valuation-v2.1",
            "status": "approved",
            "approved_at": "2026-11-14",
            "review_date": "2027-05-31",
        },
        "decision": {
            "candidate_buy_point": "candidate",
            "wait": "wait",
            "avoid": "avoid",
        }.get(conclusion, "undetermined"),
        "conclusion": conclusion,
        "conclusion_label": {
            "candidate_buy_point": "候选买点",
            "wait": "等待",
            "avoid": "回避",
        }.get(conclusion, "无法判断"),
        "price": card.data.get("price"),
        "price_date": card.data.get("price_date"),
        "threshold_price": card.data.get("threshold_price"),
        "values": card.data.get("values") or {},
        "implicit_return": card.data.get("implicit_return"),
        "card": card.markdown,
    }
    if conclusion in ("candidate_buy_point", "wait", "avoid"):
        valuation = valuation_for(
            code, price=card.data.get("price"), price_date=card.data.get("price_date")
        )
        if valuation is not None:
            price_date = str(card.data.get("price_date") or "")
            append_bank_formal(
                valuation,
                ledger_path=ledger
                or ledger_paths.write_path("equity-conclusions.jsonl"),
                analysis_date=analysis_date or service.analysis_date.isoformat(),
                trace=policy_trace.bank_trace(
                    policy_path=ROOT / "data" / "policies" / "bank-valuation-v2.1.json",
                    price_date=price_date,
                    quote=policy_trace.live_quote_reference(price_date),
                ),
            )
    text = json.dumps(payload, ensure_ascii=False, indent=2) if as_json else card.markdown
    return text, 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engine.cli",
        description="A股个股 / 境内A股宽基指数 / 普通被动ETF 分析入口（阶段一）",
    )
    parser.add_argument("query", nargs="?", help="标的名称或代码，例如 工商银行 / 沪深300 / 510300")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON（默认输出文本摘要）")
    parser.add_argument(
        "--snapshot", default=None, help="指定固定快照 id，用于重放（例如 snap-1a2b3c4d5e6f7a8b）"
    )
    parser.add_argument("--intent", default="buy_point_check", help="分析目的，默认 buy_point_check")
    parser.add_argument("--request", default=None, help="从 JSON 请求文件读取 query（用于示例与回归）")
    parser.add_argument("--out", default=None, help="把输出写入文件")
    parser.add_argument(
        "--registry", default=None, help="覆盖身份注册表路径（默认 data/registry/instruments.json）"
    )
    parser.add_argument(
        "--snapshot-dir", default=None, help="覆盖快照目录（默认 data/snapshots）"
    )
    parser.add_argument(
        "--evidence-dir", default=None, help="覆盖业务证据目录（默认 data/evidence）"
    )
    parser.add_argument(
        "--assumptions",
        default=None,
        help="研究假设配置文件（个股估值必需，例如 data/assumptions/600519-fcff-base.json）",
    )
    parser.add_argument(
        "--cross-check-assumptions",
        default=None,
        help="交叉检查用的第二份假设配置（可选，用于同一标的的另一条路线）",
    )
    parser.add_argument(
        "--policy", default=None, help="已批准政策文件（默认 data/policies/equity-policy-v1.1.json）"
    )
    parser.add_argument(
        "--policy-snapshot-dir", default=None, help="政策试运行快照目录（默认 data/policy-trial/snapshots）"
    )
    parser.add_argument(
        "--policy-evidence-dir", default=None, help="政策业务证据目录（默认 data/policy-trial/evidence）"
    )
    parser.add_argument(
        "--conclusion-ledger", default=None, help="正式结论账本（默认 data/ledger/equity-conclusions.jsonl）"
    )
    parser.add_argument(
        "--analysis-date",
        default=None,
        help="分析日 YYYY-MM-DD（用于复现；默认当前时间）",
    )
    parser.add_argument(
        "--check-conclusions",
        action="store_true",
        help="只检查结论账本是否被改写/重复，然后退出",
    )
    return parser


def _load_request(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"请求文件必须是 JSON 对象: {path}")
    return data


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from engine.pipeline import (
        DEFAULT_CONCLUSION_LEDGER,
        DEFAULT_POLICY,
        DEFAULT_POLICY_EVIDENCE,
        DEFAULT_POLICY_SNAPSHOTS,
    )

    if args.check_conclusions:
        from engine.policy.conclusion_ledger import ConclusionLedger

        ledger = ConclusionLedger(Path(args.conclusion_ledger or DEFAULT_CONCLUSION_LEDGER))
        problems = ledger.check()
        try:
            count = len(ledger.load())
        except Exception:  # noqa: BLE001 - 账本损坏时 check() 已给出原因
            count = None
        print(
            json.dumps(
                {
                    "ledger": str(ledger.path),
                    "records": count,
                    "problems": problems,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if not problems else 1

    query = args.query
    intent = args.intent
    snapshot = args.snapshot
    assumptions_path = args.assumptions
    cross_check_path = args.cross_check_assumptions
    if args.request:
        request = _load_request(args.request)
        query = request.get("query", query)
        intent = request.get("intent", intent)
        snapshot = request.get("snapshot", snapshot)
        assumptions_path = request.get("assumptions", assumptions_path)
        cross_check_path = request.get("cross_check_assumptions", cross_check_path)

    if query is None:
        print("缺少标的名称或代码。示例：python -m engine.cli '沪深300'", file=sys.stderr)
        return 2

    bank_query = _bank_query(query)
    if bank_query is not None:
        text, code = _bank_cli(
            query,
            as_json=args.json,
            analysis_date=args.analysis_date,
            ledger=Path(args.conclusion_ledger) if args.conclusion_ledger else None,
        )
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(text)
        return code

    config = AnalyzerConfig(
        registry_path=Path(args.registry) if args.registry else AnalyzerConfig.registry_path,
        equity_list_path=AnalyzerConfig().equity_list_path,
        snapshot_dir=Path(args.snapshot_dir) if args.snapshot_dir else AnalyzerConfig.snapshot_dir,
        evidence_dir=Path(args.evidence_dir) if args.evidence_dir else AnalyzerConfig.evidence_dir,
        use_policy_path=True,
        policy_path=Path(args.policy) if args.policy else DEFAULT_POLICY,
        policy_snapshot_dir=(
            Path(args.policy_snapshot_dir) if args.policy_snapshot_dir else DEFAULT_POLICY_SNAPSHOTS
        ),
        policy_evidence_dir=(
            Path(args.policy_evidence_dir) if args.policy_evidence_dir else DEFAULT_POLICY_EVIDENCE
        ),
        conclusion_ledger_path=(
            Path(args.conclusion_ledger)
            if args.conclusion_ledger
            else ledger_paths.write_path("equity-conclusions.jsonl")
        ),
    )
    try:
        analyzer = Analyzer(config)
    except RegistryError as exc:
        print(f"身份注册表不可用: {exc}", file=sys.stderr)
        return 3

    try:
        assumptions = load_assumptions(assumptions_path) if assumptions_path else None
        cross_check = load_assumptions(cross_check_path) if cross_check_path else None
    except ValuationError as exc:
        print(f"假设文件不可用：{exc}", file=sys.stderr)
        return 2

    try:
        now = clock.now()
        if args.analysis_date:
            from datetime import datetime as _datetime

            now = _datetime.fromisoformat(args.analysis_date + "T12:00:00").replace(tzinfo=TZ)
        result = analyzer.analyze(
            query,
            intent=intent,
            snapshot_id=snapshot,
            now=now,
            assumptions=assumptions,
            cross_check_assumptions=cross_check,
        )
        text = export_json(result) if args.json else render_text(result.to_dict())
    except ContractViolation as exc:
        print(f"结果契约校验失败，拒绝输出：\n{exc}", file=sys.stderr)
        return 4
    except Exception as exc:  # pragma: no cover - 兜底，仍不输出假结论
        print(f"引擎失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)

    # 正式结论写入只追加账本；无法判断仅在「改判」时追加（旧记录不改）
    from engine.policy.conclusion import append_evaluation, record_from_result
    from engine.policy.conclusion_ledger import ConclusionLedger, ConclusionLedgerError
    from engine.policy.equity import load_policy

    block = (result.payload or {}).get("policy_conclusion") or {}
    stream = sys.stderr if args.json else sys.stdout
    if result.decision in ("candidate", "wait", "avoid") and block:
        try:
            ledger = ConclusionLedger(config.conclusion_ledger_path)
            trace_block = None
            price_date = str(((block.get("price_date")) or ""))
            if result.snapshot_id:
                try:
                    trace_block = policy_trace.stock_trace(
                        policy_path=config.policy_path,
                        financial=policy_trace.snapshot_reference(
                            result.snapshot_id, price_date=price_date
                        ),
                        quote=None,
                    )
                except policy_trace.TraceError:
                    trace_block = None
            record = record_from_result(
                result,
                recorded_at=clock.now().isoformat(timespec="seconds"),
                trace=trace_block,
            )
            outcome = ledger.append([record])
            if outcome["added"]:
                print(f"已写入结论账本：{outcome['added'][0]}", file=stream)
            else:
                print(f"结论账本已有同内容记录，跳过：{outcome['skipped']}", file=stream)
        except ConclusionLedgerError as exc:
            print(f"结论账本写入失败：{exc}", file=sys.stderr)
            return 3
    elif result.decision == "undetermined" and block:
        record = {
            "instrument_id": str(result.instrument_id),
            "code": str(((result.instrument or {}).get("code")) or ""),
            "status": block.get("status"),
            "status_reason": block.get("status_reason"),
            "snapshot_id": block.get("snapshot_id"),
            "price": None,
            "price_as_of": block.get("price_date"),
            "values": {},
            "grade": block.get("grade") or {},
            "trial_conclusion": {"conclusion": "undetermined", "thresholds": None},
            "route": {
                "family": (result.model_plan or {}).get("family"),
                "status": (
                    "family_not_implemented"
                    if result.analysis_status == "unsupported"
                    else None
                ),
                "applicable_model_category": (result.model_plan or {}).get(
                    "applicable_model_category"
                ),
            },
            "reason_codes": list(result.reason_codes or []),
        }
        try:
            policy = load_policy(config.policy_path)
            outcome = append_evaluation(
                ConclusionLedger(config.conclusion_ledger_path),
                record,
                policy=policy,
                analysis_date=str(block.get("analysis_date")),
            )
            if outcome.get("type") == "reversal" and outcome.get("added"):
                print(f"已写入改判记录：{outcome['record_id']}", file=stream)
        except ConclusionLedgerError as exc:
            print(f"结论账本改判写入失败：{exc}", file=sys.stderr)
            return 3

    if result.analysis_status == "engine_error":
        return 3
    if result.analysis_status == "needs_clarification":
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
