"""每日更新定时任务（macOS launchd）的自检。

只读 `~/Library/LaunchAgents/<label>.plist`，核对其中记录的项目路径是否仍是当前项目；
不安装、不卸载、不改 plist。安装/卸载仍由 `scripts/` 下脚本人工执行。
"""

from __future__ import annotations

import os
import plistlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
LABEL = "com.investment-system.forward-update"
FIX_COMMAND = 'cd "{project}" && scripts/install-daily-update.sh'


def _absolute(path: str | Path) -> Path:
    """只做路径规范化，不跟随符号链接（.venv/bin/python 本身是符号链接）。"""
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def plist_path(home: Path | None = None) -> Path:
    return Path(home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def check_launchd(
    project: Path | None = None,
    *,
    plist: Path | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    """核对 plist 是否存在、其中路径是否指向当前项目。

    返回 `status`（ok/missing/invalid/mismatch）、`problems`（逐条中文说明）与 `fix`（修复命令）。
    """
    project = Path(project or ROOT).resolve()
    path = Path(plist) if plist is not None else plist_path(home)
    fix = FIX_COMMAND.format(project=project)
    result: dict[str, Any] = {
        "status": "ok",
        "plist": str(path),
        "project": str(project),
        "fix": fix,
        "problems": [],
    }
    if not path.exists():
        result["status"] = "missing"
        result["problems"].append(f"定时任务 plist 不存在：{path}")
        result["problems"].append(f"修复：{fix}")
        return result
    try:
        payload = plistlib.loads(path.read_bytes())
    except Exception as exc:  # noqa: BLE001 - 损坏的 plist 如实报错
        result["status"] = "invalid"
        result["problems"].append(f"定时任务 plist 无法解析：{path}（{type(exc).__name__}: {exc}）")
        result["problems"].append(f"修复：{fix}")
        return result

    problems: list[str] = []
    if str(payload.get("Label") or "") != LABEL:
        problems.append(f"plist Label 不是 {LABEL}：{payload.get('Label')!r}")

    arguments = [str(item) for item in payload.get("ProgramArguments") or []]
    expected_script = project / "tools" / "update.py"
    expected_python = project / ".venv" / "bin" / "python"
    script_argument = arguments[1] if len(arguments) > 1 else ""
    if not script_argument:
        problems.append("plist 的 ProgramArguments 缺少 tools/update.py")
    elif _absolute(script_argument) != _absolute(expected_script):
        problems.append(
            f"plist 的更新脚本指向旧路径：{script_argument}；当前项目应为 {expected_script}"
        )
    python_argument = arguments[0] if arguments else ""
    if python_argument and _absolute(python_argument) != _absolute(expected_python):
        problems.append(
            f"plist 的解释器指向旧路径：{python_argument}；当前项目应为 {expected_python}"
        )

    working = str(payload.get("WorkingDirectory") or "")
    if not working:
        problems.append("plist 缺少 WorkingDirectory")
    elif _absolute(working) != _absolute(project):
        problems.append(f"plist 的 WorkingDirectory 指向旧路径：{working}；当前项目应为 {project}")

    for key in ("StandardOutPath", "StandardErrorPath"):
        value = str(payload.get(key) or "")
        if value and not Path(value).resolve().is_relative_to(project):
            problems.append(f"plist 的 {key} 不在当前项目内：{value}")

    if problems:
        result["status"] = "mismatch"
        result["problems"] = problems + [f"修复：{fix}"]
    return result


__all__ = ["FIX_COMMAND", "LABEL", "check_launchd", "plist_path"]
