"""银行估值输入文件的版本指针。

`data/bank-pool/valuation-v2/manifest.json` 的 `current_inputs` 指向当前输入表；
旧输入表保留，历史账本记录仍能按原路径与哈希复算。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BANK_VALUATION_DIR = ROOT / "data" / "bank-pool" / "valuation-v2"
LEGACY_INPUTS = "inputs.csv.gz"
LEGACY_HISTORY = "history.csv.gz"


def directory(path: Path | None = None) -> Path:
    return Path(path) if path is not None else BANK_VALUATION_DIR


def _manifest(base: Path) -> dict:
    path = base / "manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def current_inputs(base: Path | None = None) -> Path:
    base = directory(base)
    manifest = _manifest(base)
    name = str(manifest.get("current_inputs") or LEGACY_INPUTS)
    return base / name


def current_history(base: Path | None = None) -> Path:
    base = directory(base)
    manifest = _manifest(base)
    name = str(manifest.get("current_history") or LEGACY_HISTORY)
    return base / name


__all__ = [
    "BANK_VALUATION_DIR",
    "LEGACY_HISTORY",
    "LEGACY_INPUTS",
    "current_history",
    "current_inputs",
    "directory",
]
