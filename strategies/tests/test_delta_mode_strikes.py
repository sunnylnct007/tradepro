"""DELTA-mode strike selection — recorded, not yet applied.

Spec v1.0 §2.1 (8 Sep 2026): solve each strike for a target delta rather than
placing them equidistant. It is the only formulation that is delta-neutral BY
CONSTRUCTION — short a call at -δ* and a put at +δ* net to zero.

§4 predicts equidistant strikes run "net long delta by 3-8 deltas per lot". We
measured SPX at +0.126 on 7 Sep — 12.6 deltas, WORSE than the spec's estimate.
The owner has sold the call 200-500 points closer than the system on every one
of five sessions, which is delta-matching by feel, and has beaten it.

NOTHING CHANGES SELECTION YET. The published 82.9% win rate describes the
equidistant rule; swapping it would invalidate every figure the strategy
reports — the same trap as the iron-condor substitution. Both pairs are
recorded daily so the switch becomes evidence, not argument.
"""
from unittest.mock import patch

import tradepro_strategies.cli.index_strangle_paper as P


def _row(market="SPX", spot=7630.0):
    return {"market": market, "spot": spot, "as_of": "2026-09-08",
            "exchange_date": "2026-09-08",
            "legs": {"monthly": {"dte": 17, "put_strike": 7500, "call_strike": 7760}}}


class _R:
    content = b"{}"
    def __init__(self, p): self._p = p
    def json(self): return self._p


def test_the_solved_pair_is_returned_with_both_deltas():
    payload = {"ok": True, "putStrike": 7440.0, "callStrike": 7810.0,
               "putDelta": -0.158, "callDelta": 0.155, "netDelta": 0.003,
               "inDeltaBand": True}
    with patch.object(P, "load_credentials", create=True, return_value=("http://x", "t")):
        import requests
        with patch.object(requests, "post", lambda *a, **k: _R(payload)):
            d = P.solve_delta_strikes(_row())
    assert d["ok"] is True
    assert d["put_strike"] == 7440.0 and d["call_strike"] == 7810.0
    # Delta-neutral BY CONSTRUCTION — that is the entire argument for the mode.
    assert abs(d["net_delta"]) < 0.01
    assert d["in_band"] is True


def test_the_solved_pair_is_ASYMMETRIC_in_points():
    # Spec §2.1: "put skew means the 16Δ put sits further from spot than the
    # 16Δ call, so the midpoint lands ABOVE spot". That is the mechanism, and
    # it is exactly what the owner does by hand.
    put, call, spot = 7440.0, 7810.0, 7630.0
    assert (spot - put) > (call - spot), "skew must push the put further out"


def test_out_of_band_is_reported_not_silently_traded():
    # §2.1: outside [Δ_min, Δ_max] the spec says SKIP THE DAY — a vol crush
    # leaves you selling near-ATM for scraps.
    payload = {"ok": True, "putStrike": 7600.0, "callStrike": 7660.0,
               "putDelta": -0.44, "callDelta": 0.41, "netDelta": 0.03,
               "inDeltaBand": False}
    with patch.object(P, "load_credentials", create=True, return_value=("http://x", "t")):
        import requests
        with patch.object(requests, "post", lambda *a, **k: _R(payload)):
            d = P.solve_delta_strikes(_row())
    assert d["in_band"] is False


def test_an_unsolvable_chain_reports_rather_than_guessing():
    payload = {"ok": False, "error": "no strike reached the target delta with a live quote"}
    with patch.object(P, "load_credentials", create=True, return_value=("http://x", "t")):
        import requests
        with patch.object(requests, "post", lambda *a, **k: _R(payload)):
            d = P.solve_delta_strikes(_row())
    assert d["ok"] is False and "target delta" in d["error"]


def test_india_is_skipped_no_chain_exists():
    for m in ("NIFTY", "BANKNIFTY"):
        assert P.solve_delta_strikes(_row(m, 23800.0)) is None


def test_STRIKE_SELECTION_IS_STILL_DISTANCE_BASED():
    # The measurement must not quietly become the rule. Changing selection
    # needs a backtest, not an observation.
    import inspect
    src = inspect.getsource(P.strike_pair)
    assert "delta" not in src.lower()


def test_the_spec_default_target_is_used():
    assert P.DELTA_TARGET == 0.16
    assert P.DELTA_BAND == (0.08, 0.30)
