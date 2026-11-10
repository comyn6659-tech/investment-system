"""标的识别测试。

覆盖施工单要求：
- 名称与代码解析一致；
- 歧义输入不会被猜中；
- 只说指数时不擅自选 ETF；
- 非法输入不产生买点。
"""

from __future__ import annotations

import pytest

from engine.contracts.enums import AssetClass, ResolutionStatus, ScopeStatus
from engine.identity.resolver import resolve


def _resolved(text, catalog):
    r = resolve(text, catalog)
    assert r.status == ResolutionStatus.RESOLVED.value, (text, r.status, r.reason_codes)
    return r.instrument


def test_stock_name_and_code_agree(catalog):
    by_name = _resolved("工商银行", catalog)
    by_code = _resolved("601398", catalog)
    assert by_name.instrument_id == by_code.instrument_id == "EQUITY:601398.SH"
    assert by_name.asset_class == AssetClass.EQUITY
    assert by_name.currency == "CNY"


def test_stock_code_with_exchange_prefix_and_suffix(catalog):
    for text in ("SH601398", "601398.SH", "sh601398"):
        assert _resolved(text, catalog).instrument_id == "EQUITY:601398.SH"


def test_fullwidth_digits_are_normalized(catalog):
    assert _resolved("６００５１９", catalog).instrument_id == "EQUITY:600519.SH"


def test_index_names_resolve_to_index_not_etf(catalog):
    for text in ("沪深300", "沪深300指数", "中证500", "中证1000"):
        inst = _resolved(text, catalog)
        assert inst.asset_class == AssetClass.INDEX, text
        assert inst.scope.status == ScopeStatus.IN_SCOPE


def test_index_codes_resolve(catalog):
    assert _resolved("000300", catalog).instrument_id == "INDEX:000300.SH"
    # 000905 / 000852 与深市个股代码真实冲突（厦门港务、石化机械），必须澄清
    for code, index_id in (("000905", "INDEX:000905.SH"), ("000852", "INDEX:000852.SH")):
        bare = resolve(code, catalog)
        assert bare.status == ResolutionStatus.AMBIGUOUS.value
        assert index_id in {c.instrument_id for c in bare.candidates}
        # 带沪市后缀时唯一指向指数
        assert _resolved(f"{code}.SH", catalog).instrument_id == index_id


def test_etf_code_maps_to_verified_underlying(catalog):
    inst = _resolved("510300", catalog)
    assert inst.asset_class == AssetClass.ETF
    assert inst.underlying_index_id == "INDEX:000300.SH"
    underlying = catalog.underlying(inst)
    assert underlying is not None and underlying.asset_class == AssetClass.INDEX


@pytest.mark.parametrize(
    "code,underlying",
    [
        ("159919", "INDEX:000300.SH"),
        ("510500", "INDEX:000905.SH"),
        ("512100", "INDEX:000852.SH"),
        ("159845", "INDEX:000852.SH"),
    ],
)
def test_each_verified_etf_maps_to_correct_index(code, underlying, catalog):
    assert _resolved(code, catalog).underlying_index_id == underlying


def test_code_conflict_is_ambiguous_not_guessed(catalog):
    resolution = resolve("000001", catalog)
    assert resolution.status == ResolutionStatus.AMBIGUOUS.value
    ids = {c.instrument_id for c in resolution.candidates}
    assert ids == {"INDEX:000001.SH", "EQUITY:000001.SZ"}
    assert resolution.clarification_question
    assert resolution.instrument is None


def test_bare_etf_name_is_ambiguous(catalog):
    """「沪深300ETF」对应多只基金，不能擅自挑一只。"""
    resolution = resolve("沪深300ETF", catalog)
    assert resolution.status == ResolutionStatus.AMBIGUOUS.value
    assert resolution.instrument is None
    assert len(resolution.candidates) >= 2


def test_full_etf_name_is_unique(catalog):
    inst = _resolved("沪深300ETF华泰柏瑞", catalog)
    assert inst.instrument_id == "ETF:510300.SH"


def test_alias_matches(catalog):
    assert _resolved("华泰柏瑞沪深300ETF", catalog).instrument_id == "ETF:510300.SH"


def test_empty_input_is_invalid(catalog):
    for text in ("", "   ", None):
        resolution = resolve(text, catalog)
        assert resolution.status == ResolutionStatus.INVALID.value
        assert "INPUT_EMPTY" in resolution.reason_codes


def test_garbage_input_is_invalid(catalog):
    resolution = resolve("!!!@@@", catalog)
    assert resolution.status == ResolutionStatus.INVALID.value
    assert resolution.reason_codes == ("INPUT_INVALID_CHARS",)


def test_unknown_code_is_invalid(catalog):
    resolution = resolve("999999", catalog)
    assert resolution.status == ResolutionStatus.INVALID.value
    assert "INPUT_UNRECOGNIZED" in resolution.reason_codes


def test_unknown_name_is_invalid(catalog):
    resolution = resolve("不存在的公司名", catalog)
    assert resolution.status == ResolutionStatus.INVALID.value


@pytest.mark.parametrize(
    "text,asset,reason",
    [
        ("512880", AssetClass.ETF, "OUT_OF_SCOPE_INDUSTRY_ETF"),
        ("159555", AssetClass.ETF, "OUT_OF_SCOPE_ENHANCED_ETF"),
        ("513100", AssetClass.ETF, "OUT_OF_SCOPE_CROSS_BORDER"),
        ("000015", AssetClass.INDEX, "OUT_OF_SCOPE_STRATEGY_INDEX"),
        ("000922.CSI", AssetClass.INDEX, "OUT_OF_SCOPE_STRATEGY_INDEX"),
    ],
)
def test_out_of_scope_products_are_recognized(text, asset, reason, catalog):
    resolution = resolve(text, catalog)
    assert resolution.status == ResolutionStatus.OUT_OF_SCOPE.value
    assert resolution.instrument is not None
    assert resolution.instrument.asset_class == asset
    assert resolution.instrument.scope.status == ScopeStatus.OUT_OF_SCOPE
    assert resolution.instrument.scope.reason_code == reason


def test_cross_border_name_is_excluded_by_marker(catalog):
    """名称启发式只允许用来排除。"""
    resolution = resolve("纳斯达克100ETF", catalog)
    assert resolution.status == ResolutionStatus.OUT_OF_SCOPE.value


def test_heuristic_never_includes_unverified(catalog):
    """未登记名称不会因为像是宽基就被纳入范围。"""
    resolution = resolve("某某宽基指数", catalog)
    assert resolution.status == ResolutionStatus.INVALID.value


def test_more_broad_indices_recognized_but_not_first_batch(catalog):
    inst = _resolved("000510.SH", catalog)
    assert inst.asset_class == AssetClass.INDEX
    assert inst.in_first_batch is False
