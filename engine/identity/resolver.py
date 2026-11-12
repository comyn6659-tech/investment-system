"""标的解析器。

原则（设计书 §5.1、§12）：
1. 不做「六位数字=A股、带字母=美股」式推断；
2. 代码冲突时列出候选，不猜；
3. 用户只说指数时不擅自选一只 ETF；
4. 名称启发式只能用来「排除」，绝不能把未核实对象拉进宽基流程。
"""

from __future__ import annotations

from engine.contracts import reason_codes as rc
from engine.contracts.enums import AssetClass, ResolutionStatus, ScopeStatus
from engine.identity.catalog import IdentityCatalog
from engine.identity.models import InstrumentRef, Resolution
from engine.identity.normalize import NormalizedInput, normalize

_VENUE_ALIASES = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE", "CSI": "CSI"}

# 仅用于「排除」的字符串规则：命中即视为明确的、保守的范围外，
# 绝不会因为命中规则而把对象纳入宽基流程。
_CROSS_BORDER_MARKERS = (
    "纳斯达克", "纳指", "标普", "道琼斯", "恒生", "港股", "香港", "日经", "德国",
    "法国", "欧洲", "亚太", "沙特", "越南", "印度", "QDII", "跨境", "海外", "全球",
)
_COMMODITY_MARKERS = ("黄金", "白银", "原油", "豆粕", "有色金属期货", "商品")
_BOND_MARKERS = ("国债", "政金债", "信用债", "可转债", "短融", "城投债")

# 「创业板」是交易板块，不是指数；必须澄清，不能按名称包含关系猜成创业板指。
_CHINEXT_BOARD = "创业板"
# 其他创业板指数（含创业板50、创业板综、创业板R 等）仍在本批范围外。
_CHINEXT_INDEX_VARIANTS = (
    "创业板50",
    "创业板100",
    "创业板200",
    "创业板300",
    "创业板综",
    "创业板R",
)


def resolve(text: str | None, catalog: IdentityCatalog) -> Resolution:
    n = normalize(text)
    if n.is_empty:
        return Resolution(
            status=ResolutionStatus.INVALID.value,
            raw_input=n.raw,
            reason_codes=(rc.INPUT_EMPTY,),
            clarification_question="请输入标的名称或代码，例如「工商银行」「沪深300」「510300」。",
        )
    if not n.is_plausible_name:
        return Resolution(
            status=ResolutionStatus.INVALID.value,
            raw_input=n.raw,
            reason_codes=(rc.INPUT_INVALID_CHARS,),
            clarification_question="输入包含无法识别的字符，请输入标的名称或 6 位代码。",
        )

    if n.looks_like_code:
        return _resolve_code(n, catalog)
    return _resolve_name(n, catalog)


