"""`ask` 命令：中文问题 → 固定结构 Markdown 卡片。

用法：
    python -m engine.ask "茅台怎么样"
    python -m engine.ask --detail "招商银行怎么样"
    python -m engine.ask --no-update "数据是哪天的"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from engine.ask.service import AskService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ask", description="投资分析系统的中文对话入口")
    parser.add_argument("question", help="中文问题，例如「茅台怎么样」「好公司池有哪些」")
    parser.add_argument("--detail", action="store_true", help="展开全部细节（默认卡片不超过 25 行）")
    parser.add_argument("--json", action="store_true", help="输出结构化 JSON 而不是 Markdown")
    parser.add_argument("--no-update", action="store_true", help="不尝试自动 update（离线/测试）")
    parser.add_argument("--analysis-date", default=None, help="分析日 YYYY-MM-DD（复现用）")
    args = parser.parse_args(argv)

    analysis_date = date.fromisoformat(args.analysis_date) if args.analysis_date else None
    service = AskService(allow_update=not args.no_update, analysis_date=analysis_date)
    try:
        result = service.ask(args.question, detail=args.detail)
    except Exception as exc:  # noqa: BLE001 - 失败也不输出假结论
        print(f"查询失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        print(result.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
