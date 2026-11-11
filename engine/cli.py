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
from pathlib import Path

from engine.assumptions.schema import load_assumptions
from engine.contracts.validation import ContractViolation
from engine.identity.catalog import RegistryError
from engine.valuation.errors import ValuationError
from engine.pipeline import Analyzer, AnalyzerConfig
from engine.reporting.json_exporter import export_json
from engine.reporting.text_summary import render_text


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
    return parser


def _load_request(path: str) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"请求文件必须是 JSON 对象: {path}")
    return data


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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

    config = AnalyzerConfig(
        registry_path=Path(args.registry) if args.registry else AnalyzerConfig.registry_path,
        equity_list_path=AnalyzerConfig().equity_list_path,
        snapshot_dir=Path(args.snapshot_dir) if args.snapshot_dir else AnalyzerConfig.snapshot_dir,
        evidence_dir=Path(args.evidence_dir) if args.evidence_dir else AnalyzerConfig.evidence_dir,
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
        result = analyzer.analyze(
            query,
            intent=intent,
            snapshot_id=snapshot,
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

    if result.analysis_status == "engine_error":
        return 3
    if result.analysis_status == "needs_clarification":
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