# --- 代码输入 ---------------------------------------------------------------
def _resolve_code(n: NormalizedInput, catalog: IdentityCatalog) -> Resolution:
    assert n.code is not None
    all_candidates = catalog.by_code(n.code)

    # 1) 前缀与后缀指向不同交易所：不允许静默改写，直接要求澄清
    if n.venue_conflict:
        return Resolution(
            status=ResolutionStatus.AMBIGUOUS.value,
            raw_input=n.raw,
            candidates=tuple(sorted(all_candidates, key=lambda c: c.instrument_id)),
            reason_codes=(rc.IDENTITY_VENUE_SYNTAX_CONFLICT,),
            clarification_question=(
                f"「{n.canonical}」同时给出 {n.prefix_venue} 前缀与 {n.suffix_venue} 后缀，"
                "两者指向不同交易所。请只保留一种写法，或直接给出 6 位代码。"
            ),
        )

    # 2) 显式交易所与已核实标的的交易所不一致：不解绑、不猜测
    if n.venue_hint:
        wanted = _VENUE_ALIASES.get(n.venue_hint, n.venue_hint)
        matching = [c for c in all_candidates if _venue_matches(c, wanted)]
        if not matching:
            venues = sorted({f"{c.venue}({c.instrument_id})" for c in all_candidates})
            return Resolution(
                status=ResolutionStatus.AMBIGUOUS.value,
                raw_input=n.raw,
                candidates=tuple(sorted(all_candidates, key=lambda c: c.instrument_id)),
                reason_codes=(rc.IDENTITY_VENUE_CONFLICT,),
                clarification_question=(
                    f"代码 {n.code} 被指定为「{n.venue_hint}」，但已核实记录中不存在该交易所的标的。"
                    + (f"实际候选：{', '.join(venues)}。" if venues else "该代码未登记。")
                    + "请确认交易所或代码。"
                ),
            )
        candidates = matching
    else:
        candidates = all_candidates

    if len(candidates) == 1:
        return _resolved(candidates[0], n, matched_by="exact_code")
    if len(candidates) > 1:
        if len({c.asset_class for c in candidates}) > 1:
            reason = rc.IDENTITY_MULTI_MARKET
            detail = "同一代码在不同市场/类型下分别对应不同标的"
        else:
            reason = rc.IDENTITY_AMBIGUOUS
            detail = "同一交易所有多只标的共用该代码"
        return Resolution(
            status=ResolutionStatus.AMBIGUOUS.value,
            raw_input=n.raw,
            candidates=tuple(sorted(candidates, key=lambda c: c.instrument_id)),
            reason_codes=(reason,),
            clarification_question=_ambiguity_question(n.canonical, candidates, detail),
        )
    return Resolution(
        status=ResolutionStatus.INVALID.value,
        raw_input=n.raw,
        reason_codes=(rc.INPUT_UNRECOGNIZED,),
        clarification_question=(
            f"代码 {n.code} 不在已核实标的清单中。请确认代码，或先用工具核实该标的身份。"
        ),
    )


def _venue_matches(instrument: InstrumentRef, wanted: str) -> bool:
    """带交易所后缀的代码优先匹配该交易所。

    中证指数（000xxx）在行情口径上按沪市代码发布，因此 .SH 也能指向它们；
    .SZ 不做这种放宽，避免把 000852（石化机械）误当成中证1000。
    """
    if wanted == "CSI":
        return instrument.venue == "CSI"
    if instrument.asset_class == AssetClass.INDEX and wanted == "SSE":
        return instrument.venue in ("SSE", "CSI")
    return instrument.venue == wanted


# --- 名称输入 ---------------------------------------------------------------
def _resolve_name(n: NormalizedInput, catalog: IdentityCatalog) -> Resolution:
    # 1) 单独说「创业板」：先问是不是创业板指，不按板块名称猜指数。
    if n.canonical == _CHINEXT_BOARD:
        candidates = tuple(
            sorted(catalog.by_code("399006"), key=lambda c: c.instrument_id)
        )
        return Resolution(
            status=ResolutionStatus.AMBIGUOUS.value,
            raw_input=n.raw,
            candidates=candidates,
            reason_codes=(rc.IDENTITY_AMBIGUOUS,),
            clarification_question=(
                "「创业板」是一个交易板块，不是指数。你是指创业板指"
                "（399006，INDEX:399006.SZ），还是想查询创业板板块的其他标的？"
                "请给出指数全称或 6 位代码。"
            ),
        )

    # 2) 创业板50、创业板综、创业板R 等其他创业板指数：明确范围外。
    if any(marker in n.canonical for marker in _CHINEXT_INDEX_VARIANTS):
        return Resolution(
            status=ResolutionStatus.OUT_OF_SCOPE.value,
            raw_input=n.raw,
            reason_codes=(rc.OUT_OF_SCOPE_INDEX_VARIANT,),
            clarification_question=None,
        )

    exact = catalog.by_name(n.canonical)
    if len(exact) == 1:
        return _resolved(exact[0], n, matched_by="exact_name")
    if len(exact) > 1:
        return Resolution(
            status=ResolutionStatus.AMBIGUOUS.value,
            raw_input=n.raw,
            candidates=tuple(sorted(exact, key=lambda c: c.instrument_id)),
            reason_codes=(rc.IDENTITY_CODE_NAME_CONFLICT,),
            clarification_question=_ambiguity_question(
                n.canonical, exact, "同一名称对应多个标的"
            ),
        )

    partial = catalog.name_contains(n.canonical)
    if len(partial) == 1 and len(n.canonical) >= 2:
        return _resolved(partial[0], n, matched_by="unique_substring")
    if len(partial) > 1:
        return Resolution(
            status=ResolutionStatus.AMBIGUOUS.value,
            raw_input=n.raw,
            candidates=tuple(partial),
            reason_codes=(rc.IDENTITY_AMBIGUOUS,),
            clarification_question=_ambiguity_question(
                n.canonical,
                partial,
                f"有 {len(partial)} 个标的名包含「{n.canonical}」，请给出完整代码或完整名称",
            ),
        )

    heuristic = _heuristic_out_of_scope(n.canonical)
    if heuristic is not None:
        return heuristic

    return Resolution(
        status=ResolutionStatus.INVALID.value,
        raw_input=n.raw,
        reason_codes=(rc.INPUT_UNRECOGNIZED,),
        clarification_question=(
            f"无法在已核实清单中找到「{n.raw.strip()}」。"
            "请给出 6 位代码，或确认该标的身份后先登记。"
        ),
    )


