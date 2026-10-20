"""A regime that forbids a naked put but permits a spread should get the spread.

MEASURED ON THE LIVE BOARD, 20 Oct 2026. 82 names screened, 2 eligible. The
largest refusal after earnings was:

    40x  CASH_SECURED_PUT not permitted in ORANGE regime
         (allowed: BULL_PUT_SPREAD, PROTECTIVE_PUT, COLLAR)

Forty candidates thrown away while the engine's own refusal message named the
structure that WAS permitted. That is the owner's bucket-2 case arriving as a
block instead of a trade.
"""
from __future__ import annotations

from tradepro_strategies.quant_engine.options.bucket import permitted_or_fallback
from tradepro_strategies.quant_engine.options.risk import (
    PERMITTED_BY_REGIME, Regime, Structure,
)


def test_ORANGE_turns_a_cash_secured_put_into_a_spread():
    """The 40-name case."""
    s, why = permitted_or_fallback(
        Structure.CASH_SECURED_PUT, Regime.ORANGE, PERMITTED_BY_REGIME[Regime.ORANGE])
    assert s is Structure.BULL_PUT_SPREAD
    assert "same view" in why


def test_GREEN_leaves_a_permitted_structure_alone():
    s, why = permitted_or_fallback(
        Structure.CASH_SECURED_PUT, Regime.GREEN, PERMITTED_BY_REGIME[Regime.GREEN])
    assert s is Structure.CASH_SECURED_PUT
    assert why == "", "a permitted structure needs no explanation"


def test_YELLOW_leaves_it_alone_too():
    s, _ = permitted_or_fallback(
        Structure.CASH_SECURED_PUT, Regime.YELLOW, PERMITTED_BY_REGIME[Regime.YELLOW])
    assert s is Structure.CASH_SECURED_PUT


def test_RED_refuses_rather_than_flipping_the_view():
    """RED permits BEAR_PUT_SPREAD and COLLAR. Neither says "happy to be long
    here". Falling back to one would invent a trade nobody asked for."""
    s, why = permitted_or_fallback(
        Structure.CASH_SECURED_PUT, Regime.RED, PERMITTED_BY_REGIME[Regime.RED])
    assert s is None
    assert "same view" in why and "BEAR_PUT_SPREAD" in why


def test_a_refused_name_stays_refused():
    s, why = permitted_or_fallback(None, Regime.GREEN, PERMITTED_BY_REGIME[Regime.GREEN])
    assert s is None and why == ""


def test_the_fallback_is_never_a_different_direction():
    """Guard for every regime at once: whatever comes back must be a structure
    the table permits, and must never be a bearish one."""
    for regime, permitted in PERMITTED_BY_REGIME.items():
        s, _ = permitted_or_fallback(Structure.CASH_SECURED_PUT, regime, permitted)
        if s is not None:
            assert s in permitted, regime
            assert s is not Structure.BEAR_PUT_SPREAD, regime
