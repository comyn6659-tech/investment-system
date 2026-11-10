"""结果契约中的受限枚举。

设计书 §4 要求把「事实 / 模型 / 投资结论」分开，因此这里的枚举是互不替代的
四个维度：分析完成情况、模型状态、价格位置、决策。
"""

from __future__ import annotations

from enum import Enum


class _StrEnum(str, Enum):
    """str 子类枚举，便于 JSON 序列化与字典比较。"""

    def __str__(self) -> str:  # pragma: no cover - 仅影响展示
        return self.value


class AnalysisStatus(_StrEnum):
    """分析完成情况（不是投资结论）。"""

    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT_DATA = "insufficient_data"
    UNSUPPORTED = "unsupported"
    NEEDS_CLARIFICATION = "needs_clarification"
    ENGINE_ERROR = "engine_error"


class ModelStatus(_StrEnum):
    """模型或策略是否通过验收。"""

    RESEARCH = "research"
    VALIDATED = "validated"
    DEPRECATED = "deprecated"


class ValuationState(_StrEnum):
    """模型口径下的价格位置。"""

    ATTRACTIVE = "attractive"
    NEUTRAL = "neutral"
    EXPENSIVE = "expensive"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class Decision(_StrEnum):
    """当前是否构成该分析框架的买点候选。"""

    CANDIDATE = "candidate"
    WAIT = "wait"
    AVOID = "avoid"
    UNDETERMINED = "undetermined"


class AssetClass(_StrEnum):
    EQUITY = "equity"
    INDEX = "index"
    ETF = "etf"
    UNKNOWN = "unknown"


class ScopeStatus(_StrEnum):
    """标的是否落在第一版模型范围内。"""

    IN_SCOPE = "in_scope"
    OUT_OF_SCOPE = "out_of_scope"
    # 已识别但资料不足以判断范围（例如只有行情代码、未核实跟踪指数）
    UNVERIFIED = "unverified"


class DataStatus(_StrEnum):
    """单个数据点的状态。

    区分「已知为零」「缺失」「不适用」「过期」「冲突」，
    禁止把任何一种悄悄当成 0 或当成正常值。
    """

    KNOWN = "known"
    KNOWN_ZERO = "known_zero"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"
    STALE = "stale"
    CONFLICT = "conflict"


class IndexClass(_StrEnum):
    """指数的业务分类（用于范围判定，不用于选择模型）。"""

    BROAD_BASED = "broad_based"
    STRATEGY = "strategy"
    INDUSTRY_THEME = "industry_theme"
    OVERSEAS = "overseas"
    OTHER = "other"


class EtfStrategy(_StrEnum):
    """ETF 的产品性质。"""

    PASSIVE_PLAIN = "passive_plain"
    PASSIVE_ENHANCED = "passive_enhanced"
    ACTIVE = "active"
    UNKNOWN = "unknown"


class Region(_StrEnum):
    DOMESTIC = "domestic"
    CROSS_BORDER = "cross_border"


class ResolutionStatus(_StrEnum):
    """输入解析的直接结果。"""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    OUT_OF_SCOPE = "out_of_scope"
    UNVERIFIED = "unverified"
    INVALID = "invalid"
