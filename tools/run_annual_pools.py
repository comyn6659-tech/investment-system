#!/usr/bin/env python
"""年度重跑：按顺序重建三池快照 → 以已批准规则重跑 → 追加三本账本。

用法：
    .venv/bin/python tools/run_annual_pools.py --dry-run   # 只打印步骤
    .venv/bin/python tools/run_annual_pools.py             # 实际执行（会访问数据源）

本命令不修改规则阈值；规则文件固定为 approved 版本。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STEPS: list[tuple[str, list[str]]] = [
    ("重建好公司池快照", ["tools/build_quality_pool_data.py"]),
    ("好公司池按已批准规则重跑并追加账本", ["tools/run_quality_pool.py"]),
    ("重建稳定收息池快照", ["tools/build_income_pool_data.py", "--build-tables", "--build-snapshot"]),
    ("稳定收息池按已批准规则重跑并追加账本", ["tools/run_income_pool.py"]),
    ("重建银行池快照", ["tools/build_bank_pool_data.py", "--build-tables", "--build-snapshot"]),
    ("银行池按已批准规则重跑并追加账本", ["tools/run_bank_pool.py"]),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只打印步骤，不执行")
    args = parser.parse_args(argv)
    for index, (label, command) in enumerate(STEPS, start=1):
        print(f"[{index}/{len(STEPS)}] {label}：{' '.join([sys.executable, *command])}", flush=True)
        if args.dry_run:
            continue
        result = subprocess.run([sys.executable, *command], cwd=ROOT)
        if result.returncode != 0:
            print(f"步骤失败（退出码 {result.returncode}），停止：{label}", file=sys.stderr)
            return result.returncode
    print("年度重跑完成。" if not args.dry_run else "dry-run：未执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
