"""数据事实测试：零 / 缺失 / 不适用 / 过期 / 冲突必须分开。"""

from __future__ import annotations

import math

import pytest

from engine.contracts.enums import DataStatus
from engine.data.facts import Fact, FactError
from engine.data.numeric import NumericError, require_finite, sanitize_for_json
from engine.data.snapshot import Snapshot


def test_zero_and_missing_are_different_states():
    zero = Fact.known_zero(fact_id="fin.ocf", metric="operating_cash_flow", unit="CNY", currency="CNY")
    missing = Fact.missing(fact_id="fin.capex", metric="capex")

    assert zero.status == DataStatus.KNOWN_ZERO
    assert zero.value == 0
    assert zero.is_usable_number is True

    assert missing.status == DataStatus.MISSING
    assert missing.value is None
    assert missing.is_usable_number is False

    assert zero.status != missing.status


def test_missing_cannot_carry_value():
    with pytest.raises(FactError):
        Fact(fact_id="x", metric="x", status=DataStatus.MISSING, value=0)


def test_known_requires_value():
    with pytest.raises(FactError):
        Fact.known(fact_id="x", metric="x", value=None)


def test_known_zero_requires_zero_value():
    with pytest.raises(FactError):
        Fact(fact_id="x", metric="x", status=DataStatus.KNOWN_ZERO, value=5)


def test_nan_is_rejected():
    with pytest.raises((FactError, NumericError)):
        Fact.known(fact_id="x", metric="x", value=float("nan"))


def test_infinity_is_rejected_by_numeric_gate():
    with pytest.raises(NumericError):
        require_finite(float("inf"), field="x")
    with pytest.raises(NumericError):
        sanitize_for_json({"a": float("nan")})


def test_conflict_requires_two_values():
    with pytest.raises(FactError):
        Fact.conflict(fact_id="x", metric="x", values=[1])
    fact = Fact.conflict(fact_id="x", metric="x", values=[1, 2])
    assert fact.status == DataStatus.CONFLICT
    assert fact.value is None
    assert fact.is_usable_number is False


def test_stale_is_not_treated_as_fresh():
    stale = Fact.stale(fact_id="p", metric="price", value=1.0, unit="CNY_per_share", currency="CNY", period="2020-01-01")
    assert stale.status == DataStatus.STALE


def test_index_point_must_not_carry_currency():
    with pytest.raises(FactError):
        Fact.known(
            fact_id="index.level",
            metric="level",
            value=4000.0,
            unit="index_point",
            currency="CNY",
        )


def test_snapshot_rejects_duplicate_fact_ids():
    snap = Snapshot(
        instrument_id="INDEX:000300.SH",
        asset_class="index",
        as_of="2026-11-10",
        available_at="2026-11-10T15:00:00+08:00",
        fetched_at="2026-11-11T02:00:00+08:00",
        facts=[
            Fact.known(fact_id="a", metric="a", value=1),
            Fact.known(fact_id="a", metric="a", value=2),
        ],
    )
    assert any("重复" in p for p in snap.validate())


def test_not_applicable_is_not_zero():
    na = Fact.not_applicable(fact_id="fin.net_debt", metric="net_debt")
    assert na.status == DataStatus.NOT_APPLICABLE
    assert na.value is None
    assert not math.isnan(float(na.value or 0))
