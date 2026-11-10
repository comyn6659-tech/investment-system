"""机器可检查的原因码。

每个原因码都要能被程序判断，不允许只存在于文案里。
`blocks_candidate=True` 表示只要该原因码出现，就不得输出正式 candidate。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReasonCode:
    code: str
    category: str  # identity / scope / model / data / engine
    description: str
    blocks_candidate: bool


_REGISTRY: dict[str, ReasonCode] = {}


def _register(code: str, category: str, description: str, blocks_candidate: bool = True) -> str:
    if code in _REGISTRY:  # pragma: no cover - 开发期保护
        raise RuntimeError(f"原因码重复注册: {code}")
    _REGISTRY[code] = ReasonCode(code, category, description, blocks_candidate)
    return code


# --- 输入与身份 -------------------------------------------------------------
INPUT_EMPTY = _register("INPUT_EMPTY", "identity", "输入为空或只有空白字符")
INPUT_INVALID_CHARS = _register("INPUT_INVALID_CHARS", "identity", "输入包含无法识别的字符")
INPUT_UNRECOGNIZED = _register("INPUT_UNRECOGNIZED", "identity", "输入无法匹配到任何已登记标的")
IDENTITY_AMBIGUOUS = _register("IDENTITY_AMBIGUOUS", "identity", "输入对应多个候选标的，需要澄清")
IDENTITY_MULTI_MARKET = _register("IDENTITY_MULTI_MARKET", "identity", "同一代码在不同市场分别对应不同标的")
IDENTITY_VENUE_CONFLICT = _register(
    "IDENTITY_VENUE_CONFLICT", "identity", "用户给出的交易所与已核实标的的交易所不一致"
)
IDENTITY_VENUE_SYNTAX_CONFLICT = _register(
    "IDENTITY_VENUE_SYNTAX_CONFLICT", "identity", "代码前缀与后缀指向不同交易所"
)
IDENTITY_CODE_NAME_CONFLICT = _register("IDENTITY_CODE_NAME_CONFLICT", "identity", "代码与名称指向不同标的")
IDENTITY_NOT_VERIFIED = _register("IDENTITY_NOT_VERIFIED", "identity", "标的已识别但身份来源未核实")
ETF_TRACKING_TARGET_UNVERIFIED = _register(
    "ETF_TRACKING_TARGET_UNVERIFIED", "identity", "ETF 真实跟踪指数未经产品资料核实"
)

# --- 范围 -------------------------------------------------------------------
OUT_OF_SCOPE_INDUSTRY_ETF = _register("OUT_OF_SCOPE_INDUSTRY_ETF", "scope", "行业主题 ETF 不在第一版范围")
OUT_OF_SCOPE_STRATEGY_INDEX = _register("OUT_OF_SCOPE_STRATEGY_INDEX", "scope", "策略指数不在第一版范围")
OUT_OF_SCOPE_ENHANCED_ETF = _register("OUT_OF_SCOPE_ENHANCED_ETF", "scope", "指数增强 ETF 不在第一版范围")
OUT_OF_SCOPE_ACTIVE_ETF = _register("OUT_OF_SCOPE_ACTIVE_ETF", "scope", "主动 ETF 不在第一版范围")
OUT_OF_SCOPE_CROSS_BORDER = _register("OUT_OF_SCOPE_CROSS_BORDER", "scope", "跨境/QDII 产品不在第一版范围")
OUT_OF_SCOPE_BOND_COMMODITY = _register("OUT_OF_SCOPE_BOND_COMMODITY", "scope", "债券/商品类产品不在第一版范围")
OUT_OF_SCOPE_INDUSTRY_INDEX = _register("OUT_OF_SCOPE_INDUSTRY_INDEX", "scope", "行业指数不在第一版范围")
OUT_OF_SCOPE_MARKET = _register("OUT_OF_SCOPE_MARKET", "scope", "港美股等境外市场个股不在第一版范围")
OUT_OF_SCOPE_OTHER = _register("OUT_OF_SCOPE_OTHER", "scope", "标的不在第一版模型范围")
INDEX_NOT_IN_FIRST_BATCH = _register(
    "INDEX_NOT_IN_FIRST_BATCH", "scope", "宽基指数已识别，但不在首批研究样本内，数据覆盖未核实"
)

# --- 模型 -------------------------------------------------------------------
MODEL_NOT_IMPLEMENTED_EQUITY = _register(
    "MODEL_NOT_IMPLEMENTED_EQUITY", "model", "个股估值模型尚未实现（阶段二）"
)
MODEL_NOT_IMPLEMENTED_INDEX = _register(
    "MODEL_NOT_IMPLEMENTED_INDEX", "model", "指数估值画像与策略尚未实现（阶段三）"
)
MODEL_NOT_IMPLEMENTED_ETF = _register(
    "MODEL_NOT_IMPLEMENTED_ETF", "model", "ETF 交易载体检查尚未实现（阶段三）"
)
MODEL_SELECTION_NOT_IMPLEMENTED = _register(
    "MODEL_SELECTION_NOT_IMPLEMENTED", "model", "模型适用性判断尚未实现（阶段二）"
)
MODEL_RESEARCH_ONLY = _register(
    "MODEL_RESEARCH_ONLY", "model", "模型或策略处于 research 状态，不得输出正式买点"
)
MODEL_PARAMETERS_UNAPPROVED = _register(
    "MODEL_PARAMETERS_UNAPPROVED", "model", "参数尚未经设计方批准，只能做研究测算"
)
MODEL_DEPRECATED = _register("MODEL_DEPRECATED", "model", "模型已废弃，不得输出结论")

# --- 数据 -------------------------------------------------------------------
MISSING_NET_DEBT = _register("MISSING_NET_DEBT", "data", "净负债/有息负债口径数据缺失")
MISSING_CAPEX = _register("MISSING_CAPEX", "data", "资本开支数据缺失")
MISSING_CASH_FLOW = _register("MISSING_CASH_FLOW", "data", "经营现金流数据缺失")
MISSING_SHARE_COUNT = _register("MISSING_SHARE_COUNT", "data", "股本数据缺失")
MISSING_DIVIDEND_SCHEDULE = _register("MISSING_DIVIDEND_SCHEDULE", "data", "分红所属财年或实施状态缺失")
MISSING_PRICE = _register("MISSING_PRICE", "data", "价格数据缺失")
MISSING_VALUATION_HISTORY = _register("MISSING_VALUATION_HISTORY", "data", "历史估值序列覆盖不足")
MISSING_INDEX_LEVEL = _register("MISSING_INDEX_LEVEL", "data", "指数点位数据缺失")
MISSING_ETF_NAV = _register("MISSING_ETF_NAV", "data", "ETF 净值或净值披露时间缺失")
DATA_STALE = _register("DATA_STALE", "data", "数据已过期，不满足分析时点要求")
DATA_CONFLICT = _register("DATA_CONFLICT", "data", "同一事实的多个来源冲突且未解决")
DATA_NOT_APPLICABLE = _register("DATA_NOT_APPLICABLE", "data", "该字段对当前标的不适用")
DATA_AVAILABILITY_UNKNOWN = _register("DATA_AVAILABILITY_UNKNOWN", "data", "数据可获得时间未知")
DATA_SOURCE_UNAVAILABLE = _register("DATA_SOURCE_UNAVAILABLE", "data", "必要数据源当前不可用")
SNAPSHOT_NOT_FOUND = _register("SNAPSHOT_NOT_FOUND", "data", "找不到对应的固定快照")
SNAPSHOT_ID_MISMATCH = _register(
    "SNAPSHOT_ID_MISMATCH", "data", "请求的快照 id 与文件声明的内容 id 不一致"
)
SNAPSHOT_INSTRUMENT_MISMATCH = _register(
    "SNAPSHOT_INSTRUMENT_MISMATCH", "data", "快照所属标的与请求标的不是同一个"
)
SNAPSHOT_ASSET_CLASS_MISMATCH = _register(
    "SNAPSHOT_ASSET_CLASS_MISMATCH", "data", "快照的资产类别与请求标的资产类别不一致"
)
SNAPSHOT_INVALID_STRUCTURE = _register(
    "SNAPSHOT_INVALID_STRUCTURE", "data", "快照结构或取值不合法（重复事实、非法状态或值）"
)
SNAPSHOT_LEGACY_QUARANTINED = _register(
    "SNAPSHOT_LEGACY_QUARANTINED", "data", "快照被标记为历史复现样例，禁止用于分析"
)
SNAPSHOT_HASH_MISMATCH = _register("SNAPSHOT_HASH_MISMATCH", "data", "快照内容与其 snapshot_id 不匹配")

# --- 引擎 -------------------------------------------------------------------
ENGINE_ERROR = _register("ENGINE_ERROR", "engine", "引擎内部错误")
ENGINE_CONTRACT_VIOLATION = _register("ENGINE_CONTRACT_VIOLATION", "engine", "结果契约校验失败")

# --- 研究护栏 ---------------------------------------------------------------
RESEARCH_SIGNAL_ONLY = _register(
    "RESEARCH_SIGNAL_ONLY", "model", "研究信号不能作为正式买点结论", blocks_candidate=False
)


def get(code: str) -> ReasonCode:
    try:
        return _REGISTRY[code]
    except KeyError as exc:  # pragma: no cover - 调用方不应传入未知码
        raise KeyError(f"未登记的原因码: {code}") from exc


def is_registered(code: str) -> bool:
    return code in _REGISTRY


def all_codes() -> list[ReasonCode]:
    return sorted(_REGISTRY.values(), key=lambda r: r.code)


def blocking_codes(codes: list[str]) -> list[str]:
    """返回会阻止 candidate 的原因码。未登记的原因码按阻断处理。"""
    blocked: list[str] = []
    for code in codes:
        rc = _REGISTRY.get(code)
        if rc is None or rc.blocks_candidate:
            blocked.append(code)
    return blocked


def describe(code: str) -> str:
    rc = _REGISTRY.get(code)
    return rc.description if rc else f"未登记的说明码: {code}"
