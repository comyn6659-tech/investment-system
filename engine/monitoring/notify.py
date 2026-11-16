"""macOS 变动通知（osascript）。通知失败不影响任务。"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Sequence


@dataclass(frozen=True)
class NotifyResult:
    status: str
    command: tuple[str, ...] = ()
    error: str = ""


def _escape(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def build_command(title: str, message: str) -> list[str]:
    script = (
        f'display notification "{_escape(message)}" '
        f'with title "{_escape(title)}"'
    )
    return ["osascript", "-e", script]


def notify(
    title: str,
    message: str,
    *,
    runner: Callable[..., object] | None = None,
    platform: str | None = None,
) -> NotifyResult:
    """发送一条 macOS 通知；非 macOS 或失败时返回状态，不抛异常。"""
    if platform is None:
        platform = sys.platform
    command = build_command(title, message)
    if not str(platform).startswith("darwin"):
        return NotifyResult(status="skipped", command=tuple(command), error="非 macOS")
    call = runner or subprocess.run
    try:
        call(command, check=False, capture_output=True, timeout=10)
    except Exception as exc:  # noqa: BLE001 - 通知失败不影响任务
        return NotifyResult(status="failed", command=tuple(command), error=f"{type(exc).__name__}: {exc}")
    return NotifyResult(status="sent", command=tuple(command))


def notify_changes(entries: Sequence[object], *, runner: Callable[..., object] | None = None) -> list[NotifyResult]:
    """按变动记录发送通知；只发出池与候选买点新增/消失。"""
    results = []
    for entry in entries:
        if isinstance(entry, dict):
            kind = str(entry.get("type") or "")
            detail = str(entry.get("detail") or "")
            code = str(entry.get("code") or "")
            name = str(entry.get("name") or "")
        else:  # pragma: no cover - 防御性
            continue
        if kind == "出池":
            results.append(notify("池成员出池", f"{code} {name}：{detail}", runner=runner))
        elif kind == "结论变化" and ("候选买点" in detail):
            results.append(notify("结论变化", f"{code} {name}：{detail}", runner=runner))
    return results


__all__ = ["NotifyResult", "build_command", "notify", "notify_changes"]