def _heuristic_out_of_scope(canonical: str) -> Resolution | None:
    """只在明确命中「范围外」标记时返回结论；不用于把对象纳入范围。"""
    if any(m in canonical for m in _CROSS_BORDER_MARKERS):
        code = rc.OUT_OF_SCOPE_CROSS_BORDER
    elif any(m in canonical for m in _COMMODITY_MARKERS):
        code = rc.OUT_OF_SCOPE_BOND_COMMODITY
    elif any(m in canonical for m in _BOND_MARKERS):
        code = rc.OUT_OF_SCOPE_BOND_COMMODITY
    else:
        return None
    return Resolution(
        status=ResolutionStatus.OUT_OF_SCOPE.value,
        raw_input=canonical,
        reason_codes=(code,),
        clarification_question=None,
    )


# --- 组装 -------------------------------------------------------------------
def _resolved(instrument: InstrumentRef, n: NormalizedInput, *, matched_by: str) -> Resolution:
    enriched = instrument
    # 生成一份带原始输入的副本，便于审计（不修改注册表本身）
    import copy

    enriched = copy.copy(instrument)
    enriched.raw_input = n.raw

    scope_status = enriched.scope.status if enriched.scope else ScopeStatus.UNVERIFIED
    if enriched.asset_class == AssetClass.EQUITY and scope_status == ScopeStatus.IN_SCOPE:
        status = ResolutionStatus.RESOLVED.value
    elif scope_status == ScopeStatus.IN_SCOPE:
        status = ResolutionStatus.RESOLVED.value
    elif scope_status == ScopeStatus.OUT_OF_SCOPE:
        status = ResolutionStatus.OUT_OF_SCOPE.value
    else:
        status = ResolutionStatus.UNVERIFIED.value

    return Resolution(
        status=status,
        raw_input=n.raw,
        instrument=enriched,
        reason_codes=(enriched.scope.reason_code,) if enriched.scope else (rc.IDENTITY_NOT_VERIFIED,),
        matched_by=matched_by,
    )


def _ambiguity_question(fragment: str, candidates: list[InstrumentRef] | tuple, detail: str) -> str:
    shown = list(candidates)[:6]
    lines = [f"「{fragment}」{detail}："]
    for inst in shown:
        scope = inst.scope.status.value if inst.scope else "unverified"
        lines.append(f"- {inst.display_name}（{inst.instrument_id}，{scope}）")
    if len(candidates) > len(shown):
        lines.append(f"- ……共 {len(candidates)} 个候选")
    lines.append("请给出完整代码（如 510300）或完整名称。")
    return "\n".join(lines)
