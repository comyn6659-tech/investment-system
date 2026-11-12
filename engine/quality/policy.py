"""好公司池规则（`quality-pool-v1-draft`）的加载、校验与参数访问。

本包与个股政策、指数/ETF 研究相互独立：它只服务好公司池的试运行报告，
不进入 CLI 的个股结论路径，也不改变任何既有账本。

设计约束（施工单 G2）：
- 所有阈值、观察年数、口径说明集中在版本化政策 JSON；
- 代码里不另设业务数字；判断只通过 `policy.parameter(...)` 取数；
- 草案状态只能是 `draft_unapproved`，本模块没有把政策升级为 approved 的路径。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator, Mapping

POLICY_SCHEMA_VERSION = "quality-pool-policy/1.0"
SUPPORTED_SCHEMA_VERSIONS = ("quality-pool-policy/1.0",)
POLICY_ID = "quality-pool-v1-draft"
SUPPORTED_POLICY_IDS = ("quality-pool-v1-draft", "quality-pool-v1.1-draft")
POLICY_STATUS_DRAFT = "draft_unapproved"
SUPPORTED_OPERATORS = (">=", ">", "<=", "<", "==")

_VALUE_LABELS = {
    "roe_average": "窗口内 ROE 平均值",
    "roe_above_share": "ROE 达到 10% 的年份占比",
    "roe_recent_average": "最近 3 个财年 ROE 平均值",
    "cash_conversion": "最近 5 年现金转换率",
    "fcf_total": "最近 5 年自由现金流合计",
    "loss_years": "窗口内亏损年数",
    "net_debt_to_ebitda": "净有息负债 / EBITDA",
    "dividend_payout": "最近 5 年现金分红率",
    "pledge_ratio": "控股股东最新质押比例",
    "audit_nonstandard_years": "最近 5 年非标准审计意见年数",
    "penalty_count": "最近 5 年信息披露类处罚/立案/公开谴责件数",
}


class PolicyError(RuntimeError):
    """政策文件结构或取值不合法。"""


@dataclass(frozen=True)
class QualityPolicy:
    path: Path
    raw: Mapping[str, Any]
    digest: str

    # --- 身份 ---------------------------------------------------------------
    @property
    def policy_id(self) -> str:
        return str(self.raw["policy_id"])

    @property
    def schema_version(self) -> str:
        return str(self.raw["schema_version"])

    @property
    def status(self) -> str:
        return str(self.raw["status"])

    @property
    def approved(self) -> bool:
        return bool(self.raw.get("approved"))

    @property
    def version_date(self) -> str:
        return str(self.raw["version_date"])

    @property
    def label(self) -> str:
        return str(self.raw["label"])

    # --- 参数与条件 ---------------------------------------------------------
    def parameter(self, name: str) -> dict[str, Any]:
        node = (self.raw.get("parameters") or {}).get(name)
        if not isinstance(node, dict):
            raise PolicyError(f"政策缺少参数：{name}")
        return node

    def value(self, name: str) -> float | int:
        node = self.parameter(name)
        if "value" not in node:
            raise PolicyError(f"参数 {name} 没有 value")
        return node["value"]

    def parameter_values(self) -> dict[str, float | int]:
        return {
            name: node["value"]
            for name, node in (self.raw.get("parameters") or {}).items()
            if "value" in node
        }

    def iter_conditions(self) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
        for group in self.raw.get("groups") or []:
            for condition in group.get("conditions") or []:
                yield group, condition

    def condition(self, condition_id: str) -> dict[str, Any]:
        for _, condition in self.iter_conditions():
            if condition.get("id") == condition_id:
                return condition
        raise PolicyError(f"政策缺少条件：{condition_id}")

    def conditions(self) -> list[dict[str, Any]]:
        return [condition for _, condition in self.iter_conditions()]

    def untested_conditions(self) -> list[dict[str, Any]]:
        return [
            condition
            for condition in self.conditions()
            if condition.get("evaluation") == "untested"
        ]

    def condition_label(self, condition: Mapping[str, Any]) -> str:
        return str(condition.get("label") or _VALUE_LABELS.get(str(condition.get("metric")), condition.get("id")))

    def with_overrides(self, overrides: Mapping[str, float | int]) -> "QualityPolicy":
        """返回一个只替换参数 value 的副本；basis/source 原样保留，用于敏感性报告。"""
        unknown = set(overrides) - set(self.raw.get("parameters") or {})
        if unknown:
            raise PolicyError(f"敏感性覆盖了不存在的参数：{sorted(unknown)}")
        raw = json.loads(json.dumps(self.raw, ensure_ascii=False))
        for name, value in overrides.items():
            raw["parameters"][name]["value"] = value
            raw["parameters"][name]["sensitivity_override"] = True
        digest = _digest(raw)
        return QualityPolicy(path=self.path, raw=raw, digest=digest)


def _digest(raw: Mapping[str, Any]) -> str:
    payload = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PolicyError(message)


def validate_policy(raw: Mapping[str, Any]) -> None:
    _require(raw.get("schema_version") in SUPPORTED_SCHEMA_VERSIONS, "政策 schema_version 不受支持")
    _require(raw.get("policy_id") in SUPPORTED_POLICY_IDS, f"政策 policy_id 必须是 {SUPPORTED_POLICY_IDS} 之一")
    _require(raw.get("status") == POLICY_STATUS_DRAFT, "好公司池政策本版只能是 draft_unapproved")
    _require(raw.get("approved") is False, "草案不得自我批准（approved 必须为 false）")
    _require(isinstance(raw.get("parameters"), dict) and raw["parameters"], "政策缺少 parameters")
    _require(isinstance(raw.get("groups"), list) and raw["groups"], "政策缺少 groups")

    for name, node in raw["parameters"].items():
        if not isinstance(node, dict):
            raise PolicyError(f"参数 {name} 必须是对象")
        if "value" in node:
            _require(isinstance(node["value"], (int, float)) and not isinstance(node["value"], bool), f"参数 {name} 的 value 必须是数字")
        for field in ("basis", "source"):
            _require(isinstance(node.get(field), str) and node[field].strip(), f"参数 {name} 缺少 {field}")

    seen: set[str] = set()
    for group, condition in ((g, c) for g in raw["groups"] for c in (g.get("conditions") or [])):
        cid = str(condition.get("id") or "")
        _require(cid, "条件缺少 id")
        _require(cid not in seen, f"条件 id 重复：{cid}")
        seen.add(cid)
        _require(isinstance(condition.get("label"), str) and condition["label"], f"条件 {cid} 缺少 label")
        if condition.get("evaluation") == "untested":
            _require(bool(condition.get("untested_reason")), f"未检验条件 {cid} 必须写明理由")
            continue
        _require(condition.get("metric") in _VALUE_LABELS, f"条件 {cid} 的 metric 不受支持")
        _require(condition.get("operator") in SUPPORTED_OPERATORS, f"条件 {cid} 的 operator 不受支持")
        parameter = condition.get("parameter")
        _require(parameter in raw["parameters"], f"条件 {cid} 引用了不存在的参数 {parameter}")
        _require("value" in raw["parameters"][parameter], f"参数 {parameter} 没有 value")
        _require(condition.get("distance") in ("value_minus_threshold", "threshold_minus_value"), f"条件 {cid} 的 distance 不受支持")
    # v1.1 起治理条件已补数据，不再强制保留未检验条件。


def load_policy(path: Path) -> QualityPolicy:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_policy(raw)
    return QualityPolicy(path=Path(path), raw=raw, digest=_digest(raw))
