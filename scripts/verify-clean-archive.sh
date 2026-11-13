#!/bin/sh
# 第十一批收尾 T1：把已提交版本 git archive 到无缓存的新目录，运行全部测试与端到端验收。
# 用法：scripts/verify-clean-archive.sh
# 说明：使用项目现有 .venv 的解释器；归档目录不含 .venv、data/cache 与被忽略文件。
set -eu

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$PROJECT/.venv/bin/python"
DEST="$(mktemp -d "${TMPDIR:-/tmp}/investment-system-archive.XXXXXX")"

if [ ! -x "$PYTHON" ]; then
  echo "找不到项目解释器：$PYTHON" >&2
  exit 1
fi

echo "归档到：$DEST"
git -C "$PROJECT" archive --format=tar HEAD | tar -x -C "$DEST"
cd "$DEST"

PYTHONDONTWRITEBYTECODE=1 "$PYTHON" -m pytest -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" tools/run_ask_acceptance.py

echo "clean-archive：全部测试与端到端验收通过（${DEST}）"
