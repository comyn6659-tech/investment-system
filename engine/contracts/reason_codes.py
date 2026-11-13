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
OUT_OF_SCOPE_INDEX_VARIANT = _register(
    "OUT_OF_SCOPE_INDEX_VARIANT", "scope", "该指数属于本批未纳入的指数变体"
)
OUT_OF_SCOPE_MARKET = _register("OUT_OF_SCOPE_MARKET", "scope", "港美股等境外市场个股不在第一版范围")
OUT_OF_SCOPE_OTHER = _register("OUT_OF_SCOPE_OTHER", "scope", "标的不在第一版模型范围")
INDEX_NOT_IN_FIRST_BATCH = _register(
    "INDEX_NOT_IN_FIRST_BATCH", "scope", "宽基指数已识别，但不在首批三只样本内；恒生科技等已接入指数按各自数据能力单独处理"
)
INDEX_REMOVED_OFFICIAL_HISTORY_UNAVAILABLE = _register(
    "INDEX_REMOVED_OFFICIAL_HISTORY_UNAVAILABLE",
    "scope",
    "指数已移出研究范围：官方历史估值不可得（2026-11-13 股东决定）",
)
ETF_TRACKING_INDEX_REMOVED = _register(
    "ETF_TRACKING_INDEX_REMOVED", "scope", "ETF 跟踪的指数已移出研究范围，停止产品资料检查"
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
SNAPSHOT_MANIFEST_MISMATCH = _register(
    "SNAPSHOT_MANIFEST_MISMATCH", "data", "manifest 记录的版本信息与实际快照不一致"
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


# --- 阶段二：个股研究估值 ---------------------------------------------------
MODEL_FAMILY_NOT_IMPLEMENTED = _register(
    "MODEL_FAMILY_NOT_IMPLEMENTED",
    "model",
    "该企业族首版未实现估值路线，已给出适用模型类别与本版不能处理的原因",
)
MODEL_NOT_APPLICABLE_TO_BANK = _register(
    "MODEL_NOT_APPLICABLE_TO_BANK", "model", "普通企业现金流/股息口径不适用于银行"
)
MODEL_CROSS_CHECK_ONLY = _register(
    "MODEL_CROSS_CHECK_ONLY", "model", "该模型在本标的上只作交叉检查，不作主模型"
)
MODEL_RESEARCH_VALUATION = _register(
    "MODEL_RESEARCH_VALUATION", "model", "研究估值已计算，但参数与模型未获正式使用批准"
)
RESEARCH_PARAMETERS_UNAPPROVED = _register(
    "RESEARCH_PARAMETERS_UNAPPROVED", "model", "研究参数候选未获设计方批准，不能作为正式买点依据"
)
TERMINAL_VALUE_NOT_APPLIED = _register(
    "TERMINAL_VALUE_NOT_APPLIED", "model", "本次未启用终值，价值只来自明确预测期"
)
MISSING_BUSINESS_EVIDENCE = _register(
    "MISSING_BUSINESS_EVIDENCE", "data", "缺少结构化业务证据，无法判断模型适用性"
)
MISSING_MODEL_INPUT = _register("MISSING_MODEL_INPUT", "data", "模型必要数据缺失或状态不可用")
MISSING_BANK_RISK_EVIDENCE = _register(
    "MISSING_BANK_RISK_EVIDENCE", "data", "银行资本充足率/不良率/拨备覆盖率等证据缺失"
)
BRIDGE_RECONCILIATION_GAP = _register(
    "BRIDGE_RECONCILIATION_GAP", "data", "FCFF 与经营现金流勾稽差额超出容差，存在未解释项"
)
PRICE_UNAVAILABLE_FOR_COMPARISON = _register(
    "PRICE_UNAVAILABLE_FOR_COMPARISON", "data", "缺少可核实的收盘价，不能做现价比较", blocks_candidate=False
)
SHARE_COUNT_DIVERGENCE = _register(
    "SHARE_COUNT_DIVERGENCE", "data", "当前股本与报告期加权股数差异较大，需说明稀释影响", blocks_candidate=False
)
DIVIDEND_FISCAL_YEAR_MISSING = _register(
    "DIVIDEND_FISCAL_YEAR_MISSING", "data", "部分分红记录缺少所属财年，未并入任何财年合计", blocks_candidate=False
)
SPECIAL_DIVIDEND_SEPARATED = _register(
    "SPECIAL_DIVIDEND_SEPARATED", "data", "特别分红单列，未进入可持续 DPS 序列", blocks_candidate=False
)
EXCESS_DISTRIBUTION = _register(
    "EXCESS_DISTRIBUTION", "model", "派息超过当年盈利，需以存量现金或融资支撑", blocks_candidate=False
)
GROWTH_NOT_FUNDED_BY_RETENTION = _register(
    "GROWTH_NOT_FUNDED_BY_RETENTION", "model", "终值增长超过留存收益可支撑的可持续增长"
)
BANK_CAPITAL_CONSTRAINT = _register(
    "BANK_CAPITAL_CONSTRAINT", "model", "资本充足率或分配路径触及资本约束"
)
CROSS_CHECK_DIVERGENCE = _register(
    "CROSS_CHECK_DIVERGENCE", "model", "两条路线的研究估值差异较大，需解释差异来源", blocks_candidate=False
)
LEVERAGE_GUARDRAIL_EXCEEDED = _register(
    "LEVERAGE_GUARDRAIL_EXCEEDED",
    "model",
    "账面有息负债占比超过政策护栏，研究价值仅供参考，不给正式结论",
)
TERMINAL_GUARDRAIL_BY_DEFAULT = _register(
    "TERMINAL_GUARDRAIL_BY_DEFAULT",
    "model",
    "主模型按机制默认落入（*_by_default），不是适用性规则选中：研究价值不用于结论",
)
TERMINAL_GUARDRAIL_EXPLICIT_CASH = _register(
    "TERMINAL_GUARDRAIL_EXPLICIT_CASH",
    "model",
    "基准情景显式期 FCFF 合计非正，终值与显式期脱节：研究价值不用于结论",
)
TERMINAL_GUARDRAIL_TERMINAL_SHARE = _register(
    "TERMINAL_GUARDRAIL_TERMINAL_SHARE",
    "model",
    "基准情景终值占比达到政策上限：研究价值不用于结论",
)
POLICY_GRADE_C = _register(
    "POLICY_GRADE_C",
    "model",
    "确定性等级 C：不满足正式结论条件，只展示研究价值",
)
POLICY_STATUS_PARTIAL = _register(
    "POLICY_STATUS_PARTIAL",
    "data",
    "政策结果部分完成（勾稽、护栏或数据缺口），不形成正式结论",
)
STALE_PRICE = _register(
    "STALE_PRICE",
    "data",
    "价格日期距分析日超过政策允许的交易日数，不得用旧价格给正式结论",
)
STALE_FINANCIALS = _register(
    "STALE_FINANCIALS",
    "data",
    "已有更新的年报但快照未包含，不得用旧财务数据给正式结论",
)
MISSING_POLICY_FIELD = _register(
    "MISSING_POLICY_FIELD",
    "data",
    "快照缺少政策计算所需字段，不得给正式结论",
)
PESSIMISTIC_VALUE_NONPOSITIVE = _register(
    "PESSIMISTIC_VALUE_NONPOSITIVE",
    "model",
    "悲观情景每股价值非正，负价值不可解释：状态 partial、无法判断",
)
BASE_YEAR_ANOMALY = _register(
    "BASE_YEAR_ANOMALY",
    "data",
    "基期营业收入或归母净利润同比超过政策阈值，口径可能断裂：状态 partial、无法判断",
)
TERMINAL_DISTRIBUTION_UNFUNDED = _register(
    "TERMINAL_DISTRIBUTION_UNFUNDED",
    "model",
    "末期分配超过当年盈利且没有持续资金来源证据，不能作为永续终值",
)


# --- 阶段三：指数画像与 ETF 产品资料 -----------------------------------------
INDEX_PROFILE_DESCRIPTIVE_ONLY = _register(
    "INDEX_PROFILE_DESCRIPTIVE_ONLY",
    "model",
    "指数画像只做描述性统计，未形成可交易信号",
    blocks_candidate=False,
)
INDEX_STRATEGY_NOT_IMPLEMENTED = _register(
    "INDEX_STRATEGY_NOT_IMPLEMENTED",
    "model",
    "该指数策略尚未实现（候选 B 与正式策略状态），本版不输出该信号",
)
INDEX_SIGNAL_RESEARCH_ONLY = _register(
    "INDEX_SIGNAL_RESEARCH_ONLY",
    "model",
    "候选 A 只输出研究信号且规则未获批准，不构成买卖结论",
    blocks_candidate=False,
)
INDEX_PROFILE_COVERAGE_INSUFFICIENT = _register(
    "INDEX_PROFILE_COVERAGE_INSUFFICIENT",
    "data",
    "月度参考样本有效月份不足 90%，不给该指标的分位",
)
INDEX_PROFILE_SERIES_MISSING = _register(
    "INDEX_PROFILE_SERIES_MISSING", "data", "缺少同源同列的历史估值序列，无法计算画像"
)
ETF_PRODUCT_FACTS_ONLY = _register(
    "ETF_PRODUCT_FACTS_ONLY",
    "model",
    "ETF 只输出产品资料完整性检查，不构成买卖条件",
    blocks_candidate=False,
)
ETF_TRADING_STATUS_UNKNOWN = _register(
    "ETF_TRADING_STATUS_UNKNOWN",
    "data",
    "未取得停复牌与盘口数据，不能断言当前可交易",
    blocks_candidate=False,
)
ETF_NAV_DISCLOSURE_TIME_UNKNOWN = _register(
    "ETF_NAV_DISCLOSURE_TIME_UNKNOWN",
    "data",
    "净值披露时刻未知，同日折溢价只是事后计算，不能证明收盘前可交易",
    blocks_candidate=False,
)
ETF_SAME_DAY_PREMIUM_UNAVAILABLE = _register(
    "ETF_SAME_DAY_PREMIUM_UNAVAILABLE",
    "data",
    "价格与净值不满足同日同源条件，未计算同日折溢价",
)
