#!/usr/bin/env python
"""重新生成 examples/outputs/ 下的示例输出。

每个 `examples/requests/*.json` 对应一个输出文件；脚本直接调用 `engine.cli`，
所以示例与真实入口完全同源，不存在「示例是手写」的可能。

用法：
    .venv/bin/python tools/render_examples.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUESTS = ROOT / "examples" / "requests"
OUTPUTS = ROOT / "examples" / "outputs"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重新生成示例输出")
    parser.add_argument("--only", default=None, help="只渲染指定请求文件名（不含扩展名）")
    args = parser.parse_args(argv)

    requests = sorted(REQUESTS.glob("*.json"))
    if args.only:
        requests = [p for p in requests if p.stem == args.only]
    if not requests:
        print("没有找到请求文件", file=sys.stderr)
        return 1

    OUTPUTS.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for request in requests:
        target = OUTPUTS / f"{request.stem}.json"
        proc = subprocess.run(
            [sys.executable, "-m", "engine.cli", "--json", "--request", str(request)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        # 0=正常结果，2=需要澄清，3=引擎错误：三者都是「已产出并通过契约校验」的合法出口
        if proc.returncode not in (0, 2, 3):
            failures.append(f"{request.name}: exit={proc.returncode} {proc.stderr.strip()[:200]}")
            continue
        try:
            json.loads(proc.stdout)
        except json.JSONDecodeError:
            failures.append(f"{request.name}: 输出不是合法 JSON")
            continue
        target.write_text(proc.stdout.rstrip("\n") + "\n", encoding="utf-8")
        print(f"{request.name} -> {target.relative_to(ROOT)}", file=sys.stderr)

    if failures:
        print("以下示例生成失败：", file=sys.stderr)
        for item in failures:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(f"已生成 {len(requests)} 个示例输出", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
