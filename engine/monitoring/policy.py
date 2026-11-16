"""监测政策的加载与校验。

政策文件是不可手改的已批准文件：本模块只读，不做任何默认值填充。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_POLICY_PATH = ROOT / "data" / "policies" / "pool-monitoring-v1.json"
SCHEMA_VERSION = "pool-monitoring-policy/1.0"


class MonitoringPolicyError(RuntimeError):
    """政策文件缺失、状态非法或字段不完整。"""


@dataclass(frozen=True)
class MonitoringPolicy:
    path: Path
    data: dict[str, Any]
    digest: str

    @property
    def policy_id(self) -> str:
        return str(self.data["policy_id"])

    @property
    def status(self) -> str:
        return str(self.data["status"])

    @property
    def approved(self) -> bool:
        return bool(self.data.get("approved"))

    def node(self, path: str) -> Any:
        current: Any = self.data
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                raise MonitoringPolicyError(f"政策缺少字段：{path}")
            current = current[part]
        return current

    @property
    def red_lines(self) -> dict[str, Any]:
        return self.node("red_lines")

    def red_line(self, red_line_id: str) -> dict[str, Any]:
        for item in self.red_lines.get("events") or []:
            if item.get("id") == red_line_id:
                return item
        raise MonitoringPolicyError(f"政策没有红线：{red_line_id}")


def load_policy(path: Path | None = None) -> MonitoringPolicy:
    node = Path(path or DEFAULT_POLICY_PATH)
    if not node.exists():
        raise MonitoringPolicyError(f"监测政策不存在：{node}")
    raw = node.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise MonitoringPolicyError(f"监测政策 schema 非法：{data.get('schema_version')!r}")
    if data.get("policy_id") != "pool-monitoring-v1":
        raise MonitoringPolicyError(f"监测政策 id 非法：{data.get('policy_id')!r}")
    if data.get("status") != "approved" or data.get("approved") is not True:
        raise MonitoringPolicyError("监测政策未批准，拒绝执行")
    approval = data.get("approval") or {}
    if not approval.get("approved_at") or not approval.get("decision"):
        raise MonitoringPolicyError("监测政策缺少批准记录")
    for key in ("red_lines", "actions", "schedule", "alerts"):
        if not isinstance(data.get(key), dict) or not data[key]:
            raise MonitoringPolicyError(f"监测政策缺少字段：{key}")
    if not (data.get("red_lines") or {}).get("events"):
        raise MonitoringPolicyError("监测政策缺少红线事件清单")
    return MonitoringPolicy(path=node, data=data, digest=hashlib.sha256(raw).hexdigest())


__all__ = [
    "DEFAULT_POLICY_PATH",
    "MonitoringPolicy",
    "MonitoringPolicyError",
    "SCHEMA_VERSION",
    "load_policy",
]
