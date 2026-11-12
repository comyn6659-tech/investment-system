"""个股政策草案（`equity-policy-v1.1-draft`，保留 v1 可读）的加载、校验与参数访问。

设计约束（施工单 A.1、验收 L1）：
- 所有阈值、比例、来源与理由集中在版本化政策 JSON 中；
- 代码里不得散落政策数字：本模块只做结构校验与路径访问，
  任何业务判断都通过 `policy.value(...)` / `policy.node(...)` 取数；
- 政策文件的状态只能是 draft_unapproved；本模块没有任何路径把政策升级为 approved。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from engine.data.snapshot import stable_digest

POLICY_SCHEMA_VERSION = "equity-policy/1.1"
SUPPORTED_SCHEMA_VERSIONS = ("equity-policy/1", "equity-policy/1.1")
POLICY_ID = "equity-policy-v1.1-draft"
SUPPORTED_POLICY_IDS = ("equity-policy-v1-draft", "equity-policy-v1.1-draft")
POLICY_STATUS_DRAFT = "draft_unapproved"

# 参数节点的取值形式：单值 / 规则 / 列表 / 网格 / 结构化字段组
_VALUE_KEYS = (
    "value",
    "rule",
    "values",
    "grid",
    "families",
    "required_evidence",
    "interest_bearing_debt",
    "rule",
    "growth_adjustment",
    "equity",
    "capex_intensity_max",
    "min_available_fiscal_years",
    "ocf_to_profit_min",
    "lookback_fiscal_years",
    "terminal_growth",
    "rating",
)
_BASIS_KEYS = ("basis", "source")
# 只用于说明、不算取值的字段
_META_KEYS = frozenset({"basis", "source", "note", "notes", "kind", "implementation_note", "usage", "display"})


def _has_payload(node: dict[str, Any]) -> bool:
    return any(key not in _META_KEYS for key in node)


class PolicyError(RuntimeError):
    """政策文件不可用（缺失、结构非法、参数无依据）。"""


@dataclass(frozen=True)
class EquityPolicy:
    data: dict[str, Any]
    path: str

    # --- 身份 -----------------------------------------------------------
    @property
    def policy_id(self) -> str:
        return str(self.data["policy_id"])

    @property
    def policy_digest(self) -> str:
        return "pol-" + stable_digest(self.data)

    @property
    def status(self) -> str:
        return str(self.data["status"])

    @property
    def approved(self) -> bool:
        return bool(self.data.get("approved", False))

    @property
    def version_date(self) -> str:
        return str(self.data.get("version_date", ""))

    # --- 访问 -----------------------------------------------------------
    def node(self, dotted: str) -> dict[str, Any]:
        """按点分路径取参数节点（路径相对 `parameters`，只能是对象节点）。"""
        current: Any = self.data.get("parameters") or {}
        for part in dotted.split("."):
            if not isinstance(current, Mapping) or part not in current:
                raise PolicyError(f"政策 {self.policy_id} 缺少参数 {dotted!r}（断在 {part!r}）")
            current = current[part]
        if not isinstance(current, dict):
            raise PolicyError(f"政策参数 {dotted!r} 不是对象节点")
        return current

    def value(self, dotted: str) -> float:
        """取标量参数值；节点必须是 {"value": number, ...}。"""
        node = self.node(dotted)
        if "value" not in node:
            raise PolicyError(f"政策参数 {dotted!r} 没有 value")
        raw = node["value"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise PolicyError(f"政策参数 {dotted!r} 的 value 不是数值：{raw!r}")
        return float(raw)

    def raw(self, dotted: str) -> Any:
        node = self.node(dotted)
        for key in _VALUE_KEYS:
            if key in node:
                return node[key]
        raise PolicyError(f"政策参数 {dotted!r} 没有可用的取值字段（{_VALUE_KEYS}）")

    def basis(self, dotted: str) -> str:
        node = self.node(dotted)
        parts = [str(node[key]) for key in _BASIS_KEYS if node.get(key)]
        return "；".join(parts)

    # --- 校验 -----------------------------------------------------------
    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.data.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
            problems.append(
                f"schema_version={self.data.get('schema_version')!r}，"
                f"期望 {SUPPORTED_SCHEMA_VERSIONS!r}"
            )
        if self.data.get("policy_id") not in SUPPORTED_POLICY_IDS:
            problems.append(
                f"policy_id={self.data.get('policy_id')!r}，期望 {SUPPORTED_POLICY_IDS!r}"
            )
        if self.data.get("status") != POLICY_STATUS_DRAFT:
            problems.append(
                f"status={self.data.get('status')!r}：本版只允许 {POLICY_STATUS_DRAFT!r}"
            )
        if self.data.get("approved") is not False:
            problems.append("approved 必须为 false：草案不得自我批准")

        required = (
            "discount_rate.risk_free",
            "discount_rate.equity_risk_premium",
            "discount_rate.company_specific_premium",
            "discount_rate.cost_of_equity",
            "discount_rate.cost_of_debt",
            "forecast.horizon_years",
            "forecast.payout_path",
            "terminal.growth_base",
            "terminal.growth_cap",
            "terminal.consistency",
            "terminal.reinvestment",
            "scenarios.base",
            "scenarios.pessimistic",
            "scenarios.optimistic",
            "quality.selection_thresholds",
            "quality.cash",
            "quality.minority_interest",
            "quality.financial_subsidiary",
            "grade.A",
            "grade.B",
            "grade.C",
            "conclusion.candidate_buy_point",
            "conclusion.avoid",
            "conclusion.wait",
            "conclusion.undetermined",
            "sensitivity.discount_rate_delta",
            "sensitivity.terminal_growth_delta",
        )
        if self.data.get("policy_id") == "equity-policy-v1.1-draft":
            required = required + (
                "forecast.growth_cap",
                "forecast.steady_growth",
                "forecast.interpolation",
                "terminal.continuity_guardrail",
                "grade.dividend_coverage",
                "grade.missing_data",
            )
        for path in required:
            try:
                self.node(path)
            except PolicyError as exc:
                problems.append(str(exc))

        # 每个参数节点必须带依据（来源或理由）
        for path, node in _walk_parameters(self.data.get("parameters") or {}, "parameters"):
            if not any(node.get(key) for key in _BASIS_KEYS):
                problems.append(f"参数 {path} 缺少 basis/source：每个参数都要写来源或理由")
            if not _has_payload(node):
                problems.append(f"参数 {path} 只有依据说明、没有任何可执行取值")

        # 结构性合理性：安全边际、阈值区间
        try:
            for grade in ("A", "B"):
                margin = self.value(f"grade.{grade}.margin_of_safety")
                if not 0.0 < margin < 1.0:
                    problems.append(f"grade.{grade}.margin_of_safety={margin} 不在 (0,1) 内")
            if self.value("forecast.horizon_years") != int(self.value("forecast.horizon_years")):
                problems.append("forecast.horizon_years 必须是整数")
            if self.data.get("policy_id") == "equity-policy-v1.1-draft" and not 0.0 < self.value("forecast.growth_cap") < 1.0:
                problems.append("forecast.growth_cap 不在 (0,1) 内")
            if not 0.0 <= self.value("terminal.growth_base") < 1.0:
                problems.append("terminal.growth_base 不在 [0,1) 内")
            if self.value("terminal.growth_cap") < self.value("terminal.growth_base"):
                problems.append("terminal.growth_cap 小于 growth_base：上限设置不成立")
        except PolicyError:
            pass
        return problems


def _walk_parameters(node: Any, path: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """遍历参数树：含 basis/source 的叶子节点视为参数。"""
    if isinstance(node, dict):
        if any(key in node for key in _BASIS_KEYS):
            yield path, node
            return
        for key, child in node.items():
            if isinstance(child, dict):
                yield from _walk_parameters(child, f"{path}.{key}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from _walk_parameters(child, f"{path}[{index}]")


def load_policy(path: str | Path) -> EquityPolicy:
    path = Path(path)
    if not path.exists():
        raise PolicyError(f"政策文件不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PolicyError(f"政策文件不是合法 JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"政策文件必须是 JSON 对象: {path}")
    policy = EquityPolicy(data=data, path=str(path))
    problems = policy.validate()
    if problems:
        raise PolicyError("政策文件校验失败：" + "；".join(problems))
    return policy


__all__ = [
    "EquityPolicy",
    "POLICY_ID",
    "POLICY_SCHEMA_VERSION",
    "POLICY_STATUS_DRAFT",
    "SUPPORTED_POLICY_IDS",
    "SUPPORTED_SCHEMA_VERSIONS",
    "PolicyError",
    "load_policy",
]
