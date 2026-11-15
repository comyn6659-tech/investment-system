"""账本路径解析：测试用环境变量把「写入」重定向到临时目录。

- 读路径保持仓库内已提交账本（历史/次数等只读查询的行为不变）；
- 写路径默认与读路径相同，但设置了 `INVESTMENT_SYSTEM_LEDGER_DIR` 时写到该目录。
  该变量仅供测试/沙箱使用，生产环境不设置时行为与原来完全一致。
- 显式传入的 ledger 路径（例如 AskService(ledger_path=...)）优先于环境变量。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER_DIR = ROOT / "data" / "ledger"
LEDGER_DIR_ENV = "INVESTMENT_SYSTEM_LEDGER_DIR"


def ledger_dir() -> Path:
    raw = os.environ.get(LEDGER_DIR_ENV, "").strip()
    return Path(raw) if raw else DEFAULT_LEDGER_DIR


def read_path(name: str, *, explicit: Path | None = None) -> Path:
    """账本读取路径：显式路径优先，其次仓库内默认位置（不受测试环境变量影响）。"""
    return Path(explicit) if explicit is not None else DEFAULT_LEDGER_DIR / name


def write_path(name: str, *, explicit: Path | None = None) -> Path:
    """账本写入路径：显式路径优先，其次测试环境变量目录，最后仓库默认位置。"""
    if explicit is not None:
        return Path(explicit)
    return ledger_dir() / name


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ledger_hashes(directory: Path | None = None) -> dict[str, str]:
    """目录下所有文件的 sha256；用于测试前后比对真实账本未被写入。"""
    base = Path(directory) if directory is not None else DEFAULT_LEDGER_DIR
    if not base.exists():
        return {}
    return {
        path.name: file_sha256(path)
        for path in sorted(base.iterdir())
        if path.is_file()
    }


__all__ = [
    "DEFAULT_LEDGER_DIR",
    "LEDGER_DIR_ENV",
    "ROOT",
    "file_sha256",
    "ledger_dir",
    "ledger_hashes",
    "read_path",
    "write_path",
]
