"""IV solved from the option's own mid, cross-checked against the broker.

The unlock: IBKR's IV field went dark for 47-82 of 82 symbols on 14 Aug while
bid/ask were fine, and every row blocked on "IV-Rank unavailable". IV is a
solve, not a fetch — only the PRICE is irreducibly a market fact.
"""
from __future__ import annotations

from tradepro_strategies.cli.options_screen import solve_iv_and_crosscheck
from tradepro_strategies.quant_engine.options.black_scholes import BlackScholesPricer

P = BlackScholesPricer(risk_free_rate=0.04)


def _mid_for(iv: float, spot=57.5, strike=54.0, dte=39) -> float:
    return P.price(spot, strike, dte / 365.0, iv, "put")


def test_solves_iv_when_broker_field_is_dark():
    mid = _mid_for(0.42)
    r = solve_iv_and_crosscheck(premium=mid, spot=57.5, strike=54.0, dte=39,
                                broker_iv=None, pricer=P)
    assert r["source"] == "solved_only"
    assert abs(r["iv"] - 0.42) < 0.005, r
    assert "SOLVED" in r["detail"]


def test_cross_check_agrees_when_broker_matches():
    mid = _mid_for(0.42)
    r = solve_iv_and_crosscheck(premium=mid, spot=57.5, strike=54.0, dte=39,
                                broker_iv=0.43, pricer=P)
    assert r["source"] == "cross_checked"
    assert r["agreement"] > 0.9


def test_material_disagreement_is_flagged_and_prefers_the_solved_value():
    mid = _mid_for(0.42)
    r = solve_iv_and_crosscheck(premium=mid, spot=57.5, strike=54.0, dte=39,
                                broker_iv=0.90, pricer=P)   # broker way off
    assert r["source"] == "DISAGREEMENT"
    assert abs(r["iv"] - 0.42) < 0.01, "must keep the value derived from the tradeable price"
    assert "DISAGREE" in r["detail"]


def test_no_premium_falls_back_to_broker_but_says_it_is_unverified():
    r = solve_iv_and_crosscheck(premium=None, spot=57.5, strike=54.0, dte=39,
                                broker_iv=0.38, pricer=P)
    assert r["source"] == "broker_only" and r["iv"] == 0.38
    assert "no mid to verify" in r["detail"]


def test_nothing_available_is_unknown_never_guessed():
    r = solve_iv_and_crosscheck(premium=None, spot=57.5, strike=54.0, dte=39,
                                broker_iv=None, pricer=P)
    assert r["source"] == "unavailable" and r["iv"] is None
